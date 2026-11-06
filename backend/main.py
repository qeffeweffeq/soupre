import os
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlmodel import SQLModel, Session

from database.session import engine, get_session, sqlite_url
from database.migrations import _migrate_db, migrate_db
from database.seed_taxonomies import seed_taxonomies
from modules.config import DOWNLOADS_DIR, PROJECT_ROOT
from modules.core.job_logger import (
    job_log_manager,
    JobContextFilter,
    JobLogHandler,
)
from modules.serializers import (
    serialize_job_enriched,
    serialize_page_enriched,
    is_page_untitled_fast,
    _is_page_untitled_fast,
)
from modules.utils.url_utils import (
    normalize_target_url,
    sanitize_url,
    extract_urls_from_text,
)
from modules.utils.page_recovery import (
    reset_job_retryable_pages,
    get_job_dir,
    _get_job_dir,
)
from routers.jobs import _run_job_with_logging
from routers import jobs_router, pages_router, taxonomies_router, notion_router

# Ensure downloads directory exists
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

# Backward-compatible alias
_normalize_target_url = normalize_target_url


@asynccontextmanager
async def lifespan(app: FastAPI):
    SQLModel.metadata.create_all(engine)
    _migrate_db(engine)

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

# Register modular API routers
app.include_router(jobs_router)
app.include_router(pages_router)
app.include_router(taxonomies_router)
app.include_router(notion_router)
