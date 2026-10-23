"""
tests/test_taxonomy_ui_api.py
Unit tests for Taxonomy UI API endpoints:
- GET /api/jobs/{job_id}/pages (enriched with taxonomy names and slugs)
- PATCH /api/pages/{page_id}/taxonomy (manual user reclassification)
- GET /api/taxonomies/explorer (hierarchical folder/subfolder tree with pages)
"""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine, select
from sqlalchemy.pool import StaticPool
from database.models import SQLModel, ScrapeJob, Page, Category, Types, Genre, Tag
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


def test_get_job_pages_enriched_taxonomy(client):
    with Session(test_engine) as session:
        job = ScrapeJob(target_url="https://prada.com", title="Prada Scrape", status="completed")
        cat = Category(name="Goods & Commerce", slug="goods-commerce")
        session.add_all([job, cat])
        session.commit()
        session.refresh(job)
        session.refresh(cat)

        t = Types(category_id=cat.id, name="Shop", slug="shop")
        genre = Genre(name="E-Commerce", slug="e-commerce")
        tag = Tag(name="Eyewear", slug="eyewear")
        session.add_all([t, genre, tag])
        session.commit()
        session.refresh(t)
        session.refresh(genre)
        session.refresh(tag)

        page = Page(
            job_id=job.id,
            url="https://prada.com/sunglasses",
            title="Prada Sunglasses",
            category_id=cat.id,
            types_id=t.id,
            genre_id=genre.id,
            status="completed",
            categorization_source="consensus",
            categorization_confidence=1.0,
        )
        page.tags = [tag]
        session.add(page)
        session.commit()
        session.refresh(page)
        job_id = job.id

    resp = client.get(f"/api/jobs/{job_id}/pages")
    assert resp.status_code == 200
    pages = resp.json()
    assert len(pages) == 1
    p = pages[0]
    assert p["title"] == "Prada Sunglasses"
    assert p["category_name"] == "Goods & Commerce"
    assert p["category_slug"] == "goods-commerce"
    assert p["types_name"] == "Shop"
    assert p["types_slug"] == "shop"
    assert p["genre_name"] == "E-Commerce"
    assert p["tag_names"] == ["Eyewear"]
    assert p["categorization_source"] == "consensus"
    assert p["categorization_confidence"] == 1.0


