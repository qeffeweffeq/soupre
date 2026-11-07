import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select, create_engine, SQLModel
from sqlalchemy.pool import StaticPool

from database.models import Workspace, Category, Types, Tag, Page, PageTagLink, ScrapeJob, Element
from database.migrations import migrate_db
from database.seed_taxonomies import seed_taxonomies
from main import app
from database.session import get_session
from modules.rules import (
    TaxonomyInstructionRule,
    TaxonomyRuleContext,
    evaluate_taxonomy_rules,
    resolve_canonical_tag_in_db,
    get_all_rules,
)


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


def test_zero_creation_policy_and_synonym_resolution(client_and_session):
    client, engine = client_and_session

    with Session(engine) as session:
        ws_qfwfq = session.exec(select(Workspace).where(Workspace.slug == "qfwfq")).first()
        assert ws_qfwfq is not None

        # 1. Resolving existing tag by canonical name
        tag1 = resolve_canonical_tag_in_db("Ristrutturazione", ws_qfwfq.id, session)
        assert tag1 is not None
        assert tag1.name == "Ristrutturazione"
        assert tag1.slug == "ristrutturazione"

        # 2. Resolving existing tag via English synonym ("Renovation")
        tag2 = resolve_canonical_tag_in_db("Renovation", ws_qfwfq.id, session)
        assert tag2 is not None
        assert tag2.id == tag1.id

        # 3. Non-existent tag returns None and does NOT create a row in Tag table
        initial_tag_count = len(session.exec(select(Tag)).all())
        tag_missing = resolve_canonical_tag_in_db("Jeans", ws_qfwfq.id, session)
        assert tag_missing is None

        tag_missing2 = resolve_canonical_tag_in_db("Sunglasses", ws_qfwfq.id, session)
        assert tag_missing2 is None

        tag_missing3 = resolve_canonical_tag_in_db("ArbitraryNonExistentTag", ws_qfwfq.id, session)
        assert tag_missing3 is None

        after_tag_count = len(session.exec(select(Tag)).all())
        assert after_tag_count == initial_tag_count, "Zero-creation policy violated: Tag count increased!"


def test_renovation_instruction_rule_evaluator(client_and_session):
    client, engine = client_and_session

    with Session(engine) as session:
        ws_qfwfq = session.exec(select(Workspace).where(Workspace.slug == "qfwfq")).first()
        ws_fp = session.exec(select(Workspace).where(Workspace.slug == "fp")).first()
        assert ws_qfwfq is not None
        assert ws_fp is not None

        # Test Furniture in Qfwfq / Goods
        ctx_furniture = TaxonomyRuleContext(
            workspace_id=ws_qfwfq.id,
            workspace_slug="qfwfq",
            dashboard="goods",
            notion_db_name="Goods",
            type_name="Furniture",
        )
        res = evaluate_taxonomy_rules(ctx_furniture, session)
        assert len(res) == 1
        assert res[0].tag_name == "Ristrutturazione"
        assert res[0].rule_id == "goods-items-renovation"

        # Test Electricity in Qfwfq / Goods
        ctx_electricity = TaxonomyRuleContext(
            workspace_id=ws_qfwfq.id,
            workspace_slug="qfwfq",
            dashboard="goods",
            notion_db_name="Goods",
            type_name="Electricity",
        )
        res = evaluate_taxonomy_rules(ctx_electricity, session)
        assert len(res) == 1
        assert res[0].tag_name == "Ristrutturazione"

        # Test Tiles in Qfwfq / Goods
        ctx_tiles = TaxonomyRuleContext(
            workspace_id=ws_qfwfq.id,
            workspace_slug="qfwfq",
            dashboard="goods",
            notion_db_name="Goods",
            type_name="Tiles",
        )
        res = evaluate_taxonomy_rules(ctx_tiles, session)
        assert len(res) == 1
        assert res[0].tag_name == "Ristrutturazione"

        # Test Apparel in Qfwfq / Goods -> Should NOT match rule
        ctx_apparel = TaxonomyRuleContext(
            workspace_id=ws_qfwfq.id,
            workspace_slug="qfwfq",
            dashboard="goods",
            notion_db_name="Goods",
            type_name="Apparel",
        )
        res = evaluate_taxonomy_rules(ctx_apparel, session)
        assert len(res) == 0

        # Test Furniture in FP / Services -> Should NOT match rule (scoped to Qfwfq / Goods)
        ctx_fp = TaxonomyRuleContext(
            workspace_id=ws_fp.id,
            workspace_slug="fp",
            dashboard="services",
            notion_db_name="Services",
            type_name="Furniture",
        )
        res = evaluate_taxonomy_rules(ctx_fp, session)
        assert len(res) == 0


