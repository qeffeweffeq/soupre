import pytest
from modules.core.job_logger import job_log_manager


@pytest.fixture(autouse=True)
def reset_job_logger_active_jobs():
    """Ensure clean active job tracking state before and after each test."""
    job_log_manager._active_jobs.clear()
    yield
    job_log_manager._active_jobs.clear()
