"""
tests/test_batch_urls_preview.py
Comprehensive tests for URL parameter analysis, batch preview upload endpoint,
curated batch execution endpoint, and Full-Site workspace routing.
"""

import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlmodel import Session, select, create_engine
from sqlalchemy.pool import StaticPool

from database.models import SQLModel, ScrapeJob, Workspace, Category, Types
import main
from main import app, get_session
from modules.utils.url_utils import analyze_url_parameters, extract_and_analyze_urls_from_text


test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@pytest.fixture(name="client")
def client_fixture():
    SQLModel.metadata.create_all(test_engine)

    def get_test_session():
        with Session(test_engine) as session:
            yield session

    app.dependency_overrides[get_session] = get_test_session
    orig_engine = main.engine
    main.engine = test_engine

    with patch("main._run_job_with_logging"):
        client = TestClient(app)
        yield client

    app.dependency_overrides.clear()
    main.engine = orig_engine
    SQLModel.metadata.drop_all(test_engine)


def test_analyze_url_parameters_tracking_and_functional():
    raw = "https://example.com/shop/apparel/hoodie?utm_source=meta&utm_medium=cpc&size=xl&color=black&fbclid=123#reviews"
    analysis = analyze_url_parameters(raw)

    assert analysis["raw_url"] == raw
    assert analysis["clean_url"] == "https://example.com/shop/apparel/hoodie?size=xl&color=black"
    assert "utm_source=meta" in analysis["stripped_params"]
    assert "utm_medium=cpc" in analysis["stripped_params"]
    assert "fbclid=123" in analysis["stripped_params"]
    assert "size=xl" in analysis["retained_params"]
    assert "color=black" in analysis["retained_params"]
    assert analysis["has_stripped_params"] is True


def test_analyze_url_parameters_clean_url_no_stripped():
    raw = "https://example.com/about"
    analysis = analyze_url_parameters(raw)

    assert analysis["raw_url"] == raw
    assert analysis["clean_url"] == "https://example.com/about"
    assert analysis["stripped_params"] == []
    assert analysis["retained_params"] == []
    assert analysis["has_stripped_params"] is False


def test_extract_and_analyze_urls_from_text_deduplication():
    text = """
    Check out our catalog:
    1. https://site.com/products?utm_source=newsletter
    2. https://site.com/products?utm_campaign=winter (duplicate clean URL)
    3. https://site.com/blog/article-1?tag=python
    """
    results = extract_and_analyze_urls_from_text(text)

    assert len(results) == 2
    assert results[0]["clean_url"] == "https://site.com/products"
    assert results[0]["has_stripped_params"] is True
    assert results[1]["clean_url"] == "https://site.com/blog/article-1?tag=python"
    assert results[1]["has_stripped_params"] is False


def test_batch_upload_with_taxonomy_prediction(client):
    # Seed workspace, category, and type
    with Session(test_engine) as session:
        ws = Workspace(name="Test WS", slug="test-ws")
        session.add(ws)
        session.flush()

        cat = Category(name="Street", slug="street", section="goods", workspace_id=ws.id)
        session.add(cat)
        session.flush()

        t = Types(name="Apparel", slug="apparel", category_id=cat.id)
        session.add(t)
        session.commit()
        ws_id = ws.id

    raw_text = """
    https://brand.com/street/apparel/cool-jacket?utm_source=ig&color=blue
    https://brand.com/news/spring-update
    """

    res = client.post("/api/jobs/batch-upload", json={"raw_text": raw_text, "filename": "sample_batch.txt"})
    assert res.status_code == 200
    data = res.json()

    assert data["count"] == 2
    assert data["filename"] == "sample_batch.txt"
    assert len(data["items"]) == 2

    first_item = data["items"][0]
    assert first_item["clean_url"] == "https://brand.com/street/apparel/cool-jacket?color=blue"
    assert first_item["has_stripped_params"] is True
    assert "utm_source=ig" in first_item["stripped_params"]
    assert first_item["domain"] == "brand.com"
    # Category and Type token matching
    assert first_item["predicted_category_name"] == "Street"
    assert first_item["predicted_types_name"] == "Apparel"
    assert first_item["workspace_id"] == ws_id
    assert first_item["workspace_name"] == "Test WS"

    second_item = data["items"][1]
    assert second_item["clean_url"] == "https://brand.com/news/spring-update"
    assert second_item["has_stripped_params"] is False


