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

# Create downloads directory if it doesn't exist
os.makedirs(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_downloads"), exist_ok=True)

sqlite_url = "sqlite:////Users/admin/webapps/soupre/soupre.db"
engine = create_engine(sqlite_url, connect_args={"check_same_thread": False})

# ---------------------------------------------------------------------------
# Per-job log queues (job_id -> asyncio.Queue of log line strings)
# ---------------------------------------------------------------------------
job_log_queues: dict[int, asyncio.Queue] = defaultdict(asyncio.Queue)
# Sentinel value to signal the SSE stream that the job is done
_DONE_SENTINEL = "__DONE__"


class JobQueueHandler(logging.Handler):
    """Logging handler that pushes records into the job's async queue."""

    def __init__(self, job_id: int, loop: asyncio.AbstractEventLoop):
        super().__init__()
        self.job_id = job_id
        self.loop = loop

    def emit(self, record: logging.LogRecord):
        msg = self.format(record)
        # Schedule the coroutine-safe put from the background thread
        asyncio.run_coroutine_threadsafe(
            job_log_queues[self.job_id].put(msg), self.loop
        )


def _migrate_db(engine) -> None:
    """Add any new columns to existing SQLite tables (safe, idempotent)."""
    from sqlalchemy import text
    migrations = [
        # (table_name, column_name, column_definition)
        ("scrapejob", "scrape_mode",   "TEXT NOT NULL DEFAULT 'single'"),
        ("scrapejob", "total_pages",   "INTEGER"),
        ("scrapejob", "scraped_pages", "INTEGER NOT NULL DEFAULT 0"),
        ("page",      "url_path",      "TEXT"),
        ("page",      "url_depth",     "INTEGER NOT NULL DEFAULT 0"),
        ("page",      "status",        "TEXT NOT NULL DEFAULT 'completed'"),
    ]
    with engine.connect() as conn:
        for table, column, definition in migrations:
            # Check if column already exists via PRAGMA
            result = conn.execute(
                text(f"PRAGMA table_info({table})")
            )
            existing = [row[1] for row in result.fetchall()]
            if column not in existing:
                conn.execute(text(
                    f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
                ))
                conn.commit()

@asynccontextmanager
async def lifespan(app: FastAPI):
    SQLModel.metadata.create_all(engine)
    _migrate_db(engine)
    yield

app = FastAPI(lifespan=lifespan)

app.mount("/downloads", StaticFiles(directory=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_downloads")), name="downloads")

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
    max_pages: int = 50,
    delay_min: float = 1.5,
    delay_max: float = 4.5,
    large_threshold: int = 20,
    large_pause_sec: int = 120,
):
    """Wrapper that attaches/detaches the JobQueueHandler around the scrape."""
    loop = asyncio.get_event_loop()
    handler = JobQueueHandler(job_id, loop)
    handler.setFormatter(logging.Formatter("%(levelname)s  %(message)s"))
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(handler)

    from modules.scrapers.engine import run_scrape_job
    try:
        await run_scrape_job(job_id, target_url, engine, scrape_mode, max_pages, delay_min, delay_max, large_threshold, large_pause_sec)
    finally:
        root_logger.removeHandler(handler)
        # Signal SSE consumers that this job stream is finished
        await job_log_queues[job_id].put(_DONE_SENTINEL)


@app.post("/api/jobs")
def create_job(
    target_url: str,
    background_tasks: BackgroundTasks,
    scrape_mode: str = "single",
    max_pages: int = 50,
    delay_min: float = 1.5,
    delay_max: float = 4.5,
    large_threshold: int = 20,
    large_pause_sec: int = 120,
    session: Session = Depends(get_session),
):
    job = ScrapeJob(target_url=target_url, scrape_mode=scrape_mode)
    session.add(job)
    session.commit()
    session.refresh(job)

    background_tasks.add_task(
        _run_job_with_logging,
        job.id, target_url,
        scrape_mode, max_pages, delay_min, delay_max,
        large_threshold, large_pause_sec,
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
async def stream_job_logs(job_id: int):
    """SSE endpoint: streams log lines for a running job."""
    async def event_generator():
        queue = job_log_queues[job_id]
        while True:
            try:
                line = await asyncio.wait_for(queue.get(), timeout=30)
            except asyncio.TimeoutError:
                # Send a keep-alive comment so the connection stays open
                yield ": keep-alive\n\n"
                continue

            if line == _DONE_SENTINEL:
                yield f"data: __DONE__\n\n"
                break

            # Escape newlines inside the data payload
            safe = line.replace("\n", " ").replace("\r", "")
            yield f"data: {safe}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


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


@app.post("/api/jobs/{job_id}/resume")
def resume_job(job_id: int, background_tasks: BackgroundTasks, session: Session = Depends(get_session)):
    from fastapi import HTTPException
    job = session.get(ScrapeJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status == "running":
        raise HTTPException(status_code=400, detail="Job is already running")
    
    job.status = "running"
    session.commit()
    
    background_tasks.add_task(
        _run_job_with_logging,
        job.id, job.target_url,
        job.scrape_mode
    )
    return {"status": "resumed"}

@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: int, session: Session = Depends(get_session)):
    job = session.get(ScrapeJob, job_id)
    if not job:
        return {"status": "not found"}

    import shutil
    import os
    from urllib.parse import urlparse

    # 1. Close SSE stream if active
    queue = job_log_queues.get(job_id)
    if queue:
        await queue.put(_DONE_SENTINEL)
    job_log_queues.pop(job_id, None)

    # 2. Determine folder to delete (Sitemap jobs use a predictable folder name)
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    domain = urlparse(job.target_url).netloc.replace(".", "_")
    folder_to_delete = os.path.join(base_dir, "_downloads", f"job_{job_id}_{domain}")

    # Fallback to checking page paths for single page scrapes
    pages = session.exec(select(Page).where(Page.job_id == job_id)).all()
    for page in pages:
        if not os.path.exists(folder_to_delete):
            path = page.markdown_path or page.screenshot_path
            if path and "../_downloads/" in path:
                parts = path.split("/")
                if len(parts) >= 3:
                    folder_name = parts[2]
                    folder_to_delete = os.path.join(base_dir, "_downloads", folder_name)
        session.delete(page)

    if os.path.exists(folder_to_delete):
        try:
            shutil.rmtree(folder_to_delete)
        except Exception as e:
            print(f"Error deleting folder {folder_to_delete}: {e}")

    session.delete(job)
    session.commit()
    return {"status": "deleted"}
