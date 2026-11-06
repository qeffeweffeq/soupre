import asyncio
import json
import pytest
from unittest.mock import MagicMock, patch
from sqlmodel import Session, create_engine, SQLModel

from database.models import Workspace, Page, ScrapeJob
from modules.notion.streamer import (
    check_url_exists,
    stream_batch_to_notion,
)


def test_check_url_exists():
    mock_client = MagicMock()
    mock_client.databases.query.return_value = {
        "results": [{"id": "existing-page-id-123"}]
    }

    found_id = check_url_exists(mock_client, "db-123", "https://example.com/test")
    assert found_id == "existing-page-id-123"
    mock_client.databases.query.assert_called_once()


def test_check_url_exists_not_found():
    mock_client = MagicMock()
    mock_client.databases.query.return_value = {"results": []}

    found_id = check_url_exists(mock_client, "db-123", "https://notfound.com")
    assert found_id is None


@pytest.mark.asyncio
async def test_stream_batch_dry_run():
    items = [
        {"url": "https://test1.com", "title": "Test 1", "workspace_slug": "fp"},
        {"url": "https://test2.com", "title": "Test 2", "workspace_slug": "qfwfq"},
    ]

    events = []
    async for chunk in stream_batch_to_notion(items, dry_run=True, rate_limit_delay=0.0):
        if chunk.startswith("data: "):
            payload = json.loads(chunk[6:].strip())
            events.append(payload)

    # 2 item events + 1 completion summary event
    assert len(events) == 3

    assert events[0]["index"] == 1
    assert events[0]["status"] == "dry_run"
    assert events[0]["name"] == "Test 1"
    assert events[0]["workspace"] == "fp"
    assert "URL" in events[0]["payload"]

    assert events[1]["index"] == 2
    assert events[1]["status"] == "dry_run"
    assert events[1]["name"] == "Test 2"
    assert events[1]["workspace"] == "qfwfq"
    assert "Url 1" in events[1]["payload"]

    assert events[2]["type"] == "complete"
    assert events[2]["total"] == 2
    assert events[2]["dry_run"] is True


@pytest.mark.asyncio
async def test_stream_batch_live_mocked():
    mock_client = MagicMock()
    mock_client.databases.query.return_value = {"results": []}
    mock_client.pages.create.return_value = {"id": "new-created-notion-page-id"}

    items = [
        {"url": "https://service.com", "title": "Service Name", "workspace_slug": "fp"},
    ]

    with patch("modules.notion.streamer.get_notion_client", return_value=mock_client):
        events = []
        async for chunk in stream_batch_to_notion(items, dry_run=False, rate_limit_delay=0.01):
            if chunk.startswith("data: "):
                events.append(json.loads(chunk[6:].strip()))

        assert len(events) == 2
        assert events[0]["status"] == "created"
        assert events[0]["notion_page_id"] == "new-created-notion-page-id"
        assert events[0]["notion_url"] == "https://notion.so/newcreatednotionpageid"
        assert events[1]["type"] == "complete"
        assert events[1]["created"] == 1


@pytest.mark.asyncio
async def test_stream_batch_updates_page_record():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        ws = Workspace(name="FP", slug="fp")
        session.add(ws)
        session.commit()

        job = ScrapeJob(target_url="https://site.com", workspace_id=ws.id)
        session.add(job)
        session.commit()

        page = Page(job_id=job.id, workspace_id=ws.id, url="https://site.com/p1", title="Page 1")
        session.add(page)
        session.commit()
        session.refresh(page)

        mock_client = MagicMock()
        mock_client.databases.query.return_value = {"results": []}
        mock_client.pages.create.return_value = {"id": "notion-synced-123"}

        items = [
            {"id": page.id, "url": page.url, "title": page.title, "workspace_slug": "fp"}
        ]

        with patch("modules.notion.streamer.get_notion_client", return_value=mock_client):
            events = []
            async for chunk in stream_batch_to_notion(items, session=session, dry_run=False, rate_limit_delay=0.0):
                if chunk.startswith("data: "):
                    events.append(json.loads(chunk[6:].strip()))

            session.refresh(page)
            assert page.notion_page_id == "notion-synced-123"
            assert page.sync_status == "synced"
            assert page.last_synced_at is not None



def test_api_notion_status_and_stream_endpoints():
    from fastapi.testclient import TestClient
    from main import app

    client = TestClient(app)
    status_res = client.get("/api/notion/status")
    assert status_res.status_code == 200
    data = status_res.json()
    assert data["status"] == "ok"
    assert "configured_workspaces" in data
    assert data["configured_workspaces"]["full-site"] is False
    assert "databases" in data

    # Test SSE stream endpoint (dry-run)
    stream_res = client.post(
        "/api/notion/stream",
        json={
            "items": [
                {"url": "https://test.com/item1", "title": "Item 1", "workspace_slug": "fp"}
            ],
            "dry_run": True,
            "rate_limit_delay": 0.0,
        },
    )
    assert stream_res.status_code == 200
    assert "text/event-stream" in stream_res.headers["content-type"]
    text = stream_res.text
    assert "data: " in text
    assert "dry_run" in text
