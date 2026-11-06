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
    "roles": "d74d19f8-6945-4eb0-8d40-e879f3b1d571",
    "types": "373c7930-1516-80df-9342-f3510c8bed9f",
    "tags": "11ac7930-1516-8066-8be3-f104b1285a9a",
}

QFWFQ_DATABASES = {
    "goods": "d46f6dbf-310c-4c4d-800e-813eeba04ab6",
    "items": "e74f045a-24ba-4f4e-acbc-1917636efaab",
    "type": "689933d7-5077-4f4f-bb2e-8541cd1dcff0",
    "shops": "15863158-bfa0-800a-8e34-e6462c16d17a",
}

# Constant Aliases
SERVICES_DB_ID = FP_DATABASES["services"]
ROLES_DB_ID = FP_DATABASES["roles"]
FP_TYPES_DB_ID = FP_DATABASES["types"]
FP_TAGS_DB_ID = FP_DATABASES["tags"]

GOODS_DB_ID = QFWFQ_DATABASES["goods"]
ITEMS_DB_ID = QFWFQ_DATABASES["items"]
QFWFQ_TYPE_DB_ID = QFWFQ_DATABASES["type"]
SHOPS_DB_ID = QFWFQ_DATABASES["shops"]

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
