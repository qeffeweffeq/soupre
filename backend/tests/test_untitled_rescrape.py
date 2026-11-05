import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine, select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main as app_main
from database.models import ScrapeJob, Page
from modules.scrapers.engine import (
    extract_page_title,
    is_untitled_or_empty_page,
    cleanup_untitled_page_artifacts,
    scrape_page,
    run_scrape_job,
)
from modules.scrapers.fetcher import _safe_get_page_content
from modules.scrapers.sitemap_fetcher import SitemapEntry


def test_extract_page_title_cascade():
    # 1. Standard <title>
    s1 = BeautifulSoup("<html><head><title>  Corsi di Surf – Palace Lido  </title></head><body></body></html>", "html.parser")
    assert extract_page_title(s1) == "Corsi di Surf – Palace Lido"

    # 2. Bot challenge <title> ignored, falls back to og:title
    s2 = BeautifulSoup(
        '<html><head><title>Just a moment...</title><meta property="og:title" content="Wine Tasting in Bolgheri"></head></html>',
        "html.parser",
    )
    assert extract_page_title(s2) == "Wine Tasting in Bolgheri"

    # 3. Missing <title> and og:title, falls back to <h1>
    s3 = BeautifulSoup(
        "<html><head></head><body><main><h1>Horseback Rides in Pine Grove</h1></main></body></html>",
        "html.parser",
    )
    assert extract_page_title(s3) == "Horseback Rides in Pine Grove"

    # 4. Completely empty DOM -> Untitled
    s4 = BeautifulSoup("<html><head></head><body></body></html>", "html.parser")
    assert extract_page_title(s4) == "Untitled"


@pytest.mark.asyncio
async def test_safe_get_page_content_retries_on_navigation_error():
    mock_page = MagicMock()
    calls = {"count": 0}

    async def _content():
        calls["count"] += 1
        if calls["count"] < 3:
            raise Exception("Page.content: Unable to retrieve content because the page is navigating and changing the content.")
        return "<html><head><title>Loaded After Navigation</title></head><body>Content</body></html>"

    mock_page.content = _content
    mock_page.wait_for_load_state = AsyncMock()

    with patch("modules.scrapers.fetcher.asyncio.sleep", new=AsyncMock()):
        html = await _safe_get_page_content(mock_page, retries=3)
    assert "Loaded After Navigation" in html
    assert calls["count"] == 3


def test_is_untitled_or_empty_and_cleanup(tmp_path):
    with patch("modules.scrapers.engine.DOWNLOADS_DIR", str(tmp_path)):
        job_dir = tmp_path / "job_34_palacelidosuites_com"
        untitled_dir = job_dir / "it" / "experiences" / "Untitled_esperienze_corsi-di-surf"
        untitled_dir.mkdir(parents=True)

        md_file = untitled_dir / "Untitled.md"
        md_file.write_text(
            "# Untitled\n\n## Metadata\n\n| Key | Value |\n|---|---|\n| **URL** | https://palacelidosuites.com/esperienze/corsi-di-surf/ |\n\n---\n\n",
            encoding="utf-8",
        )
        meta_file = untitled_dir / "metadata.csv"
        meta_file.write_text("Key,Value\nURL,https://palacelidosuites.com/esperienze/corsi-di-surf/\nTitle,Untitled\n", encoding="utf-8")
        shot_file = untitled_dir / "screenshot_palacelidosuites_com_123.png"
        shot_file.write_bytes(b"blank")

        page = Page(
            id=1,
            job_id=34,
            url="https://palacelidosuites.com/esperienze/corsi-di-surf/",
            title="Untitled",
            status="completed",
            markdown_path="../_downloads/job_34_palacelidosuites_com/it/experiences/Untitled_esperienze_corsi-di-surf/Untitled.md",
            screenshot_path="../_downloads/job_34_palacelidosuites_com/it/experiences/Untitled_esperienze_corsi-di-surf/screenshot_palacelidosuites_com_123.png",
        )

        assert is_untitled_or_empty_page(page, str(job_dir)) is True
        cleanup_untitled_page_artifacts(page, str(job_dir))
        assert not untitled_dir.exists()


