import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select, create_engine, SQLModel
from sqlalchemy.pool import StaticPool

from database.models import Workspace, Category, Types, Tag, Page, PageTagLink, ScrapeJob
from database.migrations import migrate_db
from database.seed_taxonomies import seed_taxonomies
from main import app
from database.session import get_session


@pytest.fixture(name="client_and_session")
def client_and_session_fixture():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    migrate_db(engine)

    def get_session_override():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = get_session_override

    with Session(engine) as session:
        seed_taxonomies(session)

    with TestClient(app) as client:
        yield client, engine

    app.dependency_overrides.clear()


def test_tag_model_and_migration():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    migrate_db(engine)

    with Session(engine) as session:
        ws = session.exec(select(Workspace).where(Workspace.slug == "fp")).first()
        assert ws is not None

        tag = Tag(name="Test AI", slug="test-ai", workspace_id=ws.id, section="services", notion_id="tag-notion-123")
        session.add(tag)
        session.commit()
        session.refresh(tag)

        assert tag.id is not None
        assert tag.workspace_id == ws.id
        assert tag.section == "services"
        assert tag.notion_id == "tag-notion-123"
        assert tag.workspace.slug == "fp"


def test_seed_taxonomies_seeds_canonical_tags():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    migrate_db(engine)

    with Session(engine) as session:
        res = seed_taxonomies(session)
        assert res["tags"] >= 100
        all_tags = session.exec(select(Tag)).all()
        assert len(all_tags) >= 100

        # Idempotence check
        res2 = seed_taxonomies(session)
        assert res2["tags"] == 0


def test_list_tags_with_workspace_filter_and_page_count(client_and_session):
    client, engine = client_and_session

    with Session(engine) as session:
        ws_fp = session.exec(select(Workspace).where(Workspace.slug == "fp")).first()
        ws_id = ws_fp.id
        tag = session.exec(select(Tag).where(Tag.workspace_id == ws_id)).first()
        assert tag is not None
        tag_id = tag.id

        job = ScrapeJob(target_url="https://example.com", workspace_id=ws_id)
        session.add(job)
        session.commit()
        session.refresh(job)

        page = Page(job_id=job.id, url="https://example.com/item", title="Item 1", workspace_id=ws_id)
        session.add(page)
        session.commit()
        session.refresh(page)

        link = PageTagLink(page_id=page.id, tag_id=tag_id)
        session.add(link)
        session.commit()

    # Query tags for workspace
    resp = client.get(f"/api/taxonomies/tags?workspace_id={ws_id}")
    assert resp.status_code == 200
    tags_data = resp.json()
    assert len(tags_data) > 0

    found_tag = next((t for t in tags_data if t["id"] == tag_id), None)
    assert found_tag is not None
    assert found_tag["page_count"] == 1


def test_create_and_update_tag_with_workspace(client_and_session):
    client, engine = client_and_session

    with Session(engine) as session:
        ws_qfwfq = session.exec(select(Workspace).where(Workspace.slug == "qfwfq")).first()
        ws_id = ws_qfwfq.id

    # Create Tag
    create_resp = client.post("/api/taxonomies/tags", json={
        "name": "Custom Sneaker Tag",
        "slug": "custom-sneaker-tag",
        "workspace_id": ws_id,
        "section": "goods",
        "notion_id": "notion-sneaker-uuid",
    })
    assert create_resp.status_code == 200
    created = create_resp.json()
    tag_id = created["id"]
    assert created["workspace_id"] == ws_id
    assert created["section"] == "goods"
    assert created["notion_id"] == "notion-sneaker-uuid"

    # Update Tag
    update_resp = client.put(f"/api/taxonomies/tags/{tag_id}", json={
        "name": "Updated Sneaker Tag",
        "section": "goods",
    })
    assert update_resp.status_code == 200
    updated = update_resp.json()
    assert updated["name"] == "Updated Sneaker Tag"


def test_bulk_assign_tags_api(client_and_session):
    client, engine = client_and_session

    with Session(engine) as session:
        job = ScrapeJob(target_url="https://bulktest.com")
        session.add(job)
        session.commit()
        session.refresh(job)

        p1 = Page(job_id=job.id, url="https://bulktest.com/1", title="Page 1")
        p2 = Page(job_id=job.id, url="https://bulktest.com/2", title="Page 2")
        session.add(p1)
        session.add(p2)
        session.commit()
        session.refresh(p1)
        session.refresh(p2)

        tags = session.exec(select(Tag).limit(2)).all()
        assert len(tags) == 2
        tag_ids = [t.id for t in tags]
        p1_id, p2_id = p1.id, p2.id

    resp = client.post("/api/taxonomies/tags/bulk-assign", json={
        "page_ids": [p1_id, p2_id],
        "tag_ids": tag_ids,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["assigned_count"] == 4
    assert data["pages_affected"] == 2

    # Idempotence: re-assigning should add 0 new links
    resp2 = client.post("/api/taxonomies/tags/bulk-assign", json={
        "page_ids": [p1_id, p2_id],
        "tag_ids": tag_ids,
    })
    assert resp2.status_code == 200
    assert resp2.json()["assigned_count"] == 0


def test_batch_upload_auto_tagging(client_and_session):
    client, _ = client_and_session

    content = "https://brand.com/products/sunglasses-luxury-model\nhttps://hotelresort.com/luxury-beach-hotel\n"
    resp = client.post("/api/jobs/batch-upload", json={"raw_text": content})
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 2

    item0 = data["items"][0]
    assert len(item0["tag_ids"]) > 0 or len(item0["predicted_tags"]) > 0
    assert any("sunglasses" in t.lower() or "eyewear" in t.lower() or "accessories" in t.lower() or "luxury" in t.lower() for t in item0["predicted_tags"])

    item1 = data["items"][1]
    assert len(item1["tag_ids"]) > 0 or len(item1["predicted_tags"]) > 0
    assert any("hotel" in t.lower() or "resort" in t.lower() or "beach" in t.lower() or "luxury" in t.lower() for t in item1["predicted_tags"])
