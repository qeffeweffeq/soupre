import asyncio
import os
import shutil
import sys
import pytest
from sqlalchemy import event
from sqlmodel import SQLModel, Session, create_engine, select

# Ensure backend directory is in python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from database.models import ScrapeJob, Page
from modules.core.job_logger import (
    JobLogManager,
    current_job_id,
    JobContextFilter,
    JobLogHandler,
    DONE_SENTINEL,
)


@pytest.mark.asyncio
async def test_job_context_isolation():
    """Verify that concurrent async tasks logging with different current_job_id do not cross-contaminate."""
    manager = JobLogManager()
    loop = asyncio.get_running_loop()
    manager.set_event_loop(loop)

    import logging

    logger = logging.getLogger("test_isolation")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    handler = JobLogHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    # Route records for this test's manager
    handler.emit = lambda record: manager.log(record.job_id, handler.format(record)) if getattr(record, "job_id", None) is not None else None
    job_filter = JobContextFilter()
    handler.addFilter(job_filter)
    logger.addHandler(handler)

    async def worker(job_id: int, count: int):
        token = current_job_id.set(job_id)
        try:
            for i in range(count):
                logger.info(f"job-{job_id}-msg-{i}")
                await asyncio.sleep(0.005)
        finally:
            current_job_id.reset(token)

    # Run three concurrent jobs
    await asyncio.gather(worker(101, 15), worker(102, 15), worker(103, 15))

    history_101 = manager.get_history(101)
    history_102 = manager.get_history(102)
    history_103 = manager.get_history(103)

    assert len(history_101) == 15
    assert len(history_102) == 15
    assert len(history_103) == 15

    assert all("job-101" in msg for msg in history_101)
    assert not any("job-102" in msg or "job-103" in msg for msg in history_101)

    assert all("job-102" in msg for msg in history_102)
    assert not any("job-101" in msg or "job-103" in msg for msg in history_102)

    assert all("job-103" in msg for msg in history_103)
    assert not any("job-101" in msg or "job-102" in msg for msg in history_103)


@pytest.mark.asyncio
async def test_parallel_job_logging_files(tmp_path):
    """Verify each concurrent job creates its own job.log containing strictly its own output."""
    manager = JobLogManager()
    loop = asyncio.get_running_loop()
    manager.set_event_loop(loop)

    log_file_1 = str(tmp_path / "job_1_domain" / "job.log")
    log_file_2 = str(tmp_path / "job_2_domain" / "job.log")

    manager.register_job_log_file(1, log_file_1)
    manager.register_job_log_file(2, log_file_2)

    async def log_job(job_id: int):
        token = current_job_id.set(job_id)
        try:
            for i in range(10):
                manager.log(job_id, f"Job {job_id} event {i}")
                await asyncio.sleep(0.002)
        finally:
            current_job_id.reset(token)

    await asyncio.gather(log_job(1), log_job(2))

    assert os.path.exists(log_file_1)
    assert os.path.exists(log_file_2)

    with open(log_file_1, "r", encoding="utf-8") as f:
        lines1 = [line.strip() for line in f.readlines()]
    with open(log_file_2, "r", encoding="utf-8") as f:
        lines2 = [line.strip() for line in f.readlines()]

    assert len(lines1) == 10
    assert len(lines2) == 10
    assert all("Job 1" in l for l in lines1)
    assert not any("Job 2" in l for l in lines1)
    assert all("Job 2" in l for l in lines2)
    assert not any("Job 1" in l for l in lines2)


@pytest.mark.asyncio
async def test_sse_history_replay():
    """Verify connecting after logs have emitted replays past lines before live stream."""
    manager = JobLogManager()
    loop = asyncio.get_running_loop()
    manager.set_event_loop(loop)

    job_id = 42
    # Pre-populate history
    for i in range(5):
        manager.log(job_id, f"history-line-{i}")

    # Late subscriber connects
    history, q = manager.subscribe(job_id)
    assert len(history) == 5
    assert history == [f"history-line-{i}" for i in range(5)]

    # New live logs arrive
    manager.log(job_id, "live-line-1")
    manager.log(job_id, "live-line-2")

    msg1 = await asyncio.wait_for(q.get(), timeout=1.0)
    msg2 = await asyncio.wait_for(q.get(), timeout=1.0)
    assert msg1 == "live-line-1"
    assert msg2 == "live-line-2"

    # Close job -> receives sentinel
    await manager.close_job(job_id)
    done_msg = await asyncio.wait_for(q.get(), timeout=1.0)
    assert done_msg == DONE_SENTINEL

    manager.cleanup_job(job_id)


@pytest.mark.asyncio
async def test_sqlite_concurrent_writes(tmp_path):
    """Verify SQLite WAL mode handles concurrent write operations without database locking."""
    db_file = str(tmp_path / "wal_concurrency.db")
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    SQLModel.metadata.create_all(engine)

    async def worker(worker_id: int):
        for i in range(8):
            with Session(engine) as session:
                job = ScrapeJob(
                    target_url=f"https://worker{worker_id}.com",
                    status="running",
                    scrape_mode="sitemap",
                    log_path=f"_downloads/job_{worker_id}/job.log",
                )
                session.add(job)
                session.commit()
                session.refresh(job)

                page = Page(
                    job_id=job.id,
                    url=f"https://worker{worker_id}.com/page-{i}",
                    title=f"Page {i}",
                    status="completed",
                )
                session.add(page)
                session.commit()
            await asyncio.sleep(0.002)

    # 6 workers writing concurrently
    await asyncio.gather(*(worker(w) for w in range(6)))

    with Session(engine) as session:
        jobs = session.exec(select(ScrapeJob)).all()
        pages = session.exec(select(Page)).all()
        assert len(jobs) == 48
        assert len(pages) == 48


@pytest.mark.asyncio
async def test_job_delete_optional_log_retention(tmp_path):
    """Verify keep_logs=True archives job.log to _downloads/_logs/ while deleting folder."""
    manager = JobLogManager()
    loop = asyncio.get_running_loop()
    manager.set_event_loop(loop)

    downloads_dir = tmp_path / "_downloads"
    archive_dir = str(downloads_dir / "_logs")
    job_folder = downloads_dir / "job_88_test_site"
    os.makedirs(job_folder, exist_ok=True)
    log_file = job_folder / "job.log"
    with open(log_file, "w", encoding="utf-8") as f:
        f.write("Scrape started\nPage 1 scraped\nJob completed\n")

    manager.register_job_log_file(88, str(log_file))

    # Preserve log
    dest = manager.preserve_log_file(88, archive_dir)
    assert dest is not None
    assert os.path.exists(dest)
    assert os.path.basename(dest) == "job_88_test_site.log"

    # Now remove the job folder (assets, media, etc.)
    shutil.rmtree(job_folder)
    assert not os.path.exists(job_folder)

    # The archived log file persists intact
    assert os.path.exists(dest)
    with open(dest, "r", encoding="utf-8") as f:
        archived_content = f.read()
    assert "Page 1 scraped" in archived_content

    # Cleanup memory
    manager.cleanup_job(88)
    assert manager.get_history(88) == []
