import os
import shutil
import asyncio
import logging
from typing import Optional, List
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select

from database.models import ScrapeJob, Page, Category, Types, Workspace
from database.session import get_session, engine as default_engine
from schemas.jobs import JobTaxonomyUpdate
from modules.serializers import serialize_job_enriched, serialize_page_enriched
from modules.config import DOWNLOADS_DIR, PROJECT_ROOT
from modules.core.job_logger import (
    job_log_manager,
    current_job_id,
    DONE_SENTINEL,
)
from modules.utils.url_utils import (
    normalize_target_url,
    sanitize_url,
    extract_urls_from_text,
)
from modules.utils.page_recovery import (
    get_job_dir,
    reset_job_retryable_pages,
)

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _get_engine():
    """Retrieve active engine, respecting any test fixture override on main.engine."""
    try:
        import main
        return getattr(main, "engine", default_engine)
    except Exception:
        return default_engine


async def _run_job_with_logging(
    job_id: int,
    target_url: str,
    scrape_mode: str = "single",
    max_pages: int = 0,
    delay_min: float = 60.0,
    delay_max: float = 120.0,
    session_min: int = 30,
    session_max: int = 50,
    cooldown_min: float = 180.0,
    cooldown_max: float = 300.0,
    large_threshold: int = 20,
    large_pause_sec: int = 120,
):
    """Wrapper that tracks job_id in contextvars and closes job log streams upon exit."""
    active_engine = _get_engine()
    token = current_job_id.set(job_id)
    try:
        from modules.scrapers.engine import run_scrape_job
        await run_scrape_job(
            job_id,
            target_url,
            active_engine,
            scrape_mode,
            max_pages,
            delay_min,
            delay_max,
            session_min,
            session_max,
            cooldown_min,
            cooldown_max,
            large_threshold,
            large_pause_sec,
        )
    except Exception as e:
        logging.error(f"Job {job_id} encountered unhandled exception: {e}", exc_info=True)
        try:
            with Session(active_engine) as session:
                job = session.get(ScrapeJob, job_id)
                if job and job.status not in ("cancelled", "paused"):
                    job.status = "failed"
                    session.commit()
        except Exception:
            pass
    finally:
        current_job_id.reset(token)
        await job_log_manager.close_job(job_id)


def _dispatch_run_job(*args, **kwargs):
    import main
    runner = getattr(main, "_run_job_with_logging", _run_job_with_logging)
    return runner(*args, **kwargs)


@router.post("/batch-upload")
async def batch_upload_urls(
    request: Request,
    file: Optional[UploadFile] = File(None),
    raw_text: Optional[str] = Form(None),
):
    """
    Extract and sanitize URLs from an uploaded file (.txt, .csv, .md, .html)
    or a raw text form/JSON payload.
    """
    content = ""
    if file is not None:
        file_bytes = await file.read()
        content = file_bytes.decode("utf-8", errors="ignore")
    elif raw_text:
        content = raw_text
    else:
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                body_json = await request.json()
                if isinstance(body_json, dict):
                    content = str(body_json.get("raw_text") or "")
                elif isinstance(body_json, str):
                    content = body_json
            except Exception:
                pass
        if not content:
            try:
                body_bytes = await request.body()
                if body_bytes:
                    content = body_bytes.decode("utf-8", errors="ignore")
            except Exception:
                pass

    urls = extract_urls_from_text(content)
    return {
        "count": len(urls),
        "urls": urls,
    }


