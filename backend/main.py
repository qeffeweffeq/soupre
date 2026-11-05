from fastapi import FastAPI, Depends, BackgroundTasks, HTTPException, Request, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse
from typing import Optional, List
from database.models import (
    SQLModel,
    ScrapeJob,
    Page,
    Category,
    Types,
    Tag,
    Genre,
    PageTagLink,
)
from database.seed_taxonomies import slugify
from sqlmodel import create_engine, Session, select
from sqlalchemy import func
from pydantic import BaseModel
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


from modules.utils.url_utils import (
    normalize_target_url,
    sanitize_url,
    extract_urls_from_text,
)


def _migrate_db(engine) -> None:
    """Add any new columns to existing SQLite tables (safe, idempotent) and backfill job title/icon."""
    from sqlalchemy import text
    migrations = [
        # (table_name, column_name, column_definition)
        ("scrapejob", "title",                     "TEXT"),
        ("scrapejob", "icon_url",                  "TEXT"),
        ("scrapejob", "scrape_mode",               "TEXT NOT NULL DEFAULT 'single'"),
        ("scrapejob", "total_pages",               "INTEGER"),
        ("scrapejob", "scraped_pages",             "INTEGER NOT NULL DEFAULT 0"),
        ("scrapejob", "log_path",                  "TEXT"),
        ("page",      "icon_url",                  "TEXT"),
        ("page",      "url_path",                  "TEXT"),
        ("page",      "url_depth",                 "INTEGER NOT NULL DEFAULT 0"),
        ("page",      "status",                    "TEXT NOT NULL DEFAULT 'completed'"),
        ("page",      "hreflang",                  "TEXT"),
        ("page",      "lastmod",                   "TEXT"),
        ("page",      "content_type",              "TEXT"),
        ("page",      "clean_url",                 "TEXT"),
        ("page",      "description",              "TEXT"),
        ("page",      "favicon_path",              "TEXT"),
        ("page",      "category_id",               "INTEGER REFERENCES category(id)"),
        ("page",      "types_id",                  "INTEGER REFERENCES types(id)"),
        ("page",      "genre_id",                  "INTEGER REFERENCES genre(id)"),
        ("page",      "categorization_source",     "TEXT NOT NULL DEFAULT 'dual_hybrid'"),
        ("page",      "categorization_confidence", "REAL NOT NULL DEFAULT 0.0"),
        ("page",      "notion_page_id",            "TEXT"),
        ("page",      "sync_status",               "TEXT NOT NULL DEFAULT 'unmerged'"),
        ("page",      "last_synced_at",            "TIMESTAMP"),
    ]
    with engine.connect() as conn:
        for table, column, definition in migrations:
            result = conn.execute(text(f"PRAGMA table_info({table})"))
            existing = [row[1] for row in result.fetchall()]
            if column not in existing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {definition}"))
                conn.commit()

        # Backfill scrapejob.title and scrapejob.icon_url from completed pages where missing
        jobs = conn.execute(
            text("SELECT id, target_url, title, icon_url FROM scrapejob WHERE title IS NULL OR icon_url IS NULL")
        ).fetchall()
        for job_row in jobs:
            j_id, j_target, j_title, j_icon = job_row
            norm_target = (j_target or "").rstrip("/")
            pages = conn.execute(
                text(
                    "SELECT url, title, icon_url, status FROM page "
                    "WHERE job_id = :jid ORDER BY CASE WHEN status = 'completed' THEN 0 ELSE 1 END, id ASC"
                ),
                {"jid": j_id},
            ).fetchall()

            best_title = j_title
            best_icon = j_icon

            for p_url, p_title, p_icon, _ in pages:
                is_real_title = (
                    p_title
                    and p_title.strip()
                    and p_title.strip() != "Untitled"
                    and not p_title.strip().startswith("/")
                    and not p_title.strip().startswith("http")
                )
                is_root_match = (p_url or "").rstrip("/") == norm_target

                if is_real_title and (not best_title or is_root_match):
                    best_title = p_title.strip()
                if p_icon and (not best_icon or is_root_match):
                    best_icon = p_icon
                if best_title and best_icon and is_root_match:
                    break

            if not best_icon and j_target:
                parsed = urlparse(j_target)
                domain = parsed.hostname or parsed.netloc
                if domain:
                    best_icon = f"https://www.google.com/s2/favicons?domain={domain}&sz=128"

            if best_title != j_title or best_icon != j_icon:
                conn.execute(
                    text("UPDATE scrapejob SET title = :title, icon_url = :icon WHERE id = :jid"),
                    {"title": best_title, "icon": best_icon, "jid": j_id},
                )
        conn.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    SQLModel.metadata.create_all(engine)
    _migrate_db(engine)

    from database.seed_taxonomies import seed_taxonomies
    with Session(engine) as session:
        seed_taxonomies(session)

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
    except Exception as e:
        import logging
        logging.error(f"Job {job_id} encountered unhandled exception: {e}", exc_info=True)
        try:
            with Session(engine) as session:
                job = session.get(ScrapeJob, job_id)
                if job and job.status not in ("cancelled", "paused"):
                    job.status = "failed"
                    session.commit()
        except Exception:
            pass
    finally:
        current_job_id.reset(token)
        await job_log_manager.close_job(job_id)


