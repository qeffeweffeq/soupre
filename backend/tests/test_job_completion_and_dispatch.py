import inspect
import pytest
from unittest.mock import AsyncMock, patch
from starlette.background import BackgroundTask
from sqlmodel import Session, create_engine, SQLModel
from sqlalchemy.pool import StaticPool

from database.models import ScrapeJob, Page
from routers.jobs import _dispatch_run_job, _run_job_with_logging
from modules.utils.page_recovery import reconcile_job_statuses


def test_dispatch_run_job_is_coroutine_function():
    """Verify _dispatch_run_job is a coroutine function so Starlette background_tasks awaits it."""
    assert inspect.iscoroutinefunction(_dispatch_run_job), "_dispatch_run_job must be async def"


@pytest.mark.asyncio
async def test_starlette_background_task_awaits_dispatch_run_job(recwarn):
    """Verify that Starlette BackgroundTask executes _dispatch_run_job without unawaited coroutine warnings."""
    mock_runner = AsyncMock(return_value=None)
    with patch("main._run_job_with_logging", mock_runner, create=True), patch("routers.jobs._run_job_with_logging", mock_runner):
        task = BackgroundTask(_dispatch_run_job, 999, "https://example.com")
        await task()
        mock_runner.assert_awaited_once_with(999, "https://example.com")

    # Assert no RuntimeWarning about unawaited coroutines
    unawaited = [w for w in recwarn if issubclass(w.category, RuntimeWarning) and "was never awaited" in str(w.message)]
    assert len(unawaited) == 0


def test_reconcile_job_statuses():
    """Verify reconcile_job_statuses auto-reconciles jobs with completed or failed pages."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        # Job 1: Has 1 completed page, 0 pending, status running -> should become completed
        j1 = ScrapeJob(target_url="https://example.com/1", status="running")
        session.add(j1)
        session.commit()
        session.refresh(j1)

        p1 = Page(job_id=j1.id, url="https://example.com/1", title="Page 1", status="completed")
        session.add(p1)

        # Job 2: Has 0 pages (failed immediately), status running -> should become failed
        j2 = ScrapeJob(target_url="https://example.com/404", status="running")
        session.add(j2)

        # Job 3: Has 1 completed, 1 pending page -> should remain running
        j3 = ScrapeJob(target_url="https://example.com/multi", status="running")
        session.add(j3)
        session.commit()
        session.refresh(j3)

        p3a = Page(job_id=j3.id, url="https://example.com/p1", title="Page 3A", status="completed")
        p3b = Page(job_id=j3.id, url="https://example.com/p2", title="Page 3B", status="pending")
        session.add_all([p3a, p3b])
        session.commit()

        # Run reconciliation
        updated = reconcile_job_statuses(session)
        assert updated == 2

        # Verify
        session.refresh(j1)
        session.refresh(j2)
        session.refresh(j3)

        assert j1.status == "completed"
        assert j1.scraped_pages == 1
        assert j2.status == "failed"
        assert j3.status == "running"  # Still has pending page

    SQLModel.metadata.drop_all(engine)


def test_reconcile_job_statuses_skips_active_running_jobs():
    """Verify actively running jobs with 0 pages (during browser startup) are not prematurely marked failed."""
    from modules.core.job_logger import job_log_manager
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        # Job 4: Newly started job, 0 pages, registered in active_jobs
        j4 = ScrapeJob(target_url="https://example.com/active-new", status="running")
        session.add(j4)
        session.commit()
        session.refresh(j4)

        job_log_manager.mark_job_active(j4.id)
        try:
            updated = reconcile_job_statuses(session)
            assert updated == 0

            session.refresh(j4)
            assert j4.status == "running", "Active job must remain 'running' even with 0 pages"
        finally:
            job_log_manager.mark_job_inactive(j4.id)

    SQLModel.metadata.drop_all(engine)