@router.post("")
def create_job(
    target_url: str,
    background_tasks: BackgroundTasks,
    scrape_mode: str = "single",
    workspace_id: Optional[int] = None,
    max_pages: int = 0,
    delay_min: float = 60.0,
    delay_max: float = 120.0,
    session_min: int = 30,
    session_max: int = 50,
    cooldown_min: float = 180.0,
    cooldown_max: float = 300.0,
    large_threshold: int = 20,
    large_pause_sec: int = 120,
    session: Session = Depends(get_session),
):
    target_url = sanitize_url(target_url)

    normalized_input = normalize_target_url(target_url)
    all_jobs = session.exec(select(ScrapeJob)).all()
    for existing in all_jobs:
        if normalize_target_url(existing.target_url) == normalized_input:
            if existing.status == "running":
                return {"job_id": existing.id, "status": "already_running"}

            # Restore existing job (resetting both failed and Untitled/empty pages)
            reset_count = reset_job_retryable_pages(session, existing)

            existing.target_url = (existing.target_url or "").strip()
            existing.status = "running"
            if workspace_id is not None:
                existing.workspace_id = workspace_id
            session.add(existing)
            session.commit()
            session.refresh(existing)

            background_tasks.add_task(
                _dispatch_run_job,
                existing.id,
                existing.target_url,
                scrape_mode,
                max_pages,
                delay_min,
                delay_max,
                session_min,
                session_max,
                cooldown_min,
                cooldown_max,
                large_threshold,
                large_pause_sec,
            )
            return {"job_id": existing.id, "status": "restored", "queued_pages": reset_count}

    job = ScrapeJob(target_url=target_url, scrape_mode=scrape_mode, workspace_id=workspace_id)
    session.add(job)
    session.commit()
    session.refresh(job)

    background_tasks.add_task(
        _dispatch_run_job,
        job.id,
        target_url,
        scrape_mode,
        max_pages,
        delay_min,
        delay_max,
        session_min,
        session_max,
        cooldown_min,
        cooldown_max,
        large_threshold,
        large_pause_sec,
    )
    return {"job_id": job.id, "status": "started"}


@router.get("")
def get_jobs(
    workspace_id: Optional[str] = None,
    session: Session = Depends(get_session)
):
    query = select(ScrapeJob)
    if workspace_id and workspace_id not in ("all", "__all__"):
        if workspace_id.isdigit():
            query = query.where(ScrapeJob.workspace_id == int(workspace_id))
        else:
            ws = session.exec(select(Workspace).where(Workspace.slug == workspace_id)).first()
            if ws:
                query = query.where(ScrapeJob.workspace_id == ws.id)
    jobs = session.exec(query.order_by(ScrapeJob.created_at.desc())).all()
    return [serialize_job_enriched(j) for j in jobs]


