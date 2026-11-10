import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from sqlmodel import Session, select
from database.session import engine
from database.models import ScrapeJob, Page
from main import app
from modules.scrapers.engine import run_scrape_job
from modules.scrapers.sitemap_fetcher import SitemapEntry


@pytest.fixture
def client():
    return TestClient(app)


def test_create_job_restores_existing_without_name_error(client: TestClient):
    """Verifies create_job handles re-submitting an existing URL without NameError: item is not defined."""
    target = "https://test-resilience-existing.com/page1"
    with Session(engine) as session:
        existing = session.exec(select(ScrapeJob).where(ScrapeJob.target_url == target)).first()
        if not existing:
            existing = ScrapeJob(target_url=target, scrape_mode="single", status="failed")
            session.add(existing)
            session.commit()
            session.refresh(existing)

    with patch("routers.jobs._dispatch_run_job", new_callable=AsyncMock) as mock_dispatch:
        resp = client.post(f"/api/jobs?target_url={target}&scrape_mode=single")
        assert resp.status_code == 200
        data = resp.json()
        assert data["job_id"] == existing.id
        assert data["status"] == "restored"


@pytest.mark.asyncio
async def test_run_scrape_job_sitemap_no_detached_instance_error():
    """Verifies run_scrape_job synchronizes sitemap entries into Pages without DetachedInstanceError."""
    target = "https://test-resilience-sitemap.com"
    with Session(engine) as session:
        job = ScrapeJob(target_url=target, scrape_mode="sitemap", status="running", workspace_id=1, category_id=2, types_id=3)
        session.add(job)
        session.commit()
        session.refresh(job)
        job_id = job.id

    mock_entries = [
        SitemapEntry(url=f"{target}/page1", content_type="pages"),
        SitemapEntry(url=f"{target}/page2", content_type="pages"),
    ]

    async def fake_scrape(u, jid, db_eng, **kwargs):
        with Session(db_eng) as s:
            p = s.exec(select(Page).where(Page.job_id == jid, Page.url == u)).first()
            if p:
                p.status = "completed"
                s.add(p)
                s.commit()

    with patch("modules.scrapers.fetcher.fetch_url_with_browser", new_callable=AsyncMock) as mock_browser, \
         patch("modules.scrapers.engine.extract_best_icon", return_value="https://test-resilience-sitemap.com/icon.png"), \
         patch("modules.scrapers.engine._update_job_contact_csv"), \
         patch("modules.scrapers.sitemap_fetcher.discover_sitemap_urls", new_callable=AsyncMock) as mock_discover, \
         patch("modules.scrapers.engine.scrape_page", side_effect=fake_scrape):

        mock_browser.return_value = ("<html><title>Resilience Test</title></html>", None, None)
        mock_discover.return_value = mock_entries

        await run_scrape_job(
            job_id=job_id,
            target_url=target,
            db_engine=engine,
            scrape_mode="sitemap",
            max_pages=2,
            delay_min=0.001,
            delay_max=0.002,
            session_min=2,
            session_max=2,
            cooldown_min=0.001,
            cooldown_max=0.002,
        )

    with Session(engine) as session:
        saved_job = session.get(ScrapeJob, job_id)
        assert saved_job.status != "failed", "Job should not fail with DetachedInstanceError"
        pages = session.exec(select(Page).where(Page.job_id == job_id)).all()
        assert len(pages) == 2
        for p in pages:
            assert p.workspace_id == 1
            assert p.category_id == 2
            assert p.types_id == 3