_normalize_target_url = normalize_target_url


@app.post("/api/jobs/batch-upload")
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
    target_url = sanitize_url(target_url)
    if not target_url:
        raise HTTPException(status_code=400, detail="A valid target URL is required")
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

            existing.target_url = (existing.target_url or "").strip()
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

    job.target_url = (job.target_url or "").strip()
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


# ---------------------------------------------------------------------------
# Taxonomy Pydantic Schemas & CRUD Endpoints
# ---------------------------------------------------------------------------

class CategoryCreate(BaseModel):
    name: str
    slug: Optional[str] = None
    description: Optional[str] = None
    notion_id: Optional[str] = None


class CategoryUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    description: Optional[str] = None
    notion_id: Optional[str] = None


class TypesCreate(BaseModel):
    category_id: int
    name: str
    slug: Optional[str] = None
    description: Optional[str] = None
    notion_id: Optional[str] = None


class TypesUpdate(BaseModel):
    category_id: Optional[int] = None
    name: Optional[str] = None
    slug: Optional[str] = None
    description: Optional[str] = None
    notion_id: Optional[str] = None


class TagCreate(BaseModel):
    name: str
    slug: Optional[str] = None
    notion_id: Optional[str] = None


class TagUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    notion_id: Optional[str] = None


class GenreCreate(BaseModel):
    name: str
    slug: Optional[str] = None


class GenreUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None


@app.get("/api/taxonomies/tree")
def get_taxonomies_tree(session: Session = Depends(get_session)):
    categories = session.exec(select(Category).order_by(Category.name)).all()
    tree = []
    for cat in categories:
        types_list = session.exec(
            select(Types).where(Types.category_id == cat.id).order_by(Types.name)
        ).all()
        types_data = []
        cat_page_count = session.exec(
            select(func.count(Page.id)).where(Page.category_id == cat.id)
        ).one() or 0
        for t in types_list:
            type_page_count = session.exec(
                select(func.count(Page.id)).where(Page.types_id == t.id)
            ).one() or 0
            types_data.append({
                "id": t.id,
                "category_id": t.category_id,
                "name": t.name,
                "slug": t.slug,
                "description": t.description,
                "notion_id": t.notion_id,
                "page_count": type_page_count,
            })
        tree.append({
            "id": cat.id,
            "name": cat.name,
            "slug": cat.slug,
            "description": cat.description,
            "notion_id": cat.notion_id,
            "page_count": cat_page_count,
            "types": types_data,
        })
    return tree


@app.get("/api/taxonomies/categories")
def list_categories(session: Session = Depends(get_session)):
    return session.exec(select(Category).order_by(Category.name)).all()


@app.post("/api/taxonomies/categories")
def create_category(payload: CategoryCreate, session: Session = Depends(get_session)):
    slug = payload.slug or slugify(payload.name)
    existing = session.exec(select(Category).where(Category.slug == slug)).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Category with slug '{slug}' already exists")
    category = Category(
        name=payload.name,
        slug=slug,
        description=payload.description,
        notion_id=payload.notion_id,
    )
    session.add(category)
    session.commit()
    session.refresh(category)
    return category


@app.put("/api/taxonomies/categories/{category_id}")
def update_category(category_id: int, payload: CategoryUpdate, session: Session = Depends(get_session)):
    category = session.get(Category, category_id)
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    if payload.name is not None:
        category.name = payload.name
        if payload.slug is None:
            category.slug = slugify(payload.name)
    if payload.slug is not None:
        category.slug = payload.slug
    if payload.description is not None:
        category.description = payload.description
    if payload.notion_id is not None:
        category.notion_id = payload.notion_id
    session.add(category)
    session.commit()
    session.refresh(category)
    return category


@app.delete("/api/taxonomies/categories/{category_id}")
def delete_category(category_id: int, session: Session = Depends(get_session)):
    category = session.get(Category, category_id)
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    pages = session.exec(select(Page).where(Page.category_id == category_id)).all()
    for p in pages:
        p.category_id = None
        session.add(p)
    types_list = session.exec(select(Types).where(Types.category_id == category_id)).all()
    for t in types_list:
        sub_pages = session.exec(select(Page).where(Page.types_id == t.id)).all()
        for sp in sub_pages:
            sp.types_id = None
            session.add(sp)
        session.delete(t)
    session.delete(category)
    session.commit()
    return {"status": "deleted", "id": category_id}


@app.get("/api/taxonomies/types")
def list_types(category_id: Optional[int] = None, session: Session = Depends(get_session)):
    query = select(Types)
    if category_id is not None:
        query = query.where(Types.category_id == category_id)
    return session.exec(query.order_by(Types.name)).all()


