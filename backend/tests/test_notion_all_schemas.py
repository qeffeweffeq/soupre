import pytest
from fastapi.testclient import TestClient
from main import app
from modules.notion.databases import list_available_databases, get_database_meta
from modules.notion.mapper import build_notion_payload


def test_list_available_databases_discovers_all_fp_and_qfwfq_databases():
    all_dbs = list_available_databases()
    assert len(all_dbs) >= 19

    fp_dbs = list_available_databases(workspace="fp")
    fp_names = [d["name"] for d in fp_dbs]
    expected_fp = [
        "Services",
        "Creatives",
        "Tasks",
        "Clients",
        "Projects",
        "Notes",
        "Media",
        "Website",
        "Prompt Templates",
        "Documents, Proposals & Reports",
        "Personas",
        "Global Keywords",
    ]
    for exp in expected_fp:
        assert exp in fp_names, f"Missing FP DB: {exp}"

    qfwfq_dbs = list_available_databases(workspace="qfwfq")
    qfwfq_names = [d["name"] for d in qfwfq_dbs]
    expected_qfwfq = [
        "Goods (Items)",
        "Shops",
        "Brands",
        "Return Points",
        "Costs",
        "Pinned",
        "Trips",
    ]
    for exp in expected_qfwfq:
        assert exp in qfwfq_names, f"Missing Qfwfq DB: {exp}"


def test_full_site_workspace_is_isolated_from_notion_databases():
    # Full-site must strictly return 0 databases
    assert list_available_databases(workspace="full-site") == []
    assert list_available_databases(workspace="fullsite") == []


def test_notion_databases_api_endpoint():
    client = TestClient(app)
    resp = client.get("/api/notion/databases")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] >= 19
    assert len(data["databases"]) >= 19

    # Filter by workspace
    fp_resp = client.get("/api/notion/databases?workspace=fp")
    assert fp_resp.status_code == 200
    assert fp_resp.json()["count"] >= 12
    for db in fp_resp.json()["databases"]:
        assert db["workspace"] == "fp"

    # Full site
    fs_resp = client.get("/api/notion/databases?workspace=full-site")
    assert fs_resp.status_code == 200
    assert fs_resp.json()["count"] == 0


def test_dynamic_payload_builder_for_different_schemas():
    item = {
        "title": "Creative Studio Lookbook",
        "url": "https://creativestudio.com",
        "description": "Visual direction and branding agency",
    }

    # 1. Creatives DB (has Name, URL, Tags, Type, Description)
    creatives_db = get_database_meta("00ec9a41-3957-4b5d-91b5-3d586e331405")
    assert creatives_db is not None
    payload_creatives = build_notion_payload(
        item,
        target_db_id=creatives_db.id,
        type_notion_id="type-uuid-1",
        tag_notion_ids=["tag-uuid-a", "tag-uuid-b"],
    )
    assert payload_creatives["Name"]["title"][0]["text"]["content"] == "Creative Studio Lookbook"
    assert payload_creatives["URL"]["url"] == "https://creativestudio.com"
    assert "Tags" in payload_creatives
    assert len(payload_creatives["Tags"]["relation"]) == 2
    assert "Type" in payload_creatives

    # 2. Goods / Items DB (has Name, Url 1, Categories, Type, Tags, Price, Description, Buy Now)
    goods_db = get_database_meta("e74f045a-24ba-4f4e-acbc-1917636efaab")
    assert goods_db is not None
    goods_item = {
        "title": "Sneakers 2026",
        "url": "https://store.com/sneakers",
        "price": 149.99,
        "buy_now": "https://store.com/checkout",
        "description": "Comfortable leather sneakers",
    }
    payload_goods = build_notion_payload(
        goods_item,
        target_db_id=goods_db.id,
        category_notion_id="cat-uuid-1",
        tag_notion_ids=["tag-uuid-1"],
    )
    assert payload_goods["Name"]["title"][0]["text"]["content"] == "Sneakers 2026"
    assert payload_goods["Url 1"]["url"] == "https://store.com/sneakers"
    assert payload_goods["Price"]["number"] == 149.99
    assert payload_goods["Buy Now"]["url"] == "https://store.com/checkout"
    assert "Categories" in payload_goods
    assert "Tags" in payload_goods
