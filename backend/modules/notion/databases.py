"""
Registry and discovery engine for all available Notion database schemas from no-db-man.
Discovers canonical schemas across FP and Qfwfq workspaces, exposing metadata,
supported taxonomy relations (tags, categories, types), and field mapping rules.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class NotionDatabaseMeta:
    id: str
    name: str
    workspace: str        # "fp" | "qfwfq"
    dashboard: str        # "work" | "goods" | "profolio" | "palace-lido" | ...
    icon: str             # e.g. "⚡", "🎨", "🪐", "🛍️", "📋"
    schema_file: Optional[str] = None
    has_tags: bool = False
    has_categories: bool = False
    has_types: bool = False
    has_elements: bool = False
    properties: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "workspace": self.workspace,
            "dashboard": self.dashboard,
            "icon": self.icon,
            "has_tags": self.has_tags,
            "has_categories": self.has_categories,
            "has_types": self.has_types,
            "has_elements": self.has_elements,
            "properties": self.properties,
        }


# Canonical Database Definitions reflecting no-db-man schemas & config.py
CANONICAL_DATABASES: list[dict[str, Any]] = [
    # ── FP Workspace ──────────────────────────────────────────────────────────
    {
        "id": "11ac7930-1516-807e-b685-d3fee70ae00d",
        "name": "Services",
        "workspace": "fp",
        "dashboard": "work",
        "icon": "⚡",
        "schema_key": "services",
        "has_tags": True,
        "has_categories": True,
        "has_types": True,
        "properties": ["Name", "URL", "Domains", "Roles", "Types", "Tags", "Description"],
    },
    {
        "id": "00ec9a41-3957-4b5d-91b5-3d586e331405",
        "name": "Creatives",
        "workspace": "fp",
        "dashboard": "profolio",
        "icon": "🎨",
        "schema_key": "creatives",
        "has_tags": True,
        "has_categories": False,
        "has_types": True,
        "properties": ["Name", "URL", "Tags", "Fields", "Type", "Description"],
    },
    {
        "id": "11ac7930-1516-8025-a4b5-ca31792a7f5a",
        "name": "Tasks",
        "workspace": "fp",
        "dashboard": "work",
        "icon": "📋",
        "schema_key": "tasks",
        "has_tags": False,
        "has_categories": True,
        "has_types": False,
        "properties": ["Name", "Roles", "Status", "Personas", "Date"],
    },
    {
        "id": "11ac7930-1516-805d-af7f-fa08191ec485",
        "name": "Clients",
        "workspace": "fp",
        "dashboard": "work",
        "icon": "🤝",
        "schema_key": "clients",
        "has_tags": False,
        "has_categories": False,
        "has_types": False,
        "properties": ["Name", "Projects", "Status", "Contact"],
    },
    {
        "id": "11ac7930-1516-80a5-a0ae-e7d6fa6a9d70",
        "name": "Projects",
        "workspace": "fp",
        "dashboard": "work",
        "icon": "📁",
        "schema_key": "projects",
        "has_tags": False,
        "has_categories": False,
        "has_types": False,
        "properties": ["Name", "Clients", "Tasks", "Status", "Timeline"],
    },
    {
        "id": "11ac7930-1516-80fe-9f37-d204a11f26f6",
        "name": "Notes",
        "workspace": "fp",
        "dashboard": "work",
        "icon": "📝",
        "schema_key": "notes",
        "has_tags": False,
        "has_categories": False,
        "has_types": False,
        "properties": ["Name", "Projects", "Date", "Summary"],
    },
    {
        "id": "fa29eb81-8ac4-4c64-858f-018a70844124",
        "name": "Media",
        "workspace": "fp",
        "dashboard": "palace-lido",
        "icon": "🖼️",
        "schema_key": "media",
        "has_tags": True,
        "has_categories": False,
        "has_types": True,
        "properties": ["Name", "Tags", "URL #1", "URL #2", "URL #3", "Type", "Status", "Master"],
    },
    {
        "id": "a4fcf8ca-fa5c-4e89-9dbf-19149fe8e698",
        "name": "Website",
        "workspace": "fp",
        "dashboard": "palace-lido",
        "icon": "🏨",
        "schema_key": "website",
        "has_tags": True,
        "has_categories": False,
        "has_types": False,
        "properties": ["Name", "Tags", "Market", "Media", "Page URL"],
    },
    {
        "id": "11ac7930-1516-8095-a4b4-d7d45eb22197",
        "name": "Prompt Templates",
        "workspace": "fp",
        "dashboard": "work",
        "icon": "💡",
        "schema_key": "prompt-templates",
        "has_tags": False,
        "has_categories": False,
        "has_types": False,
        "properties": ["Name", "Model", "Prompt Text", "Category"],
    },
    {
        "id": "11ac7930-1516-8041-86e5-da7c2657e237",
        "name": "Documents, Proposals & Reports",
        "workspace": "fp",
        "dashboard": "work",
        "icon": "📄",
        "schema_key": "documents-proposals-reports",
        "has_tags": False,
        "has_categories": False,
        "has_types": False,
        "properties": ["Name", "Clients", "Projects", "Date", "Status"],
    },
    {
        "id": "11ac7930-1516-8040-af25-ec98fb1bfa50",
        "name": "Personas",
        "workspace": "fp",
        "dashboard": "work",
        "icon": "👤",
        "schema_key": "personas",
        "has_tags": False,
        "has_categories": False,
        "has_types": False,
        "properties": ["Name", "Role", "Specialization", "Tasks"],
    },
    {
        "id": "270aabfa-9126-459d-93e9-cb8d894347fd",
        "name": "Global Keywords",
        "workspace": "fp",
        "dashboard": "palace-lido",
        "icon": "🏷️",
        "schema_key": "global-keywords",
        "has_tags": True,
        "has_categories": False,
        "has_types": False,
        "properties": ["Name", "Tags", "Sub-item", "Parent item"],
    },

    # ── QFWFQ Workspace ───────────────────────────────────────────────────────
    {
        "id": "e74f045a-24ba-4f4e-acbc-1917636efaab",
        "name": "Goods (Items)",
        "workspace": "qfwfq",
        "dashboard": "goods",
        "icon": "🪐",
        "schema_key": "items",
        "has_tags": True,
        "has_categories": True,
        "has_types": True,
        "has_elements": True,
        "properties": ["Name", "Url 1", "Categories", "Type", "Tags", "Elements", "Price", "Description", "Buy Now", "Order URL", "Brand", "Shops"],
    },
    {
        "id": "41163158-bfa0-8110-9ec1-e4c34e3a9eec",
        "name": "Elements",
        "workspace": "qfwfq",
        "dashboard": "goods",
        "icon": "🧩",
        "schema_key": "elements",
        "has_tags": False,
        "has_categories": False,
        "has_types": False,
        "has_elements": False,
        "properties": ["Name", "Items Relation", "Items Costs", "Items Count", "Total Own"],
    },
    {
        "id": "15863158-bfa0-800a-8e34-e6462c16d17a",
        "name": "Shops",
        "workspace": "qfwfq",
        "dashboard": "goods",
        "icon": "🛍️",
        "schema_key": "shops",
        "has_tags": False,
        "has_categories": True,
        "has_types": True,
        "properties": ["Name", "URL", "Categories", "Type", "Description", "Location"],
    },
    {
        "id": "239e2e28-a234-4106-9ce7-ed737e9e1669",
        "name": "Brands",
        "workspace": "qfwfq",
        "dashboard": "goods",
        "icon": "🏷️",
        "schema_key": "brands",
        "has_tags": False,
        "has_categories": False,
        "has_types": False,
        "properties": ["Name", "URL", "Description", "Items Relation"],
    },
    {
        "id": "17e63158-bfa0-808e-aede-d06ca3cf0c74",
        "name": "Return Points",
        "workspace": "qfwfq",
        "dashboard": "goods",
        "icon": "📍",
        "schema_key": "return-points",
        "has_tags": False,
        "has_categories": False,
        "has_types": False,
        "properties": ["Name", "Items Relation"],
    },
    {
        "id": "fa48d3d7-7e88-48f1-a589-a54d63fd4264",
        "name": "Costs",
        "workspace": "qfwfq",
        "dashboard": "costs",
        "icon": "💰",
        "schema_key": "all-costs",
        "has_tags": False,
        "has_categories": False,
        "has_types": False,
        "properties": ["Obj", "Value", "Date", "Type", "Person"],
    },
    {
        "id": "a2d3d887-0ccc-4c3e-a67d-5a5a00750c5a",
        "name": "Pinned",
        "workspace": "qfwfq",
        "dashboard": "todo",
        "icon": "📌",
        "schema_key": "pinned",
        "has_tags": False,
        "has_categories": True,
        "has_types": True,
        "properties": ["Name", "URL", "Type", "Category", "Notes", "Date"],
    },
    {
        "id": "fd7f7270-28b3-41be-a460-94671e024747",
        "name": "Trips",
        "workspace": "qfwfq",
        "dashboard": "todo",
        "icon": "✈️",
        "schema_key": "trips",
        "has_tags": False,
        "has_categories": False,
        "has_types": True,
        "properties": ["Name", "URL", "Type", "Location", "Date"],
    },
]


def discover_databases_from_disk(dbs_dir: Optional[Path] = None) -> list[NotionDatabaseMeta]:
    """
    Scan schema directories and merge with canonical database metadata.
    """
    if dbs_dir is None:
        dbs_dir = Path(__file__).resolve().parent.parent / "_dbs"

    discovered: dict[str, NotionDatabaseMeta] = {}

    # Seed canonical first
    for item in CANONICAL_DATABASES:
        meta = NotionDatabaseMeta(
            id=item["id"],
            name=item["name"],
            workspace=item["workspace"],
            dashboard=item["dashboard"],
            icon=item["icon"],
            schema_file=None,
            has_tags=item["has_tags"],
            has_categories=item["has_categories"],
            has_types=item["has_types"],
            properties=list(item["properties"]),
        )
        discovered[meta.id] = meta

    # Check disk for schema files
    candidate_dirs = [dbs_dir]
    no_db_man_dbs = Path("/Users/admin/webapps/no-db-man/modules/_dbs")
    if no_db_man_dbs.is_dir() and no_db_man_dbs.resolve() != dbs_dir.resolve():
        candidate_dirs.append(no_db_man_dbs)

    for base_dir in candidate_dirs:
        if not base_dir.exists():
            continue
        for schema_json in base_dir.rglob("*.json"):
            if schema_json.name.startswith("_"):
                continue
            try:
                data = json.loads(schema_json.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    continue
                db_id = data.get("id")
                if not db_id:
                    continue

                title = data.get("title") or schema_json.stem.replace("-", " ").title()
                props = list(data.get("properties", {}).keys()) if isinstance(data.get("properties"), dict) else []
                has_tags = any(p.lower() in ("tags", "tag") for p in props)
                has_categories = any(p.lower() in ("categories", "category", "roles", "role") for p in props)
                has_types = any(p.lower() in ("type", "types") for p in props)
                has_elements = any(p.lower() in ("elements", "element") for p in props)

                parts = [p.lower() for p in schema_json.parts]
                ws = "fp" if "fp" in parts else ("qfwfq" if "qfwfq" in parts else "fp")
                dashboard = parts[parts.index(ws) + 1] if ws in parts and parts.index(ws) + 1 < len(parts) else "general"

                if db_id in discovered:
                    existing = discovered[db_id]
                    existing.schema_file = str(schema_json)
                    if props:
                        existing.properties = sorted(list(set(existing.properties + props)))
                        existing.has_tags = existing.has_tags or has_tags
                        existing.has_categories = existing.has_categories or has_categories
                        existing.has_types = existing.has_types or has_types
                        existing.has_elements = existing.has_elements or has_elements
                else:
                    icon = "⚡" if ws == "fp" else ("🧩" if "elements" in schema_json.stem.lower() else "🪐")
                    discovered[db_id] = NotionDatabaseMeta(
                        id=db_id,
                        name=title,
                        workspace=ws,
                        dashboard=dashboard,
                        icon=icon,
                        schema_file=str(schema_json),
                        has_tags=has_tags,
                        has_categories=has_categories,
                        has_types=has_types,
                        has_elements=has_elements,
                        properties=props,
                    )
            except Exception as e:
                logger.debug(f"Failed to parse {schema_json}: {e}")

    return list(discovered.values())


def list_available_databases(workspace: Optional[str] = None) -> list[dict[str, Any]]:
    """
    Returns metadata for all available databases in no-db-man schemas.
    Filtered by workspace slug ('fp', 'qfwfq') if provided.
    Strictly returns empty list for 'full-site' and 'ffff' workspaces (isolated).
    """
    if workspace:
        ws_norm = workspace.strip().lower()
        if ws_norm in ("full-site", "fullsite", "ffff"):
            return []

    databases = discover_databases_from_disk()

    if workspace:
        ws_norm = workspace.strip().lower()
        databases = [d for d in databases if d.workspace.lower() == ws_norm]

    # Sort consistently: Services/Goods first, then alphabetical by name
    def _sort_key(d: NotionDatabaseMeta):
        is_primary = d.name.lower() in ("services", "goods (items)", "goods")
        return (not is_primary, d.workspace, d.name)

    databases.sort(key=_sort_key)
    return [d.to_dict() for d in databases]


def get_database_meta(database_id: str) -> Optional[NotionDatabaseMeta]:
    """Find metadata for a database by its Notion UUID."""
    for db in discover_databases_from_disk():
        if db.id.replace("-", "") == database_id.replace("-", ""):
            return db
    return None