@pytest.mark.asyncio
async def test_scrape_page_retries_and_rejects_empty_untitled(tmp_path):
    db_file = tmp_path / "test_scrape.db"
    test_engine = create_engine(f"sqlite:///{db_file}")
    SQLModel.metadata.create_all(test_engine)

    with patch("modules.scrapers.engine.DOWNLOADS_DIR", str(tmp_path)), \
         patch("modules.scrapers.engine.asyncio.sleep", new=AsyncMock()):

        # Case 1: Both browser attempts and HTTP fallback return >500-byte blank Untitled skeleton HTML -> raises RuntimeError and leaves no Untitled dir
        blank_skeleton_html = "<html><head><style>" + ("body { margin: 0; } " * 30) + "</style></head><body><div></div></body></html>"
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_resp.raise_for_status.side_effect = Exception("Service Unavailable")

        with patch("modules.scrapers.engine.fetch_url_with_browser", new=AsyncMock(return_value=(blank_skeleton_html, "", []))), \
             patch("requests.get", return_value=mock_resp):
            with pytest.raises(RuntimeError, match="Untitled"):
                await scrape_page(
                    "https://palacelidosuites.com/esperienze/corsi-di-sup/",
                    job_id=34,
                    db_engine=test_engine,
                    job_dir_name="job_34_palacelidosuites_com",
                )

        # Verify no Untitled directory was created
        job_dir = tmp_path / "job_34_palacelidosuites_com"
        for root, dirs, _ in os.walk(job_dir):
            for d in dirs:
                assert not d.startswith("Untitled")

        # Case 2: First browser attempt returns blank Untitled, second browser attempt succeeds with real content
        rich_html = (
            "<html><head><title>Corsi di SUP a Marina di Cecina – Palace Lido</title></head>"
            "<body><main><h1>Corsi di SUP a Marina di Cecina</h1>"
            "<p>" + ("Scopri i nostri corsi di SUP sulla costa toscana con istruttori qualificati. " * 6) + "</p>"
            "</main></body></html>"
        )
        browser_mock = AsyncMock(
            side_effect=[
                ("<html><head></head><body></body></html>", "", []),
                (rich_html, "", []),
            ]
        )
        with patch("modules.scrapers.engine.fetch_url_with_browser", new=browser_mock), \
             patch("modules.scrapers.engine.extract_best_icon", return_value="https://palacelidosuites.com/favicon.ico"):
            await scrape_page(
                "https://palacelidosuites.com/esperienze/corsi-di-sup/",
                job_id=34,
                db_engine=test_engine,
                job_dir_name="job_34_palacelidosuites_com",
                sitemap_hreflang="it",
                sitemap_content_type="experiences",
            )

        assert browser_mock.call_count == 2
        with Session(test_engine) as session:
            saved_page = session.exec(select(Page).where(Page.job_id == 34)).first()
            assert saved_page is not None
            assert saved_page.status == "completed"
            assert saved_page.title == "Corsi di SUP a Marina di Cecina – Palace Lido"
            assert "Untitled" not in (saved_page.markdown_path or "")


@pytest.mark.asyncio
async def test_run_scrape_job_and_api_rescrape_untitled_pages(tmp_path):
    db_file = tmp_path / "test_api_untitled.db"
    test_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(test_engine)

    job_dir = tmp_path / "job_1_palacelidosuites_com"
    untitled_dir = job_dir / "it" / "experiences" / "Untitled"
    untitled_dir.mkdir(parents=True)
    (untitled_dir / "Untitled.md").write_text("# Untitled\n\n---\n\n", encoding="utf-8")
    (untitled_dir / "metadata.csv").write_text(
        "Key,Value\nURL,https://palacelidosuites.com/esperienze/corsi-di-sup/\nTitle,Untitled\n",
        encoding="utf-8",
    )

    valid_dir = job_dir / "it" / "experiences" / "Escursioni"
    valid_dir.mkdir(parents=True)
    (valid_dir / "Escursioni.md").write_text(
        "# Escursioni\n\n## Metadata\n\n| Key | Value |\n|---|---|\n\n---\n\nValid body content here.",
        encoding="utf-8",
    )

    with Session(test_engine) as session:
        job = ScrapeJob(
            id=1,
            target_url="https://palacelidosuites.com",
            title="Palace Lido",
            status="completed",
            scrape_mode="sitemap",
            total_pages=2,
            scraped_pages=2,
        )
        session.add(job)
        session.add(
            Page(
                id=10,
                job_id=1,
                url="https://palacelidosuites.com/esperienze/escursioni/",
                url_path="/esperienze/escursioni/",
                title="Escursioni",
                status="completed",
                hreflang="it",
                content_type="experiences",
                markdown_path="../_downloads/job_1_palacelidosuites_com/it/experiences/Escursioni/Escursioni.md",
            )
        )
        session.add(
            Page(
                id=11,
                job_id=1,
                url="https://palacelidosuites.com/esperienze/corsi-di-sup/",
                url_path="/esperienze/corsi-di-sup/",
                title="Untitled",
                status="completed",
                hreflang="it",
                content_type="experiences",
                markdown_path="../_downloads/job_1_palacelidosuites_com/it/experiences/Untitled/Untitled.md",
            )
        )
        session.commit()

    def override_get_session():
        with Session(test_engine) as s:
            yield s

    app_main.app.dependency_overrides[app_main.get_session] = override_get_session
    client = TestClient(app_main.app)

    try:
        with patch("main.DOWNLOADS_DIR", str(tmp_path)), \
             patch("modules.scrapers.engine.DOWNLOADS_DIR", str(tmp_path)), \
             patch("main._run_job_with_logging", new=AsyncMock()):

            # 1. GET /api/jobs reports untitled_count == 1
            res_jobs = client.get("/api/jobs")
            assert res_jobs.status_code == 200
            job_payload = res_jobs.json()[0]
            assert job_payload["untitled_count"] == 1

            # 2. POST /api/jobs/{job_id}/rescrape-untitled resets the Untitled page to pending and removes Untitled dir
            res_rescrape = client.post("/api/jobs/1/rescrape-untitled")
            assert res_rescrape.status_code == 200
            data = res_rescrape.json()
            assert data["status"] == "resumed"
            assert data["queued_pages"] == 1
            assert not untitled_dir.exists()
            assert valid_dir.exists()

            with Session(test_engine) as session:
                p10 = session.get(Page, 10)
                p11 = session.get(Page, 11)
                assert p10.status == "completed"
                assert p11.status == "pending"
                assert p11.markdown_path is None
    finally:
        app_main.app.dependency_overrides.clear()
