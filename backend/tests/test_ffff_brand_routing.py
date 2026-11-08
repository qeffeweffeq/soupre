import json
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine, SQLModel
from sqlalchemy.pool import StaticPool

from main import app
from database.session import get_session
from database.models import Workspace, Page, ScrapeJob, Category, Types, Tag, Element
from modules.notion.client import (
    get_notion_client,
    get_workspace_token,
    get_target_db_id,
)
from modules.notion.databases import list_available_databases
from modules.notion.streamer import stream_batch_to_notion


@pytest.fixture
def ffff_test_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        ws_fp = Workspace(name="FP", slug="fp", notion_id="d74d19f8-6945-4eb0-8d40-e879f3b1d571")
        ws_qfwfq = Workspace(name="Qfwfq", slug="qfwfq", notion_id="d46f6dbf-310c-4c4d-800e-813eeba04ab6")
        ws_full = Workspace(name="Full-Site", slug="full-site", notion_id=None)
        ws_ffff = Workspace(name="FFFF", slug="ffff", icon="🎨", color="pink", notion_id=None)
        session.add_all([ws_fp, ws_qfwfq, ws_full, ws_ffff])
        session.commit()

        # Add categories and types in FP and Qfwfq that previously caused Pentagram misclassifications
        cat_design = Category(name="Design", slug="design", workspace_id=ws_fp.id, section="services", notion_id="d74d19f8-6945-4eb0-8d40-e879f3b1d571")
        cat_street = Category(name="Street", slug="street", workspace_id=ws_qfwfq.id, section="goods", notion_id="d46f6dbf-310c-4c4d-800e-813eeba04ab6")
        session.add_all([cat_design, cat_street])
        session.commit()
        session.refresh(cat_design)
        session.refresh(cat_street)

        type_solo = Types(name="Solo", slug="solo", category_id=cat_design.id)
        type_acc = Types(name="Accessories", slug="accessories", category_id=cat_street.id)
        tag_ai = Tag(name="AI", slug="ai", workspace_id=None)
        tag_3d = Tag(name="3D", slug="3d", workspace_id=None)
        elem_bag = Element(name="Bag", slug="bag")
        session.add_all([type_solo, type_acc, tag_ai, tag_3d, elem_bag])
        session.commit()

    def _get_sess():
        with Session(engine) as sess:
            yield sess

    app.dependency_overrides[get_session] = _get_sess
    yield
    app.dependency_overrides.clear()
    SQLModel.metadata.drop_all(engine)


def test_batch_upload_routes_brands_to_ffff_workspace(ffff_test_session):
    client = TestClient(app)

    file_content = (
        "https://www.pentagram.com/work/in-depth-brand-redesign\n"
        "https://www.pentagram.com/news/mich-appointment\n"
        "https://www.behance.net/gallery/999999/branding-identity\n"
        "https://www.itsnicethat.com/articles/creative-showcase-ai\n"
    )

    response = client.post(
        "/api/jobs/batch-upload",
        files={"file": ("brands.txt", file_content.encode("utf-8"), "text/plain")},
    )

    assert response.status_code == 200
    data = response.json()
    items = data["items"]
    assert len(items) == 4

    for item in items:
        # 1. Must be routed strictly to FFFF
        assert item["workspace_slug"] == "ffff", f"Failed for url: {item['clean_url']}"
        assert item["workspace_name"] == "FFFF"

        # 2. Must NEVER be synced to Notion
        assert item["notion_id"] is None
        assert item["notion_db_name"] is None

        # 3. Must NOT predict GOODS elements
        assert item["element_ids"] == []
        assert item["predicted_elements"] == []

    # Check that global canonical tag "AI" still predicted for itsnicethat AI article
    ai_item = next(it for it in items if "creative-showcase-ai" in it["clean_url"])
    assert "AI" in ai_item["predicted_tags"]


def test_ffff_client_isolation():
    assert get_notion_client("ffff") is None
    assert get_workspace_token("ffff") is None
    assert get_target_db_id("ffff") is None
    assert list_available_databases("ffff") == []


@pytest.mark.asyncio
async def test_streamer_ffff_skips_with_zero_notion_calls():
    mock_client = MagicMock()

    items = [
        {"url": "https://www.pentagram.com/work/identity", "title": "Pentagram", "workspace_slug": "ffff"},
        {"url": "https://www.behance.net/gallery/123", "title": "Behance", "workspace_slug": "ffff"},
    ]

    with patch("modules.notion.streamer.get_notion_client", return_value=mock_client):
        events = []
        async for chunk in stream_batch_to_notion(items, dry_run=False, rate_limit_delay=0.0):
            if chunk.startswith("data: "):
                events.append(json.loads(chunk[6:].strip()))

        # Both items skipped + 1 complete event
        assert len(events) == 3
        assert events[0]["status"] == "skipped"
        assert "isolated" in events[0]["reason"].lower()
        assert events[1]["status"] == "skipped"
        assert events[2]["type"] == "complete"
        assert events[2]["skipped"] == 2
        assert events[2]["created"] == 0

        mock_client.databases.query.assert_not_called()
        mock_client.pages.create.assert_not_called()
        mock_client.pages.update.assert_not_called()


def test_api_forbids_syncing_ffff_pages():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        ws_ffff = Workspace(name="FFFF", slug="ffff")
        session.add(ws_ffff)
        session.commit()

        job = ScrapeJob(target_url="https://www.pentagram.com/work", workspace_id=ws_ffff.id)
        session.add(job)
        session.commit()

        page = Page(job_id=job.id, workspace_id=ws_ffff.id, url="https://www.pentagram.com/work/project", title="Pentagram Project")
        session.add(page)
        session.commit()
        session.refresh(page)

    def get_test_session():
        with Session(engine) as sess:
            yield sess

    app.dependency_overrides[get_session] = get_test_session
    client = TestClient(app)

    res = client.post(f"/api/notion/pages/{page.id}/sync")
    assert res.status_code == 400
    assert "strictly isolated" in res.json()["detail"].lower()

    # Check Notion status endpoint
    status_res = client.get("/api/notion/status")
    assert status_res.status_code == 200
    assert status_res.json()["configured_workspaces"]["ffff"] is False

    app.dependency_overrides.clear()
    SQLModel.metadata.drop_all(engine)
