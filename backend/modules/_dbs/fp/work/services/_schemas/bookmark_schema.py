"""
schemas/services.py
─────────────────────────────────────────────────────────────────────────────
Services bookmark import schema — migrated from the original mapper.py
and builder.py with NO behavioral change.

Produces identical Notion page payloads as the pre-refactor code.
"""

from __future__ import annotations

import re
import logging

from modules._extra.bookmarks.schema_base import clean_segment
from modules.scraper.extractors.icons_base64 import decode_icon, upload_icon, icon_payload
from modules.notion.relations import resolve_relation_ids, ensure_relation_page

from modules.taxonomies.jev_matcher import match_taxonomy_values

# ── Metadata resolver (JEV-backed) ───────────────────────────────────────────

def _resolve_folder_meta(location: str) -> dict:
    """
    Parse a breadcrumb location string into {roles, types, tags} using dynamic
    JEV taxonomy matching.
    """
    raw_segments = [s.strip() for s in location.split("›") if s.strip()]
    if not raw_segments:
        return {"roles": ["Technology"], "types": ["Utils"], "tags": ["Utils"]}

    cleaned_segments = [clean_segment(s) for s in raw_segments if clean_segment(s)]
    if not cleaned_segments:
        cleaned_segments = raw_segments

    # Pass through JEV matcher to validate against live _taxonomy.json
    valid_roles = match_taxonomy_values("services", "Roles", cleaned_segments)
    if not valid_roles:
        valid_roles = ["Technology"]

    valid_types = match_taxonomy_values("services", "Types", cleaned_segments)
    if not valid_types:
        valid_types = ["Utils"]

    # Tags can auto-create, so we pass auto_create=True
    valid_tags = match_taxonomy_values("services", "Tags", cleaned_segments, auto_create=True)
    if not valid_tags:
        valid_tags = ["Utils"]

    return {
        "roles": valid_roles,
        "types": valid_types,
        "tags": valid_tags,
    }

# ── Schema class ─────────────────────────────────────────────────────────────

class ServicesSchema:
    """
    Bookmark import schema for the FP / Services database.

    - root_folder = None → imports ALL bookmarks (no root filtering)
    - Relation keys: tags, types, roles
    - Tags are auto-created if missing (via ensure_relation_page)
    - Icons are uploaded via the File Upload API
    """

    key: str = "services"
    root_folder: str | None = None
    relation_keys: tuple[str, ...] = ("tags", "types", "roles")

    def map_metadata(self, breadcrumb: list[str]) -> dict:
        """Delegates to the migrated resolve_folder_meta logic."""
        location = " › ".join(breadcrumb)
        return _resolve_folder_meta(location)

    def build_properties(
        self,
        bookmark: dict,
        relation_maps: dict[str, dict[str, str]],
        relation_db_ids: dict[str, str],
        *,
        client: object,
        notion_token: str,
        rate_limit_delay: float,
        dry_run: bool,
        new_tags_log: list[dict],
    ) -> tuple[dict, dict | None]:
        """
        Constructs properties dict and optional icon payload for Notion
        page creation. Identical output to the pre-refactor builder.py.

        Returns (props, icon_dict_or_None).
        """
        bm = bookmark
        meta = _resolve_folder_meta(bm["location"])

        tag_map  = relation_maps.get("tags", {})
        type_map = relation_maps.get("types", {})
        role_map = relation_maps.get("roles", {})
        tags_db_id = relation_db_ids.get("tags", "")

        props: dict = {}

        # ── title ─────────────────────────────────────────────────────────────
        props["Name"] = {
            "title": [{"text": {"content": bm["name"][:2000]}}]
        }

        # ── url ───────────────────────────────────────────────────────────────
        if bm["url"]:
            props["URL"] = {"url": bm["url"]}

        # ── date ──────────────────────────────────────────────────────────────
        if bm["added_iso"]:
            props["Added"] = {"date": {"start": bm["added_iso"]}}

        # ── checkboxes ────────────────────────────────────────────────────────
        props["Archive"] = {"checkbox": False}
        props["Pin"]     = {"checkbox": False}

        # ── rich_text ─────────────────────────────────────────────────────────
        # Domains and Location are relations in the live schema — skip.
        # (Kept commented out for documentation parity with original builder.py)
        # if bm["domain"]:
        #     props["Domains"] = {
        #         "rich_text": [{"text": {"content": bm["domain"][:2000]}}]
        #     }
        # if bm["location"]:
        #     props["Location"] = {
        #         "rich_text": [{"text": {"content": bm["location"][:2000]}}]
        #     }

        # ── relations ─────────────────────────────────────────────────────────
        role_ids = resolve_relation_ids(meta["roles"], role_map)
        if role_ids:
            props["Roles"] = {"relation": role_ids}

        type_ids = resolve_relation_ids(meta["types"], type_map)
        if type_ids:
            props["Types"] = {"relation": type_ids}

        tag_ids = []
        for tag_name in meta["tags"]:
            pid = ensure_relation_page(
                client=client,
                name=tag_name,
                relation_map=tag_map,
                new_items_log=new_tags_log,
                db_id=tags_db_id,
                rate_limit_delay=rate_limit_delay,
                dry_run=dry_run,
            )
            if pid:
                tag_ids.append({"id": pid})
        if tag_ids:
            props["Tags"] = {"relation": tag_ids}

        # ── icon ──────────────────────────────────────────────────────────────
        icon: dict | None = None
        if bm.get("icon_b64"):
            decoded = decode_icon(bm["icon_b64"])
            if decoded and notion_token:
                upload_id = upload_icon(
                    token=notion_token,
                    decoded=decoded,
                    mime=bm.get("icon_mime", "image/png"),
                    name=bm["name"][:60],
                )
                if upload_id:
                    icon = icon_payload(upload_id)

        return props, icon
