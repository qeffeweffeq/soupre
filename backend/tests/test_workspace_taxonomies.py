"""
tests/test_workspace_taxonomies.py
Unit tests for Notion-style Workspace taxonomy hierarchy:
- Workspace CRUD endpoints (/api/taxonomies/workspaces)
- Category workspace linking and CRUD (/api/taxonomies/categories)
- Tree and explorer filtering by workspace (/api/taxonomies/tree, /api/taxonomies/explorer)
- Job and page workspace cascading (/api/jobs/{id}/taxonomy, /api/pages/{id}/taxonomy)
"""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine, select
from sqlalchemy.pool import StaticPool
from database.models import SQLModel, Workspace, Category, Types, ScrapeJob, Page
import main
from main import app, get_session

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

    client = TestClient(app)
    yield client

    app.dependency_overrides.clear()
    main.engine = orig_engine
    SQLModel.metadata.drop_all(test_engine)


def test_workspace_crud_lifecycle(client):
    # 1. Create a workspace
    create_resp = client.post("/api/taxonomies/workspaces", json={
        "name": "Design Lab",
        "slug": "design-lab",
        "icon": "🎨",
        "color": "pink",
        "description": "Design research and UI experiments",
    })
    assert create_resp.status_code == 200
    created = create_resp.json()
    assert created["id"] is not None
    assert created["name"] == "Design Lab"
    assert created["slug"] == "design-lab"
    assert created["icon"] == "🎨"
    assert created["color"] == "pink"
    ws_id = created["id"]

    # 2. List workspaces
    list_resp = client.get("/api/taxonomies/workspaces")
    assert list_resp.status_code == 200
    workspaces = list_resp.json()
    assert any(w["id"] == ws_id for w in workspaces)

    # 3. Update workspace
    update_resp = client.put(f"/api/taxonomies/workspaces/{ws_id}", json={
        "name": "Creative Studio",
        "color": "mauve",
    })
    assert update_resp.status_code == 200
    updated = update_resp.json()
    assert updated["name"] == "Creative Studio"
    assert updated["color"] == "mauve"

    # 4. Delete workspace
    del_resp = client.delete(f"/api/taxonomies/workspaces/{ws_id}")
    assert del_resp.status_code == 200
    assert del_resp.json()["status"] == "deleted"

    # Verify deleted
    get_del = client.put(f"/api/taxonomies/workspaces/{ws_id}", json={"name": "Ghost"})
    assert get_del.status_code == 404


def test_explorer_and_tree_filtering_by_workspace(client):
    with Session(test_engine) as session:
        ws_fp = Workspace(name="FP", slug="fp", icon="⚡", color="mauve")
        ws_qfwfq = Workspace(name="Qfwfq", slug="qfwfq", icon="🪐", color="teal")
        session.add_all([ws_fp, ws_qfwfq])
        session.commit()
        session.refresh(ws_fp)
        session.refresh(ws_qfwfq)

        cat_tech = Category(name="Technology", slug="technology", section="services", workspace_id=ws_fp.id)
        cat_street = Category(name="Street", slug="street", section="goods", workspace_id=ws_qfwfq.id)
        session.add_all([cat_tech, cat_street])
        session.commit()
        session.refresh(cat_tech)
        session.refresh(cat_street)

        type_app = Types(name="Application", slug="application", category_id=cat_tech.id)
        type_apparel = Types(name="Apparel", slug="apparel", category_id=cat_street.id)
        session.add_all([type_app, type_apparel])
        session.commit()

        job1 = ScrapeJob(target_url="https://github.com", workspace_id=ws_fp.id, category_id=cat_tech.id)
        job2 = ScrapeJob(target_url="https://prada.com", workspace_id=ws_qfwfq.id, category_id=cat_street.id)
        session.add_all([job1, job2])
        session.commit()
        session.refresh(job1)
        session.refresh(job2)

        page1 = Page(job_id=job1.id, url="https://github.com", title="GitHub", workspace_id=ws_fp.id, category_id=cat_tech.id, types_id=type_app.id)
        page2 = Page(job_id=job2.id, url="https://prada.com", title="Prada", workspace_id=ws_qfwfq.id, category_id=cat_street.id, types_id=type_apparel.id)
        session.add_all([page1, page2])
        session.commit()

        ws_fp_id = ws_fp.id
        ws_qfwfq_id = ws_qfwfq.id

    # 1. Test explorer without workspace filter (returns all)
    all_resp = client.get("/api/taxonomies/explorer")
    assert all_resp.status_code == 200
    all_data = all_resp.json()
    cat_names = [c["name"] for c in all_data if c["id"] is not None]
    assert "Technology" in cat_names
    assert "Street" in cat_names

    # 2. Test explorer filtered by FP workspace ID
    fp_resp = client.get(f"/api/taxonomies/explorer?workspace_id={ws_fp_id}")
    assert fp_resp.status_code == 200
    fp_data = fp_resp.json()
    fp_cat_names = [c["name"] for c in fp_data if c["id"] is not None]
    assert "Technology" in fp_cat_names
    assert "Street" not in fp_cat_names
    # Check attached workspace metadata
    tech_node = next(c for c in fp_data if c["name"] == "Technology")
    assert tech_node["workspace_slug"] == "fp"
    assert tech_node["workspace_icon"] == "⚡"

    # 3. Test explorer filtered by Qfwfq slug
    qfwfq_resp = client.get("/api/taxonomies/explorer?workspace_id=qfwfq")
    assert qfwfq_resp.status_code == 200
    qfwfq_data = qfwfq_resp.json()
    qfwfq_cat_names = [c["name"] for c in qfwfq_data if c["id"] is not None]
    assert "Street" in qfwfq_cat_names
    assert "Technology" not in qfwfq_cat_names

    # 4. Test tree filtered by workspace
    tree_fp = client.get(f"/api/taxonomies/tree?workspace_id={ws_fp_id}").json()
    assert len(tree_fp) == 1
    assert tree_fp[0]["name"] == "Technology"


def test_job_workspace_cascade_to_pages(client):
    with Session(test_engine) as session:
        ws_fp = Workspace(name="FP", slug="fp", icon="⚡", color="mauve")
        ws_qfwfq = Workspace(name="Qfwfq", slug="qfwfq", icon="🪐", color="teal")
        session.add_all([ws_fp, ws_qfwfq])
        session.commit()
        session.refresh(ws_fp)
        session.refresh(ws_qfwfq)

        cat_tech = Category(name="Technology", slug="technology", section="services", workspace_id=ws_fp.id)
        session.add(cat_tech)
        session.commit()
        session.refresh(cat_tech)

        job = ScrapeJob(target_url="https://anthropic.com", title="Anthropic")
        session.add(job)
        session.commit()
        session.refresh(job)

        page = Page(job_id=job.id, url="https://anthropic.com", title="Anthropic Home")
        session.add(page)
        session.commit()
        session.refresh(page)

        ws_fp_id = ws_fp.id
        cat_tech_id = cat_tech.id
        job_id = job.id

    # Update job category to Technology (which belongs to FP workspace)
    resp = client.put(f"/api/jobs/{job_id}/taxonomy", json={"category_id": cat_tech_id})
    assert resp.status_code == 200
    res_data = resp.json()
    # Check job updated
    assert res_data["job"]["category_name"] == "Technology"
    assert res_data["job"]["workspace_id"] == ws_fp_id
    assert res_data["job"]["workspace_slug"] == "fp"
    # Check pages cascaded
    assert len(res_data["pages"]) == 1
    assert res_data["pages"][0]["workspace_id"] == ws_fp_id
    assert res_data["pages"][0]["workspace_name"] == "FP"
