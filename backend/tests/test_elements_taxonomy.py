import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select
from sqlalchemy.pool import StaticPool

from main import app
from database.session import get_session
from database.models import Page, ScrapeJob, Workspace, Category, Types, Element, PageElementLink
from database.migrations import migrate_db
from database.seed_taxonomies import seed_taxonomies
from modules.notion.databases import discover_databases_from_disk, get_database_meta
from modules.notion.client import QFWFQ_ELEMENTS_DB_ID
from modules.notion.mapper import map_goods_payload, build_notion_payload


@pytest.fixture(name="client_and_engine")
def client_and_engine_fixture():
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(test_engine)
    migrate_db(test_engine)

    def get_session_override():
        with Session(test_engine) as session:
            yield session

    app.dependency_overrides[get_session] = get_session_override

    with Session(test_engine) as session:
        seed_taxonomies(session)

    with TestClient(app) as client:
        yield client, test_engine

    app.dependency_overrides.clear()


def test_elements_database_discovery():
    """Verify Elements and Goods (Items) database discovery in notion databases registry."""
    databases = discover_databases_from_disk()
    db_ids = {d.id: d for d in databases}

    assert QFWFQ_ELEMENTS_DB_ID in db_ids
    elem_db = db_ids[QFWFQ_ELEMENTS_DB_ID]
    assert elem_db.name == "Elements"
    assert elem_db.workspace == "qfwfq"
    assert elem_db.icon == "🧩"

    items_meta = get_database_meta("e74f045a-24ba-4f4e-acbc-1917636efaab")
    assert items_meta is not None
    assert items_meta.has_elements is True
    assert "Elements" in items_meta.properties


def test_elements_seeding_and_counts(client_and_engine):
    """Verify all 271 canonical Goods Elements are seeded and linked to Qfwfq."""
    _, engine = client_and_engine
    with Session(engine) as session:
        elements = session.exec(select(Element)).all()
        assert len(elements) >= 271

        # Check common elements
        sunglassess = session.exec(select(Element).where(Element.slug == "sunglassess")).first()
        assert sunglassess is not None
        assert sunglassess.section == "goods"

        sweater = session.exec(select(Element).where(Element.slug == "sweater")).first()
        assert sweater is not None

        sneakers = session.exec(select(Element).where(Element.slug == "sneakers")).first()
        assert sneakers is not None


def test_elements_payload_mapping():
    """Verify map_goods_payload maps element_notion_ids to Elements relation."""
    item = {"title": "Cashmere Knit Sweater", "url": "https://example.com/item"}
    payload = map_goods_payload(item, element_notion_ids=["el-page-123", "el-page-456"])
    assert "Elements" in payload
    assert payload["Elements"] == {
        "relation": [{"id": "el-page-123"}, {"id": "el-page-456"}]
    }

    dynamic = build_notion_payload(
        item,
        target_db_id="e74f045a-24ba-4f4e-acbc-1917636efaab",
        element_notion_ids=["el-page-789"],
    )
    assert "Elements" in dynamic
    assert dynamic["Elements"] == {"relation": [{"id": "el-page-789"}]}


def test_elements_api_crud_and_bulk_assign(client_and_engine):
    """Verify Elements REST endpoints and bulk assignment."""
    client, engine = client_and_engine
    res = client.get("/api/taxonomies/elements")
    assert res.status_code == 200
    data = res.json()
    assert len(data) >= 271

    # Create job & page
    with Session(engine) as session:
        job = ScrapeJob(target_url="https://example.com/catalog", title="Test Job")
        session.add(job)
        session.commit()
        session.refresh(job)

        page = Page(job_id=job.id, url="https://example.com/catalog/hoodie", title="Hoodie")
        session.add(page)
        session.commit()
        session.refresh(page)
        page_id = page.id

    el1 = data[0]["id"]
    el2 = data[1]["id"]

    bulk_res = client.post(
        "/api/taxonomies/elements/bulk-assign",
        json={"page_ids": [page_id], "element_ids": [el1, el2]},
    )
    assert bulk_res.status_code == 200
    assert bulk_res.json()["assigned_count"] == 2

    # Check page serialization includes elements
    p_res = client.patch(
        f"/api/pages/{page_id}/taxonomy",
        json={"element_ids": [el1]},
    )
    assert p_res.status_code == 200
    p_data = p_res.json()
    assert len(p_data["element_ids"]) == 1
    assert p_data["element_ids"][0] == el1


def test_batch_upload_element_prediction(client_and_engine):
    """Verify smart batch-upload predicts elements from URL tokens."""
    client, _ = client_and_engine
    res = client.post(
        "/api/jobs/batch-upload",
        data={"raw_text": "https://www.paulsmith.com/eu/orange-cotton-silk-ribbed-socks\nhttps://example.com/cashmere-sweater"},
    )
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) == 2

    # First item has 'socks'
    assert any("sock" in e.lower() for e in items[0]["predicted_elements"])
    assert len(items[0]["element_ids"]) > 0

    # Second item has 'sweater'
    assert any("sweater" in e.lower() for e in items[1]["predicted_elements"])
    assert len(items[1]["element_ids"]) > 0
