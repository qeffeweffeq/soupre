import pytest
from sqlmodel import SQLModel, create_engine, Session, select

from database.models import Page, ScrapeJob, Category, Types, Tag, Genre, PageTagLink
from database.seed_taxonomies import seed_taxonomies
from modules.taxonomies.jev_matcher import match_taxonomy_values, load_taxonomy
from modules.embeddings.vector_store import compute_similarity, find_similar
from modules.embeddings.taxonomy_embeddings import (
    get_taxonomy_embeddings,
    match_category_embedding,
    match_type_embedding,
)
from modules.enrichment.classifier import classify_page


def test_jev_matcher_resolution():
    tax = load_taxonomy("services")
    assert "Roles" in tax
    assert "Types" in tax

    matched_roles = match_taxonomy_values("services", "Roles", ["tech", "developer", "code"])
    assert "Technology" in matched_roles

    matched_types = match_taxonomy_values("services", "Types", ["app", "plugin", "util"])
    assert "Application" in matched_types
    assert "Plugin" in matched_types
    assert "Utils" in matched_types


def test_vector_store_similarity():
    v1 = [1.0, 0.0, 0.0]
    v2 = [1.0, 0.0, 0.0]
    v3 = [0.0, 1.0, 0.0]

    assert compute_similarity(v1, v2) == pytest.approx(1.0)
    assert compute_similarity(v1, v3) == pytest.approx(0.0)

    cache = {"item1": [1.0, 0.0, 0.0], "item2": [0.7, 0.7, 0.0]}
    similar = find_similar([1.0, 0.0, 0.0], cache)
    assert len(similar) == 2
    assert similar[0][0] == "item1"
    assert similar[0][1] >= similar[1][1]


def test_taxonomy_embeddings_store():
    store = get_taxonomy_embeddings()
    assert "categories" in store
    assert "types" in store
    assert len(store["categories"]) >= 15
    assert len(store["types"]) >= 20

    cat_matches = match_category_embedding("Cloud database and API web framework")
    assert len(cat_matches) > 0
    assert cat_matches[0][0] == "Technology"


def test_classifier_synthetic_pages():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        seed_taxonomies(session)

        # 1. Tech SaaS
        res_tech = classify_page(
            title="FastAPI Cloud Platform",
            description="High performance python REST API backend with automated database deployments.",
            body_text="Deploy scalable microservices, postgres databases, and developer APIs in seconds.",
            url="https://fastapi.cloud",
            db_session=session,
        )
        assert res_tech.category == "Technology"
        assert res_tech.category_id is not None
        assert res_tech.source in ("consensus", "jev")
        assert res_tech.confidence >= 0.80

        # 2. Creative Agency
        res_creative = classify_page(
            title="Pentagram Design Consultancy",
            description="Multi-disciplinary design studio specializing in brand identity, typography, and architecture.",
            body_text="We craft visual identities, publications, and digital experiences for leading cultural institutions.",
            url="https://pentagram.com",
            db_session=session,
        )
        assert res_creative.category in ("Design", "Creativity")
        assert res_creative.category_id is not None

        # 3. Ecommerce Shop
        res_shop = classify_page(
            title="Acme Streetwear Online Store",
            description="Shop premium hoodies, streetwear apparel, and accessories with free shipping.",
            body_text="Browse our new seasonal apparel collection. Fast checkout and global express delivery on all merchandise.",
            url="https://acmeshop.com/store",
            db_session=session,
        )
        assert res_shop.category == "Goods & Commerce"
        assert res_shop.type == "Shop"
        assert res_shop.category_id is not None
        assert res_shop.types_id is not None