def test_batch_upload_strict_tag_matching(client_and_session):
    client, engine = client_and_session

    # Test Prada items (Jeans & Sunglasses elements, no phantom tags)
    res = client.post(
        "/api/jobs/batch-upload",
        data={
            "raw_text": (
                "https://www.prada.com/it/it/p/jeans-a-gamba-dritta/\n"
                "https://www.prada.com/it/it/p/occhiali-da-sole/\n"
                "https://www.ikea.com/it/it/p/kallax-scaffale-furniture/"
            )
        },
    )
    assert res.status_code == 200
    data = res.json()
    items = data["items"]
    assert len(items) == 3

    # Prada Jeans: has Element Jeans, but NO Tag Jeans
    prada_jeans = items[0]
    assert "Jeans" in prada_jeans["predicted_elements"]
    assert "Jeans" not in prada_jeans["predicted_tags"]
    assert len(prada_jeans["tag_ids"]) == 0

    # Prada Sunglasses: has Element Sunglassess, but NO Tag Sunglasses
    prada_glasses = items[1]
    assert any("sunglass" in e.lower() for e in prada_glasses["predicted_elements"])
    assert "Sunglasses" not in prada_glasses["predicted_tags"]
    assert len(prada_glasses["tag_ids"]) == 0

    # IKEA Kallax Furniture: Rule triggers and auto-applies Ristrutturazione canonical tag
    ikea_item = items[2]
    assert ikea_item["predicted_types_name"] == "Furniture"
    assert ikea_item["workspace_name"] == "Qfwfq"
    assert "Ristrutturazione" in ikea_item["predicted_tags"]
    assert len(ikea_item["tag_ids"]) >= 1
    assert any("Renovation" in r or "Ristrutturazione" in r for r in ikea_item.get("rule_applied_tags", []))


def test_taxonomy_rules_api_endpoints(client_and_session):
    client, engine = client_and_session

    # 1. GET /api/taxonomies/rules
    res = client.get("/api/taxonomies/rules")
    assert res.status_code == 200
    rules = res.json()
    assert isinstance(rules, list)
    assert any(r["id"] == "goods-items-renovation" for r in rules)

    # 2. POST /api/taxonomies/rules/apply on pages in DB
    with Session(engine) as session:
        ws_qfwfq = session.exec(select(Workspace).where(Workspace.slug == "qfwfq")).first()
        t_furniture = session.exec(select(Types).where(Types.name == "Furniture")).first()
        t_apparel = session.exec(select(Types).where(Types.name == "Apparel")).first()
        assert ws_qfwfq is not None
        assert t_furniture is not None
        assert t_apparel is not None

        job = ScrapeJob(target_url="https://example.com", workspace_id=ws_qfwfq.id)
        session.add(job)
        session.commit()
        session.refresh(job)

        page1 = Page(
            job_id=job.id,
            url="https://example.com/furniture-table",
            clean_url="https://example.com/furniture-table",
            title="Furniture Table",
            workspace_id=ws_qfwfq.id,
            types_id=t_furniture.id,
            status="done",
        )
        page2 = Page(
            job_id=job.id,
            url="https://example.com/apparel-jacket",
            clean_url="https://example.com/apparel-jacket",
            title="Apparel Jacket",
            workspace_id=ws_qfwfq.id,
            types_id=t_apparel.id,
            status="done",
        )
        session.add(page1)
        session.add(page2)
        session.commit()
        session.refresh(page1)
        session.refresh(page2)

        ws_qfwfq_id = ws_qfwfq.id
        page1_id = page1.id
        page2_id = page2.id

    # Apply rules
    apply_res = client.post(f"/api/taxonomies/rules/apply?workspace_id={ws_qfwfq_id}")
    assert apply_res.status_code == 200
    apply_data = apply_res.json()
    assert apply_data["status"] == "success"
    assert apply_data["links_added"] >= 1

    # Verify that page1 has Ristrutturazione tag linked, and page2 does not
    with Session(engine) as session:
        p1 = session.get(Page, page1_id)
        p2 = session.get(Page, page2_id)
        p1_tag_names = [t.name for t in p1.tags]
        p2_tag_names = [t.name for t in p2.tags]

        assert "Ristrutturazione" in p1_tag_names
        assert "Ristrutturazione" not in p2_tag_names
