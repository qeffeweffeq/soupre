from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine, select, SQLModel
from sqlalchemy.pool import StaticPool

from main import app
from database.session import get_session
from database.models import Workspace, Category, Types
from modules.notion.schema_syncer import (
    sync_local_snapshots,
    sync_remote_notion,
    sync_schemas,
)
from modules.notion.client import SERVICES_DB_ID, GOODS_DB_ID, ROLES_DB_ID


def test_sync_local_snapshots_populates_notion_ids():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        res = sync_local_snapshots(session)
        assert res["status"] == "success"
        assert res["mode"] == "local"
        assert res["categories"] >= 32
        assert res["types"] > 0

        fp_ws = session.exec(select(Workspace).where(Workspace.slug == "fp")).first()
        assert fp_ws is not None
        assert fp_ws.notion_id == SERVICES_DB_ID

        qfwfq_ws = session.exec(select(Workspace).where(Workspace.slug == "qfwfq")).first()
        assert qfwfq_ws is not None
        assert qfwfq_ws.notion_id == GOODS_DB_ID

        full_site_ws = session.exec(select(Workspace).where(Workspace.slug == "full-site")).first()
        assert full_site_ws is not None
        assert full_site_ws.notion_id is None

        # Verify services categories have notion_id set
        services_cat = session.exec(select(Category).where(Category.slug == "technology")).first()
        assert services_cat is not None
        assert services_cat.notion_id == ROLES_DB_ID

    SQLModel.metadata.drop_all(engine)


def test_sync_remote_notion_mocked():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        sync_local_snapshots(session)

        mock_fp_client = MagicMock()
        mock_fp_client.databases.query.side_effect = [
            # 1. Roles DB query
            {
                "results": [
                    {
                        "id": "notion-role-tech-page-id",
                        "properties": {
                            "Name": {
                                "type": "title",
                                "title": [{"plain_text": "Technology"}]
                            }
                        }
                    }
                ]
            },
            # 2. Types DB query
            {
                "results": [
                    {
                        "id": "notion-type-tools-page-id",
                        "properties": {
                            "Name": {
                                "type": "title",
                                "title": [{"plain_text": "tools"}]
                            }
                        }
                    }
                ]
            },
            # 3. Tags DB query
            {"results": []},
        ]

        with patch("modules.notion.schema_syncer.get_notion_client", return_value=mock_fp_client):
            res = sync_remote_notion(session)
            assert res["status"] == "success"
            assert res["mode"] == "remote"

            cat = session.exec(select(Category).where(Category.slug == "technology")).first()
            assert cat is not None
            assert cat.notion_id == "notion-role-tech-page-id"

            t_obj = session.exec(select(Types).where(Types.slug == "tools")).first()
            assert t_obj is not None
            assert t_obj.notion_id == "notion-type-tools-page-id"

    SQLModel.metadata.drop_all(engine)


def test_api_schema_sync_endpoint():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def get_test_session():
        with Session(engine) as sess:
            yield sess

    app.dependency_overrides[get_session] = get_test_session
    client = TestClient(app)

    res = client.post("/api/notion/schemas/sync?mode=local")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["mode"] == "local"
    assert data["categories"] >= 32

    app.dependency_overrides.clear()
    SQLModel.metadata.drop_all(engine)
