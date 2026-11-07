from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select, create_engine, SQLModel
from sqlalchemy.pool import StaticPool

from database.models import Workspace, Category, Types, Tag
from database.migrations import migrate_db
from database.seed_taxonomies import seed_taxonomies
from modules.notion.schema_syncer import sync_local_snapshots, sync_remote_notion, sync_schemas
from modules.notion.client import FP_TAGS_DB_ID, QFWFQ_TAGS_DB_ID
from main import app
from database.session import get_session


@pytest.fixture(name="mem_session")
def mem_session_fixture():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    migrate_db(engine)
    with Session(engine) as session:
        seed_taxonomies(session)
        yield session


def test_sync_local_snapshots_populates_tags(mem_session):
    res = sync_local_snapshots(mem_session)
    assert res["status"] == "success"
    assert res["tags"] >= 100

    tags = mem_session.exec(select(Tag)).all()
    assert len(tags) >= 100
    fp_tags = [t for t in tags if t.workspace and t.workspace.slug == "fp"]
    assert len(fp_tags) > 0


def test_sync_remote_notion_updates_tag_notion_ids(mem_session):
    mock_fp_client = MagicMock()
    mock_qfwfq_client = MagicMock()

    # Mock FP Roles query
    mock_fp_client.databases.query.side_effect = lambda database_id, page_size: {
        "results": [
            {
                "id": "fp-tag-notion-uuid-1",
                "properties": {
                    "Name": {"type": "title", "title": [{"plain_text": "AI"}]}
                }
            },
            {
                "id": "fp-tag-notion-uuid-2",
                "properties": {
                    "Name": {"type": "title", "title": [{"plain_text": "3D"}]}
                }
            }
        ]
    }

    mock_qfwfq_client.databases.query.side_effect = lambda database_id, page_size: {
        "results": [
            {
                "id": "qfwfq-tag-notion-uuid-1",
                "properties": {
                    "Name": {"type": "title", "title": [{"plain_text": "BF24"}]}
                }
            }
        ]
    }

    with patch("modules.notion.schema_syncer.get_notion_client") as mock_get_client:
        mock_get_client.side_effect = lambda ws: mock_fp_client if ws == "fp" else mock_qfwfq_client

        res = sync_remote_notion(mem_session)
        assert res["status"] == "success"
        assert res["mode"] == "remote"
        assert res["updated"]["tags"] >= 2

        ai_tag = mem_session.exec(select(Tag).where(Tag.slug == "ai")).first()
        assert ai_tag is not None
        assert ai_tag.notion_id == "fp-tag-notion-uuid-1"

        tag_3d = mem_session.exec(select(Tag).where(Tag.slug == "3d")).first()
        assert tag_3d is not None
        assert tag_3d.notion_id == "fp-tag-notion-uuid-2"


def test_api_trigger_schema_sync(mem_session):
    def get_session_override():
        yield mem_session

    app.dependency_overrides[get_session] = get_session_override
    try:
        with TestClient(app) as client:
            resp = client.post("/api/notion/schemas/sync?mode=local")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "success"
            assert data["tags"] >= 100
    finally:
        app.dependency_overrides.clear()
