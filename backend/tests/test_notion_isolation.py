import json
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine, SQLModel
from sqlalchemy.pool import StaticPool

from main import app
from database.session import get_session
from database.models import Workspace, Page, ScrapeJob
from modules.notion.client import (
    get_notion_client,
    get_workspace_token,
    get_target_db_id,
)
from modules.notion.streamer import stream_batch_to_notion


def test_full_site_client_isolation():
    assert get_notion_client("full-site") is None
    assert get_notion_client("fullsite") is None
    assert get_workspace_token("full-site") is None
    assert get_workspace_token("fullsite") is None
    assert get_target_db_id("full-site") is None
    assert get_target_db_id("fullsite") is None


@pytest.mark.asyncio
async def test_streamer_full_site_skips_with_zero_notion_calls():
    mock_client = MagicMock()

    items = [
        {"url": "https://fullsite.internal/1", "title": "Internal 1", "workspace_slug": "full-site"},
        {"url": "https://fullsite.internal/2", "title": "Internal 2", "workspace_slug": "fullsite"},
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

        # Assert zero calls to Notion client
        mock_client.databases.query.assert_not_called()
        mock_client.pages.create.assert_not_called()
        mock_client.pages.update.assert_not_called()


def test_api_forbids_syncing_full_site_pages():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        ws_full = Workspace(name="Full-Site", slug="full-site")
        session.add(ws_full)
        session.commit()

        job = ScrapeJob(target_url="https://fullsite.com", workspace_id=ws_full.id)
        session.add(job)
        session.commit()

        page = Page(job_id=job.id, workspace_id=ws_full.id, url="https://fullsite.com/docs", title="Internal Docs")
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

    app.dependency_overrides.clear()
    SQLModel.metadata.drop_all(engine)