def test_batch_execute_jobs(client):
    with Session(test_engine) as session:
        ws = Workspace(name="Full-Site", slug="full-site", icon="🌐", color="sapphire")
        ws2 = Workspace(name="Qfwfq", slug="qfwfq", icon="⚡", color="flamingo")
        cat = Category(name="Technology", slug="technology", section="services")
        session.add(ws)
        session.add(ws2)
        session.add(cat)
        session.flush()
        t = Types(name="Service", slug="service", category_id=cat.id)
        session.add(t)
        session.commit()
        ws_id = ws.id
        ws2_id = ws2.id
        cat_id = cat.id
        t_id = t.id

    payload = {
        "items": [
            {
                "url": "https://api.dev/v1?utm_term=test",
                "clean_url": "https://api.dev/v1",
                "workspace_id": ws2_id,
                "category_id": cat_id,
                "types_id": t_id,
                "content_type": "services",
            },
            {
                "url": "https://example.com/docs",
                "clean_url": "https://example.com/docs",
                "category_id": None,
                "types_id": None,
                "content_type": "pages",
            },
        ],
        "workspace_id": ws_id,
        "scrape_mode": "single",
    }

    res = client.post("/api/jobs/batch-execute", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["queued"] == 2
    assert len(data["jobs"]) == 2

    with Session(test_engine) as session:
        jobs = session.exec(select(ScrapeJob)).all()
        assert len(jobs) == 2
        job1 = session.exec(select(ScrapeJob).where(ScrapeJob.target_url == "https://api.dev/v1")).first()
        assert job1 is not None
        assert job1.workspace_id == ws2_id  # Verified per-item workspace overrides payload fallback!
        assert job1.category_id == cat_id
        assert job1.types_id == t_id

        job2 = session.exec(select(ScrapeJob).where(ScrapeJob.target_url == "https://example.com/docs")).first()
        assert job2 is not None
        assert job2.workspace_id == ws_id  # Falls back to batch payload workspace


def test_sitemap_mode_auto_routes_to_full_site_workspace(client):
    with Session(test_engine) as session:
        ws = Workspace(name="Full-Site", slug="full-site", icon="🌐", color="sapphire")
        session.add(ws)
        session.commit()
        full_site_id = ws.id

    res = client.post("/api/jobs", params={"target_url": "https://hotel.it", "scrape_mode": "sitemap"})
    assert res.status_code == 200
    job_id = res.json()["job_id"]

    with Session(test_engine) as session:
        job = session.get(ScrapeJob, job_id)
        assert job is not None
        assert job.workspace_id == full_site_id
        assert job.scrape_mode == "sitemap"


def test_unclassified_url_remains_unassigned_and_paulsmith_predicts_street_apparel(client):
    with Session(test_engine) as session:
        ws_goods = Workspace(name="Qfwfq", slug="qfwfq")
        session.add(ws_goods)
        session.flush()

        cat = Category(name="Street", slug="street", section="goods", workspace_id=ws_goods.id)
        session.add(cat)
        session.flush()

        t = Types(name="Apparel", slug="apparel", category_id=cat.id)
        session.add(t)
        session.commit()
        goods_ws_id = ws_goods.id

    raw_text = """
    https://www.paulsmith.com/eu/river-collage-viscose-blend-shirt
    https://totally-unclassified-domain.xyz/random/unknown/path-123
    """

    res = client.post("/api/jobs/batch-upload", json={"raw_text": raw_text, "filename": "test.txt"})
    assert res.status_code == 200
    data = res.json()
    assert len(data["items"]) == 2

    paul_item = data["items"][0]
    assert paul_item["predicted_category_name"] == "Street"
    assert paul_item["predicted_types_name"] == "Apparel"
    assert paul_item["workspace_id"] == goods_ws_id

    unclassified_item = data["items"][1]
    assert unclassified_item["predicted_category_name"] is None
    assert unclassified_item["predicted_types_name"] is None
    # Crucial assertion: unclassified item is None (Unassigned), never forced to Full-Site
    assert unclassified_item["workspace_id"] is None


def test_batch_verify_urls_detects_404_and_homepage_redirect_with_raw_fallback(client, monkeypatch):
    import routers.jobs as jobs_router

    async def fake_probe(http_client, target_url: str):
        if target_url == "https://www.ralphlauren.it/it/espadrillas-cameryn-iii-in-tela-123":
            return True, "Redirected to Homepage", 200, "https://www.ralphlauren.it/it"
        if target_url == "https://www.ralphlauren.it/it/espadrillas-cameryn-iii-in-tela-123?dwvar=valid":
            return True, "404 Not Found", 404, target_url
        if target_url == "https://example.shop/item/abc":
            return True, "404 Not Found", 404, target_url
        if target_url == "https://example.shop/item/abc?sku=999":
            return False, "OK", 200, target_url
        return False, "OK", 200, target_url

    monkeypatch.setattr(jobs_router, "_probe_url_reachability", fake_probe)

    payload = {
        "items": [
            {
                "id": "url-1",
                "raw_url": "https://www.ralphlauren.it/it/espadrillas-cameryn-iii-in-tela-123?dwvar=valid",
                "clean_url": "https://www.ralphlauren.it/it/espadrillas-cameryn-iii-in-tela-123",
                "has_stripped_params": True,
            },
            {
                "id": "url-2",
                "raw_url": "https://example.shop/item/abc?sku=999",
                "clean_url": "https://example.shop/item/abc",
                "has_stripped_params": True,
            },
            {
                "id": "url-3",
                "raw_url": "https://www.paulsmith.com/eu/river-collage-viscose-blend-shirt",
                "clean_url": "https://www.paulsmith.com/eu/river-collage-viscose-blend-shirt",
                "has_stripped_params": False,
            },
        ]
    }

    res = client.post("/api/jobs/batch-verify-urls", json=payload)
    assert res.status_code == 200
    results = {r["id"]: r for r in res.json()["results"]}

    # url-1: both clean_url (redirect to homepage) and raw_url (404) failed -> Page / Item Not Found
    assert results["url-1"]["url_status"] == "not_found"
    assert "Page / Item Not Found" in results["url-1"]["url_status_label"]
    assert results["url-1"]["use_raw_url"] is False

    # url-2: clean_url returned 404, but raw_url with params succeeded -> params_required
    assert results["url-2"]["url_status"] == "params_required"
    assert results["url-2"]["use_raw_url"] is True

    # url-3: clean_url is live -> ok
    assert results["url-3"]["url_status"] == "ok"
    assert results["url-3"]["use_raw_url"] is False


