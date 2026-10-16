from fastapi import FastAPI, Depends, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse
from database.models import SQLModel, ScrapeJob, Page
from sqlmodel import create_engine, Session, select
from contextlib import asynccontextmanager
import os
import asyncio
import logging
from collections import defaultdict
from urllib.parse import urlparse
from modules.config import DOWNLOADS_DIR, PROJECT_ROOT

# Create downloads directory if it doesn't exist
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

sqlite_url = "sqlite:////Users/admin/webapps/soupre/soupre.db"
engine = create_engine(sqlite_url, connect_args={"check_same_thread": False})

from sqlalchemy import event


@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=10000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


from modules.core.job_logger import (
    job_log_manager,
    current_job_id,
    JobContextFilter,
    JobLogHandler,
    DONE_SENTINEL,
)


def _migrate_db(engine) -> None:
    """Add any new columns to existing SQLite tables (safe, idempotent)."""
    from sqlalchemy import text
    migrations = [
        # (table_name, column_name, column_definition)
        ("scrapejob", "scrape_mode",   "TEXT NOT NULL DEFAULT 'single'"),
        ("scrapejob", "total_pages",   "INTEGER"),
        ("scrapejob", "scraped_pages", "INTEGER NOT NULL DEFAULT 0"),
        ("scrapejob", "log_path",      "TEXT"),
        ("page",      "url_path",      "TEXT"),
        ("page",      "url_depth",     "INTEGER NOT NULL DEFAULT 0"),
        ("page",      "status",        "TEXT NOT NULL DEFAULT 'completed'"),
        ("page",      "hreflang",      "TEXT"),
        ("page",      "lastmod",       "TEXT"),
        ("page",      "content_type",  "TEXT"),
    ]
    with engine.connect() as conn:
        for table, column, definition in migrations:
            result = conn.execute(text(f"PRAGMA table_info({table})"))
            existing = [row[1] for row in result.fetchall()]
            if column not in existing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {definition}"))
                conn.commit()
@asynccontextmanager
async def lifespan(app: FastAPI):
    SQLModel.metadata.create_all(engine)
    _migrate_db(engine)

    # Initialize job logger event loop
    loop = asyncio.get_running_loop()
    job_log_manager.set_event_loop(loop)

    # Attach context filter and job log handler to root logger
    root_logger = logging.getLogger()
    job_filter = JobContextFilter()
    job_handler = JobLogHandler()
    job_handler.setFormatter(logging.Formatter("%(levelname)s  %(message)s"))
    job_handler.addFilter(job_filter)
    root_logger.addFilter(job_filter)
    root_logger.addHandler(job_handler)

    yield

app = FastAPI(lifespan=lifespan)

app.mount("/downloads", StaticFiles(directory=DOWNLOADS_DIR), name="downloads")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_session():
    with Session(engine) as session:
        yield session


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
    token = current_job_id.set(job_id)
    try:
        from modules.scrapers.engine import run_scrape_job
        await run_scrape_job(
            job_id,
            target_url,
            engine,
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
    finally:
        current_job_id.reset(token)
        await job_log_manager.close_job(job_id)


def _normalize_target_url(url: str) -> str:
    url = url.strip()
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/")
    return f"{scheme}://{netloc}{path}"


@app.post("/api/jobs")
def create_job(
    target_url: str,
    background_tasks: BackgroundTasks,
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
    session: Session = Depends(get_session),
):
    if scrape_mode == "sitemap":
        norm_target = _normalize_target_url(target_url)
        existing_jobs = session.exec(
            select(ScrapeJob).where(ScrapeJob.scrape_mode == "sitemap")
        ).all()
        existing = None
        for j in existing_jobs:
            if _normalize_target_url(j.target_url) == norm_target:
                existing = j
                break

        if existing:
            if existing.status == "running":
                return {"job_id": existing.id, "status": "already_running"}

            # Restore existing job
            failed_pages = session.exec(
                select(Page).where(Page.job_id == existing.id, Page.status == "failed")
            ).all()
            for p in failed_pages:
                p.status = "pending"
                session.add(p)

            existing.status = "running"
            session.add(existing)
            session.commit()
            session.refresh(existing)
            background_tasks.add_task(
                _run_job_with_logging,
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
            return {"job_id": existing.id, "status": "restored"}

    job = ScrapeJob(target_url=target_url, scrape_mode=scrape_mode)
    session.add(job)
    session.commit()
    session.refresh(job)
    background_tasks.add_task(
        _run_job_with_logging,
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


@app.get("/api/jobs/{job_id}")
def get_job(job_id: int, session: Session = Depends(get_session)):
    from fastapi import HTTPException
    job = session.get(ScrapeJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/api/jobs/{job_id}/logs")
async def stream_job_logs(job_id: int, session: Session = Depends(get_session)):
    """SSE endpoint: streams log lines for a job with history replay."""
    job = session.get(ScrapeJob, job_id)
    if not job:
        from fastapi import HTTPException
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


@app.get("/api/jobs/{job_id}/logs/history")
def get_job_log_history(job_id: int, session: Session = Depends(get_session)):
    from fastapi import HTTPException
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


@app.get("/api/jobs")
def get_jobs(session: Session = Depends(get_session)):
    jobs = session.exec(select(ScrapeJob).order_by(ScrapeJob.created_at.desc())).all()
    return jobs


@app.get("/api/jobs/{job_id}/pages")
def get_job_pages(job_id: int, session: Session = Depends(get_session)):
    pages = session.exec(select(Page).where(Page.job_id == job_id)).all()
    return pages


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: int, session: Session = Depends(get_session)):
    job = session.get(ScrapeJob, job_id)
    if not job:
        return {"status": "not found"}
    if job.status == "running":
        job.status = "cancelled"
        session.commit()
    return {"status": "cancelled"}


@app.post("/api/jobs/{job_id}/pause")
def pause_job(job_id: int, session: Session = Depends(get_session)):
    job = session.get(ScrapeJob, job_id)
    if not job:
        return {"status": "not found"}
    if job.status == "running":
        job.status = "paused"
        session.commit()
    return {"status": "paused"}


@app.post("/api/jobs/{job_id}/resume")
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
    from fastapi import HTTPException
    job = session.get(ScrapeJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status == "running":
        raise HTTPException(status_code=400, detail="Job is already running")

    failed_pages = session.exec(
        select(Page).where(Page.job_id == job_id, Page.status == "failed")
    ).all()
    for p in failed_pages:
        p.status = "pending"
        session.add(p)

    job.status = "running"
    session.add(job)
    session.commit()
    session.refresh(job)

    max_pages = max(0, job.total_pages or 0)
    background_tasks.add_task(
        _run_job_with_logging,
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
    return {"status": "resumed"}
@app.delete("/api/jobs/{job_id}")
async def delete_job(
    job_id: int,
    keep_logs: bool = False,
    session: Session = Depends(get_session),
):
    job = session.get(ScrapeJob, job_id)
    if not job:
        return {"status": "not found"}

    import shutil
    import os
    from urllib.parse import urlparse

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
            print(f"Error deleting folder {folder_to_delete}: {e}")

    session.delete(job)
    session.commit()
    job_log_manager.cleanup_job(job_id)
    return {"status": "deleted"}