@app.post("/api/taxonomies/types")
def create_types(payload: TypesCreate, session: Session = Depends(get_session)):
    parent_cat = session.get(Category, payload.category_id)
    if not parent_cat:
        raise HTTPException(status_code=404, detail="Parent category not found")
    slug = payload.slug or slugify(payload.name)
    existing = session.exec(select(Types).where(Types.slug == slug)).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Types with slug '{slug}' already exists")
    item_type = Types(
        category_id=payload.category_id,
        name=payload.name,
        slug=slug,
        description=payload.description,
        notion_id=payload.notion_id,
    )
    session.add(item_type)
    session.commit()
    session.refresh(item_type)
    return item_type


@app.put("/api/taxonomies/types/{types_id}")
def update_types(types_id: int, payload: TypesUpdate, session: Session = Depends(get_session)):
    item_type = session.get(Types, types_id)
    if not item_type:
        raise HTTPException(status_code=404, detail="Types not found")
    if payload.category_id is not None:
        parent_cat = session.get(Category, payload.category_id)
        if not parent_cat:
            raise HTTPException(status_code=404, detail="Parent category not found")
        item_type.category_id = payload.category_id
    if payload.name is not None:
        item_type.name = payload.name
        if payload.slug is None:
            item_type.slug = slugify(payload.name)
    if payload.slug is not None:
        item_type.slug = payload.slug
    if payload.description is not None:
        item_type.description = payload.description
    if payload.notion_id is not None:
        item_type.notion_id = payload.notion_id
    session.add(item_type)
    session.commit()
    session.refresh(item_type)
    return item_type


@app.delete("/api/taxonomies/types/{types_id}")
def delete_types(types_id: int, session: Session = Depends(get_session)):
    item_type = session.get(Types, types_id)
    if not item_type:
        raise HTTPException(status_code=404, detail="Types not found")
    pages = session.exec(select(Page).where(Page.types_id == types_id)).all()
    for p in pages:
        p.types_id = None
        session.add(p)
    session.delete(item_type)
    session.commit()
    return {"status": "deleted", "id": types_id}


@app.get("/api/taxonomies/tags")
def list_tags(session: Session = Depends(get_session)):
    return session.exec(select(Tag).order_by(Tag.name)).all()


@app.post("/api/taxonomies/tags")
def create_tag(payload: TagCreate, session: Session = Depends(get_session)):
    slug = payload.slug or slugify(payload.name)
    existing = session.exec(select(Tag).where(Tag.slug == slug)).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Tag with slug '{slug}' already exists")
    tag = Tag(
        name=payload.name,
        slug=slug,
        notion_id=payload.notion_id,
    )
    session.add(tag)
    session.commit()
    session.refresh(tag)
    return tag


@app.put("/api/taxonomies/tags/{tag_id}")
def update_tag(tag_id: int, payload: TagUpdate, session: Session = Depends(get_session)):
    tag = session.get(Tag, tag_id)
    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")
    if payload.name is not None:
        tag.name = payload.name
        if payload.slug is None:
            tag.slug = slugify(payload.name)
    if payload.slug is not None:
        tag.slug = payload.slug
    if payload.notion_id is not None:
        tag.notion_id = payload.notion_id
    session.add(tag)
    session.commit()
    session.refresh(tag)
    return tag


@app.delete("/api/taxonomies/tags/{tag_id}")
def delete_tag(tag_id: int, session: Session = Depends(get_session)):
    tag = session.get(Tag, tag_id)
    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")
    links = session.exec(select(PageTagLink).where(PageTagLink.tag_id == tag_id)).all()
    for link in links:
        session.delete(link)
    session.delete(tag)
    session.commit()
    return {"status": "deleted", "id": tag_id}


@app.get("/api/taxonomies/genres")
def list_genres(session: Session = Depends(get_session)):
    return session.exec(select(Genre).order_by(Genre.name)).all()


@app.post("/api/taxonomies/genres")
def create_genre(payload: GenreCreate, session: Session = Depends(get_session)):
    slug = payload.slug or slugify(payload.name)
    existing = session.exec(select(Genre).where(Genre.slug == slug)).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Genre with slug '{slug}' already exists")
    genre = Genre(
        name=payload.name,
        slug=slug,
    )
    session.add(genre)
    session.commit()
    session.refresh(genre)
    return genre


@app.put("/api/taxonomies/genres/{genre_id}")
def update_genre(genre_id: int, payload: GenreUpdate, session: Session = Depends(get_session)):
    genre = session.get(Genre, genre_id)
    if not genre:
        raise HTTPException(status_code=404, detail="Genre not found")
    if payload.name is not None:
        genre.name = payload.name
        if payload.slug is None:
            genre.slug = slugify(payload.name)
    if payload.slug is not None:
        genre.slug = payload.slug
    session.add(genre)
    session.commit()
    session.refresh(genre)
    return genre


@app.delete("/api/taxonomies/genres/{genre_id}")
def delete_genre(genre_id: int, session: Session = Depends(get_session)):
    genre = session.get(Genre, genre_id)
    if not genre:
        raise HTTPException(status_code=404, detail="Genre not found")
    pages = session.exec(select(Page).where(Page.genre_id == genre_id)).all()
    for p in pages:
        p.genre_id = None
        session.add(p)
    session.delete(genre)
    session.commit()
    return {"status": "deleted", "id": genre_id}
