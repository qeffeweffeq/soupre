import os
from pathlib import Path
from unittest.mock import patch

from modules.notion.client import (
    discover_tokens,
    get_workspace_token,
    get_target_db_id,
    get_notion_client,
    FP_DATABASES,
    QFWFQ_DATABASES,
    SERVICES_DB_ID,
    GOODS_DB_ID,
)


def test_database_id_constants():
    assert SERVICES_DB_ID == "11ac7930-1516-807e-b685-d3fee70ae00d"
    assert GOODS_DB_ID == "d46f6dbf-310c-4c4d-800e-813eeba04ab6"
    assert FP_DATABASES["roles"] == "d74d19f8-6945-4eb0-8d40-e879f3b1d571"
    assert FP_DATABASES["types"] == "373c7930-1516-80df-9342-f3510c8bed9f"
    assert FP_DATABASES["tags"] == "11ac7930-1516-8066-8be3-f104b1285a9a"
    assert QFWFQ_DATABASES["items"] == "e74f045a-24ba-4f4e-acbc-1917636efaab"
    assert QFWFQ_DATABASES["type"] == "689933d7-5077-4f4f-bb2e-8541cd1dcff0"
    assert QFWFQ_DATABASES["shops"] == "15863158-bfa0-800a-8e34-e6462c16d17a"


def test_discover_tokens_from_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("NOTION_FP_TOKEN=secret_fp_123\nNOTION_QFWFQ_TOKEN=secret_qfwfq_456\nOTHER_KEY=ignore\n")

    with patch.dict(os.environ, {}, clear=True):
        tokens = discover_tokens(extra_paths=[env_file])
        assert tokens.get("NOTION_FP_TOKEN") == "secret_fp_123"
        assert tokens.get("NOTION_QFWFQ_TOKEN") == "secret_qfwfq_456"
        assert "OTHER_KEY" not in tokens


def test_workspace_token_resolution():
    with patch.dict(os.environ, {"NOTION_FP_TOKEN": "fp_token", "NOTION_QFWFQ_TOKEN": "qfwfq_token"}):
        assert get_workspace_token("fp") == "fp_token"
        assert get_workspace_token("FP") == "fp_token"
        assert get_workspace_token("services") == "fp_token"

        assert get_workspace_token("qfwfq") == "qfwfq_token"
        assert get_workspace_token("goods") == "qfwfq_token"

        # Full-site isolation: MUST return None
        assert get_workspace_token("full-site") is None
        assert get_workspace_token("fullsite") is None
        assert get_workspace_token("") is None


def test_target_db_id_resolution():
    assert get_target_db_id("fp") == SERVICES_DB_ID
    assert get_target_db_id("services") == SERVICES_DB_ID
    assert get_target_db_id("qfwfq") == GOODS_DB_ID
    assert get_target_db_id("goods") == GOODS_DB_ID
    assert get_target_db_id("full-site") is None
    assert get_target_db_id("fullsite") is None


def test_get_notion_client_factory():
    with patch.dict(os.environ, {"NOTION_FP_TOKEN": "secret_valid_token"}):
        client = get_notion_client("fp")
        assert client is not None
        assert hasattr(client, "databases")
        assert hasattr(client, "pages")

    # Full site always returns None
    assert get_notion_client("full-site") is None
    assert get_notion_client("fullsite") is None