def test_patch_page_taxonomy_manual_reclassification(client):
    with Session(test_engine) as session:
        job = ScrapeJob(target_url="https://example.com", title="Test Job")
        cat1 = Category(name="Design", slug="design")
        cat2 = Category(name="Writing", slug="writing")
        session.add_all([job, cat1, cat2])
        session.commit()
        session.refresh(job)
        session.refresh(cat1)
        session.refresh(cat2)

        type2 = Types(category_id=cat2.id, name="Articles", slug="articles")
        tag1 = Tag(name="Inspiration", slug="inspiration")
        session.add_all([type2, tag1])
        session.commit()
        session.refresh(type2)
        session.refresh(tag1)

        page = Page(
            job_id=job.id,
            url="https://example.com/blog/1",
            title="Design Blog Post",
            category_id=cat1.id,
            status="completed",
            categorization_source="jev",
            categorization_confidence=0.67,
        )
        session.add(page)
        session.commit()
        session.refresh(page)
        page_id = page.id
        cat2_id = cat2.id
        type2_id = type2.id
        tag1_id = tag1.id

    # Reclassify page to cat2, type2, and tag1
    payload = {
        "category_id": cat2_id,
        "types_id": type2_id,
        "tag_ids": [tag1_id],
    }
    resp = client.patch(f"/api/pages/{page_id}/taxonomy", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    assert data["category_id"] == cat2_id
    assert data["category_name"] == "Writing"
    assert data["types_id"] == type2_id
    assert data["types_name"] == "Articles"
    assert data["tag_names"] == ["Inspiration"]
    assert data["categorization_source"] == "user_manual"
    assert data["categorization_confidence"] == 1.0

    # Verify persistence in database
    with Session(test_engine) as session:
        db_page = session.get(Page, page_id)
        assert db_page.category_id == cat2_id
        assert db_page.types_id == type2_id
        assert db_page.categorization_source == "user_manual"
        assert db_page.categorization_confidence == 1.0
        assert len(db_page.tags) == 1
        assert db_page.tags[0].name == "Inspiration"


def test_patch_page_taxonomy_404_cases(client):
    # Non-existent page
    resp = client.patch("/api/pages/99999/taxonomy", json={"category_id": 1})
    assert resp.status_code == 404

    # Existing page, non-existent category
    with Session(test_engine) as session:
        job = ScrapeJob(target_url="https://example.com", title="Job")
        session.add(job)
        session.commit()
        session.refresh(job)

        page = Page(job_id=job.id, url="https://example.com/p", title="P")
        session.add(page)
        session.commit()
        session.refresh(page)
        page_id = page.id

    resp = client.patch(f"/api/pages/{page_id}/taxonomy", json={"category_id": 88888})
    assert resp.status_code == 404


def test_get_taxonomies_explorer_hierarchy(client):
    with Session(test_engine) as session:
        job = ScrapeJob(target_url="https://brand.com", title="Brand")
        cat_shop = Category(name="Goods & Commerce", slug="goods-commerce")
        cat_blog = Category(name="Writing", slug="writing")
        session.add_all([job, cat_shop, cat_blog])
        session.commit()
        session.refresh(job)
        session.refresh(cat_shop)
        session.refresh(cat_blog)

        type_apparel = Types(category_id=cat_shop.id, name="Apparel", slug="apparel")
        type_eyewear = Types(category_id=cat_shop.id, name="Eyewear", slug="eyewear")
        type_news = Types(category_id=cat_blog.id, name="News", slug="news")
        session.add_all([type_apparel, type_eyewear, type_news])
        session.commit()
        session.refresh(type_apparel)
        session.refresh(type_eyewear)
        session.refresh(type_news)

        # Page 1: in Apparel
        p1 = Page(
            job_id=job.id,
            url="https://brand.com/jeans",
            title="Denim Jeans",
            category_id=cat_shop.id,
            types_id=type_apparel.id,
            status="completed",
        )
        # Page 2: in Eyewear
        p2 = Page(
            job_id=job.id,
            url="https://brand.com/glasses",
            title="Black Glasses",
            category_id=cat_shop.id,
            types_id=type_eyewear.id,
            status="completed",
        )
        # Page 3: in Goods & Commerce but untyped
        p3 = Page(
            job_id=job.id,
            url="https://brand.com/promo",
            title="Holiday Promo",
            category_id=cat_shop.id,
            types_id=None,
            status="completed",
        )
        # Page 4: unassigned
        p4 = Page(
            job_id=job.id,
            url="https://brand.com/unknown",
            title="Unknown Page",
            category_id=None,
            types_id=None,
            status="completed",
        )
        session.add_all([p1, p2, p3, p4])
        session.commit()

    resp = client.get("/api/taxonomies/explorer")
    assert resp.status_code == 200
    tree = resp.json()

    # Find Goods & Commerce
    goods_cat = next((c for c in tree if c["slug"] == "goods-commerce"), None)
    assert goods_cat is not None
    assert goods_cat["page_count"] == 3  # 2 typed + 1 untyped

    types_map = {t["name"]: t for t in goods_cat["types"]}
    assert "Apparel" in types_map
    assert types_map["Apparel"]["page_count"] == 1
    assert types_map["Apparel"]["pages"][0]["title"] == "Denim Jeans"

    assert "Eyewear" in types_map
    assert types_map["Eyewear"]["page_count"] == 1
    assert types_map["Eyewear"]["pages"][0]["title"] == "Black Glasses"

    # Untyped subfolder
    assert "General / Other" in types_map
    assert types_map["General / Other"]["page_count"] == 1
    assert types_map["General / Other"]["pages"][0]["title"] == "Holiday Promo"

    # Find Uncategorized category
    uncat = next((c for c in tree if c["slug"] == "uncategorized"), None)
    assert uncat is not None
    assert uncat["page_count"] == 1
    assert uncat["types"][0]["pages"][0]["title"] == "Unknown Page"
