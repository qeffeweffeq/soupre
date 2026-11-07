"""
Notion client initialization and token discovery module.
Discovers Notion API tokens across candidate .env locations and creates
authenticated notion-client instances for target workspaces.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from notion_client import Client

logger = logging.getLogger(__name__)

# Notion Database IDs
FP_DATABASES = {
    "services": "11ac7930-1516-807e-b685-d3fee70ae00d",
    "creatives": "00ec9a41-3957-4b5d-91b5-3d586e331405",
    "tasks": "11ac7930-1516-8025-a4b5-ca31792a7f5a",
    "clients": "11ac7930-1516-805d-af7f-fa08191ec485",
    "projects": "11ac7930-1516-80a5-a0ae-e7d6fa6a9d70",
    "notes": "11ac7930-1516-80fe-9f37-d204a11f26f6",
    "media": "fa29eb81-8ac4-4c64-858f-018a70844124",
    "website": "a4fcf8ca-fa5c-4e89-9dbf-19149fe8e698",
    "prompt-templates": "11ac7930-1516-8095-a4b4-d7d45eb22197",
    "documents-proposals-reports": "11ac7930-1516-8041-86e5-da7c2657e237",
    "personas": "11ac7930-1516-8040-af25-ec98fb1bfa50",
    "global-keywords": "270aabfa-9126-459d-93e9-cb8d894347fd",
    "roles": "d74d19f8-6945-4eb0-8d40-e879f3b1d571",
    "types": "373c7930-1516-80df-9342-f3510c8bed9f",
    "tags": "11ac7930-1516-8066-8be3-f104b1285a9a",
    "creatives_tags": "79812c03-a4c0-4ff8-9fba-0aa31f9494e2",
    "palace_lido_tags": "30dc7930-1516-81fe-af33-d26fa33824bb",
}

QFWFQ_DATABASES = {
    "goods": "d46f6dbf-310c-4c4d-800e-813eeba04ab6",
    "items": "e74f045a-24ba-4f4e-acbc-1917636efaab",
    "shops": "15863158-bfa0-800a-8e34-e6462c16d17a",
    "brands": "239e2e28-a234-4106-9ce7-ed737e9e1669",
    "return-points": "17e63158-bfa0-808e-aede-d06ca3cf0c74",
    "costs": "fa48d3d7-7e88-48f1-a589-a54d63fd4264",
    "pinned": "a2d3d887-0ccc-4c3e-a67d-5a5a00750c5a",
    "trips": "fd7f7270-28b3-41be-a460-94671e024747",
    "type": "689933d7-5077-4f4f-bb2e-8541cd1dcff0",
    "elements": "41163158-bfa0-8110-9ec1-e4c34e3a9eec",
    "tags": "2208539a-2cae-4e6f-b1a0-7bf00e14ac7a",
    "palace_lido_tags": "16f63158-bfa0-819b-a7ea-e4823cc42c68",
}

# Constant Aliases
SERVICES_DB_ID = FP_DATABASES["services"]
CREATIVES_DB_ID = FP_DATABASES["creatives"]
TASKS_DB_ID = FP_DATABASES["tasks"]
CLIENTS_DB_ID = FP_DATABASES["clients"]
PROJECTS_DB_ID = FP_DATABASES["projects"]
NOTES_DB_ID = FP_DATABASES["notes"]
MEDIA_DB_ID = FP_DATABASES["media"]
WEBSITE_DB_ID = FP_DATABASES["website"]
PROMPT_TEMPLATES_DB_ID = FP_DATABASES["prompt-templates"]
DOCUMENTS_DB_ID = FP_DATABASES["documents-proposals-reports"]
PERSONAS_DB_ID = FP_DATABASES["personas"]
GLOBAL_KEYWORDS_DB_ID = FP_DATABASES["global-keywords"]
ROLES_DB_ID = FP_DATABASES["roles"]
FP_TYPES_DB_ID = FP_DATABASES["types"]
FP_TAGS_DB_ID = FP_DATABASES["tags"]
FP_CREATIVES_TAGS_DB_ID = FP_DATABASES["creatives_tags"]
FP_PALACE_LIDO_TAGS_DB_ID = FP_DATABASES["palace_lido_tags"]

GOODS_DB_ID = QFWFQ_DATABASES["goods"]
ITEMS_DB_ID = QFWFQ_DATABASES["items"]
SHOPS_DB_ID = QFWFQ_DATABASES["shops"]
BRANDS_DB_ID = QFWFQ_DATABASES["brands"]
RETURN_POINTS_DB_ID = QFWFQ_DATABASES["return-points"]
COSTS_DB_ID = QFWFQ_DATABASES["costs"]
PINNED_DB_ID = QFWFQ_DATABASES["pinned"]
TRIPS_DB_ID = QFWFQ_DATABASES["trips"]
QFWFQ_TYPE_DB_ID = QFWFQ_DATABASES["type"]
QFWFQ_ELEMENTS_DB_ID = QFWFQ_DATABASES["elements"]
QFWFQ_TAGS_DB_ID = QFWFQ_DATABASES["tags"]
QFWFQ_PALACE_LIDO_TAGS_DB_ID = QFWFQ_DATABASES["palace_lido_tags"]

# Candidate paths for .env discovery
ENV_CANDIDATE_PATHS = [
    Path.cwd() / ".env",
    Path.cwd() / "backend" / ".env",
    Path("/Users/admin/webapps/soupre/.env"),
    Path("/Users/admin/webapps/no-db-man/.env"),
]


def discover_tokens(extra_paths: Optional[list[Path]] = None) -> dict[str, str]:
    """
    Search candidate .env files and os.environ for Notion tokens.
    Returns a dictionary of discovered token names to token values.
    """
    tokens: dict[str, str] = {}
    paths = (extra_paths or []) + ENV_CANDIDATE_PATHS

    for path in paths:
        try:
            if path.is_file():
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        key, val = line.split("=", 1)
                        key = key.strip()
                        val = val.strip().strip("'\"")
                        if "NOTION" in key and val and key not in tokens:
                            tokens[key] = val
        except Exception as e:
            logger.debug(f"Failed to read env candidate {path}: {e}")

    # os.environ takes precedence or supplements
    for key, val in os.environ.items():
        if "NOTION" in key and val:
            tokens[key] = val

    return tokens


def get_workspace_token(workspace: str, extra_paths: Optional[list[Path]] = None) -> Optional[str]:
    """
    Resolve the Notion token for a given workspace slug.
    Strictly returns None for 'full-site' workspace.
    """
    ws = (workspace or "").strip().lower()
    if ws in ("full-site", "fullsite", ""):
        return None

    tokens = discover_tokens(extra_paths=extra_paths)

    if ws in ("fp", "services"):
        return tokens.get("NOTION_FP_TOKEN") or tokens.get("NOTION_TOKEN")
    elif ws in ("qfwfq", "goods"):
        return tokens.get("NOTION_QFWFQ_TOKEN") or tokens.get("NOTION_TOKEN")

    # Generic workspace fallback: NOTION_<WS>_TOKEN
    ws_key = f"NOTION_{ws.upper().replace('-', '_')}_TOKEN"
    return tokens.get(ws_key) or tokens.get("NOTION_TOKEN")


def get_target_db_id(workspace: str, db_type: Optional[str] = None) -> Optional[str]:
    """
    Get the default destination Notion database ID for a workspace.
    Strictly returns None for 'full-site' workspace.
    """
    ws = (workspace or "").strip().lower()
    if ws in ("full-site", "fullsite", ""):
        return None

    if ws in ("fp", "services"):
        return FP_DATABASES.get(db_type or "services", SERVICES_DB_ID)
    elif ws in ("qfwfq", "goods"):
        return QFWFQ_DATABASES.get(db_type or "goods", GOODS_DB_ID)

    return None


def get_notion_client(workspace: str, extra_paths: Optional[list[Path]] = None) -> Optional[Client]:
    """
    Factory function: returns an authenticated Notion Client for the requested workspace,
    or None if the workspace is isolated (e.g. 'full-site') or has no valid token.
    """
    token = get_workspace_token(workspace, extra_paths=extra_paths)
    if not token:
        return None
    return Client(auth=token)