@router.get("/{job_id}")
def get_job(job_id: int, session: Session = Depends(get_session)):
    job = session.get(ScrapeJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return serialize_job_enriched(job)


@router.get("/{job_id}/logs")
async def stream_job_logs(job_id: int, session: Session = Depends(get_session)):
    """SSE endpoint: streams log lines for a job with history replay."""
    job = session.get(ScrapeJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    is_active = job.status in ("running", "paused")
    fallback_path = os.path.join(PROJECT_ROOT, job.log_path) if job.log_path else None

    async def event_generator():
        history, queue = job_log_manager.subscribe(job_id, fallback_path=fallback_path)
        try:
            # 1. Replay past logs
            for line in history:
                safe = line.replace("\n", " ").replace("\r", "")
                yield f"data: {safe}\n\n"

            # 2. If job is not active, finish immediately
            if not is_active:
                yield f"data: {DONE_SENTINEL}\n\n"
                return

            # 3. Stream live logs
            while True:
                try:
                    line = await asyncio.wait_for(queue.get(), timeout=25)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue

                if line == DONE_SENTINEL:
                    yield f"data: {DONE_SENTINEL}\n\n"
                    break

                safe = line.replace("\n", " ").replace("\r", "")
                yield f"data: {safe}\n\n"
        finally:
            job_log_manager.unsubscribe(job_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{job_id}/logs/history")
def get_job_log_history(job_id: int, session: Session = Depends(get_session)):
    job = session.get(ScrapeJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    fallback_path = os.path.join(PROJECT_ROOT, job.log_path) if job.log_path else None
    history = job_log_manager.get_history(job_id, fallback_path=fallback_path)
    return {
        "job_id": job_id,
        "status": job.status,
        "log_path": job.log_path,
        "logs": history,
    }


@router.get("/{job_id}/pages")
def get_job_pages(job_id: int, session: Session = Depends(get_session)):
    job = session.get(ScrapeJob, job_id)
    pages = session.exec(select(Page).where(Page.job_id == job_id)).all()
    return [serialize_page_enriched(p, job) for p in pages]


@router.post("/{job_id}/cancel")
def cancel_job(job_id: int, session: Session = Depends(get_session)):
    job = session.get(ScrapeJob, job_id)
    if not job:
        return {"status": "not found"}
    if job.status == "running":
        job.status = "cancelled"
        session.commit()
    return {"status": "cancelled"}


@router.post("/{job_id}/pause")
def pause_job(job_id: int, session: Session = Depends(get_session)):
    job = session.get(ScrapeJob, job_id)
    if not job:
        return {"status": "not found"}
    if job.status == "running":
        job.status = "paused"
        session.commit()
    return {"status": "paused"}


@router.post("/{job_id}/resume")
def resume_job(
    job_id: int,
    background_tasks: BackgroundTasks,
    session_min: int = 30,
    session_max: int = 50,
    cooldown_min: float = 180.0,
    cooldown_max: float = 300.0,
    delay_min: float = 60.0,
    delay_max: float = 120.0,
    session: Session = Depends(get_session),
):
    job = session.get(ScrapeJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status == "running":
        raise HTTPException(status_code=400, detail="Job is already running")

    reset_count = reset_job_retryable_pages(session, job)

    job.target_url = (job.target_url or "").strip()
    job.status = "running"
    session.add(job)
    session.commit()
    session.refresh(job)

    max_pages = max(0, job.total_pages or 0)
    background_tasks.add_task(
        _dispatch_run_job,
        job.id,
        job.target_url,
        job.scrape_mode,
        max_pages,
        delay_min,
        delay_max,
        session_min,
        session_max,
        cooldown_min,
        cooldown_max,
    )
    return {"status": "resumed", "queued_pages": reset_count}


@router.post("/{job_id}/rescrape-untitled")
def rescrape_untitled_job(
    job_id: int,
    background_tasks: BackgroundTasks,
    session_min: int = 30,
    session_max: int = 50,
    cooldown_min: float = 180.0,
    cooldown_max: float = 300.0,
    delay_min: float = 60.0,
    delay_max: float = 120.0,
    session: Session = Depends(get_session),
):
    job = session.get(ScrapeJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status == "running":
        raise HTTPException(status_code=400, detail="Job is already running")

    reset_count = reset_job_retryable_pages(session, job)
    pending_count = len(
        session.exec(select(Page).where(Page.job_id == job_id, Page.status == "pending")).all()
    )
    if reset_count == 0 and pending_count == 0 and job.scrape_mode == "sitemap":
        return {"status": "nothing_to_rescrape", "job_id": job.id, "queued_pages": 0}

    job.target_url = (job.target_url or "").strip()
    job.status = "running"
    session.add(job)
    session.commit()
    session.refresh(job)

    max_pages = max(0, job.total_pages or 0)
    background_tasks.add_task(
        _dispatch_run_job,
        job.id,
        job.target_url,
        job.scrape_mode,
        max_pages,
        delay_min,
        delay_max,
        session_min,
        session_max,
        cooldown_min,
        cooldown_max,
    )
    return {"status": "resumed", "job_id": job.id, "queued_pages": reset_count}


@router.delete("/{job_id}")
async def delete_job(
    job_id: int,
    keep_logs: bool = False,
    session: Session = Depends(get_session),
):
    job = session.get(ScrapeJob, job_id)
    if not job:
        return {"status": "not found"}

    # 1. Determine folder to delete
    domain = urlparse(job.target_url).netloc.replace(".", "_")
    folder_to_delete = os.path.join(DOWNLOADS_DIR, f"job_{job_id}_{domain}")
    if job.log_path:
        rel_dir = os.path.dirname(job.log_path).lstrip("/").lstrip("./")
        if rel_dir.startswith("_downloads/"):
            rel_dir = rel_dir.replace("_downloads/", "", 1)
        folder_to_delete = os.path.join(DOWNLOADS_DIR, rel_dir)

    fallback_path = os.path.join(PROJECT_ROOT, job.log_path) if job.log_path else None

    # 2. Preserve logs if requested
    if keep_logs:
        archive_dir = os.path.join(DOWNLOADS_DIR, "_logs")
        job_log_manager.preserve_log_file(job_id, archive_dir, fallback_path=fallback_path)

    # 3. Clean up database pages and files
    pages = session.exec(select(Page).where(Page.job_id == job_id)).all()
    for page in pages:
        if not os.path.exists(folder_to_delete):
            path = page.markdown_path or page.screenshot_path
            if path and "_downloads/" in path:
                parts = path.split("/")
                if "_downloads" in parts:
                    idx = parts.index("_downloads")
                    if idx + 1 < len(parts):
                        folder_name = parts[idx + 1]
                        folder_to_delete = os.path.join(DOWNLOADS_DIR, folder_name)
        session.delete(page)

    if os.path.exists(folder_to_delete):
        try:
            shutil.rmtree(folder_to_delete)
        except Exception as e:
            logging.error(f"Error deleting folder {folder_to_delete}: {e}")

    session.delete(job)
    session.commit()
    return {"status": "deleted"}


@router.patch("/{job_id}/taxonomy")
@router.put("/{job_id}/taxonomy")
def update_job_taxonomy(
    job_id: int,
    payload: JobTaxonomyUpdate,
    session: Session = Depends(get_session)
):
    job = session.get(ScrapeJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    fields_set = payload.model_fields_set

    if "workspace_id" in fields_set:
        if payload.workspace_id is not None:
            ws = session.get(Workspace, payload.workspace_id)
            if not ws:
                raise HTTPException(status_code=404, detail="Workspace not found")
        job.workspace_id = payload.workspace_id

    if "category_id" in fields_set:
        if payload.category_id is not None:
            cat = session.get(Category, payload.category_id)
            if not cat:
                raise HTTPException(status_code=404, detail="Category not found")
            if "workspace_id" not in fields_set and cat.workspace_id:
                job.workspace_id = cat.workspace_id
        job.category_id = payload.category_id

    if "types_id" in fields_set:
        if payload.types_id is not None:
            t = session.get(Types, payload.types_id)
            if not t:
                raise HTTPException(status_code=404, detail="Types not found")
            if "category_id" not in fields_set and t.category_id:
                job.category_id = t.category_id
        job.types_id = payload.types_id

    # If category changed and types_id was not explicitly specified, verify if existing types_id matches category
    if "category_id" in fields_set and "types_id" not in fields_set:
        if job.types_id is not None:
            existing_type = session.get(Types, job.types_id)
            if existing_type and existing_type.category_id != job.category_id:
                job.types_id = None

    session.add(job)

    # Bulk update all pages belonging to this job
    pages = session.exec(select(Page).where(Page.job_id == job_id)).all()
    for page in pages:
        if "workspace_id" in fields_set or ("category_id" in fields_set and job.workspace_id is not None):
            page.workspace_id = job.workspace_id

        if "category_id" in fields_set:
            page.category_id = job.category_id
            page.categorization_source = "user_manual"
            page.categorization_confidence = 1.0
            if job.types_id is None:
                page.types_id = None

        if "types_id" in fields_set:
            page.types_id = job.types_id
            page.categorization_source = "user_manual"
            page.categorization_confidence = 1.0
            if job.category_id is not None:
                page.category_id = job.category_id

        session.add(page)

    session.commit()
    session.refresh(job)
    for page in pages:
        session.refresh(page)

    return {
        "job": serialize_job_enriched(job),
        "pages": [serialize_page_enriched(p, job) for p in pages],
        "updated_pages_count": len(pages),
    }
