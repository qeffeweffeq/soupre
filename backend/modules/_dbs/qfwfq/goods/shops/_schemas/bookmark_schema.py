"""
schemas/shops.py
─────────────────────────────────────────────────────────────────────────────
Shops bookmark import schema — for the QFWFQ "GOODS" dashboard.

Target DB: NOTION_GOODS_SHOPS_DB_ID

⚠️ Design assumptions:
  - Categories is a MULTI relation; Type is a SINGLE relation.
  - Categories/Type values must match EXISTING entries by name.
  - Unlike Services Tags, missing Categories/Type entries are NOT auto-created.
    If a mapped name is not found, a warning is logged and the relation is left empty.
    (The Shops/Goods taxonomy is curated; auto-creation is not desired.)
"""

from __future__ import annotations

import re
import logging

from modules._extra.bookmarks.schema_base import clean_segment, root_folder_index
from modules.scraper.extractors.icons_base64 import decode_icon, upload_icon, icon_payload
from modules.notion.relations import resolve_relation_ids

log = logging.getLogger(__name__)


# ── Folder → metadata mapping rules ─────────────────────────────────────────
# The SECOND breadcrumb segment (the sub-shelf, after the root "🛍 Shops")
# drives the mapping. Ordered IGNORECASE regex rules; first match wins.

SHOP_FOLDER_RULES: list[tuple[str, dict]] = [
    (r"bio.?food|food",
     {"categories": ["Food"],               "type": ["Food"]}),

    (r"clothe|apparel|wear",
     {"categories": ["Street"],              "type": ["Apparel"]}),

    (r"furniture|home",
     {"categories": ["Home"],                "type": ["Furniture"]}),

    (r"cycl|bike",
     {"categories": ["Cycling"],             "type": ["Gear"]}),

    (r"graphic|paper|print",
     {"categories": ["Creativity"],          "type": ["Stationary"]}),

    (r"beauty|cosmetic",
     {"categories": ["Wellness"],            "type": ["Body"]}),

    (r"coffee|cialde",
     {"categories": ["Food"],                "type": ["Beverage"]}),

    # ⚠️ Type "Accessories" is assumed to exist in the GOODS Types DB
    (r"music|vinyl|record",
     {"categories": ["Entertainment"],       "type": ["Accessories"]}),

    (r"cool.?stuff|misc|gift",
     {"categories": ["Creativity", "Other"], "type": ["Accessories"]}),
]

# Default fallback for unmatched sub-folders
_DEFAULT_META: dict = {"categories": ["Other"], "type": ["Accessories"]}


def _clean_shop_name(raw_name: str) -> str:
    """
    Clean a shop bookmark name:
    - Take text up to the first '|', '—', or '-' separator
    - Collapse whitespace
    - Strip leading/trailing whitespace

    Examples:
        'MUJI Online | Home & Kitchen'  → 'MUJI Online'
        'Rapha — Performance Road Cycling Clothing' → 'Rapha'
        'Real Food Source - Organic'     → 'Real Food Source'
        '  Some  Shop  '                → 'Some Shop'
    """
    # Split on pipe, em-dash, or hyphen (with optional surrounding spaces)
    # Use the FIRST segment as the shop name
    parts = re.split(r'\s*[|—]\s*|\s+[-]\s+', raw_name, maxsplit=1)
    name = parts[0] if parts else raw_name
    return re.sub(r'\s+', ' ', name).strip()


class ShopsSchema:
    """
    Bookmark import schema for the QFWFQ / GOODS Shops database.

    - root_folder = "Shops" → only imports bookmarks under "🛍 Shops"
    - Relation keys: categories, type
    - NO auto-creation of missing relation entries — warns and skips
    - Icons uploaded via the File Upload API (same as Services)
    """

    key: str = "shops"
    root_folder: str | None = "Shops"
    relation_keys: tuple[str, ...] = ("categories", "type")

    def map_metadata(self, breadcrumb: list[str]) -> dict:
        """
        Map breadcrumb to {categories, type} using the sub-folder rules.

        The second breadcrumb segment after the root "🛍 Shops" drives the
        mapping. First regex match wins; falls back to default.
        """
        # Find the root folder index
        ridx = root_folder_index(breadcrumb, "Shops")
        if ridx < 0:
            return dict(_DEFAULT_META)

        # The sub-folder is the segment right after the root
        sub_segments = breadcrumb[ridx + 1:]
        if not sub_segments:
            return dict(_DEFAULT_META)

        sub_folder = sub_segments[0]

        for pattern, meta in SHOP_FOLDER_RULES:
            if re.search(pattern, sub_folder, re.IGNORECASE):
                # Return a copy to avoid mutating the rule dicts
                return {"categories": list(meta["categories"]),
                        "type": list(meta["type"])}

        return dict(_DEFAULT_META)

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
        Build Notion page-creation payload for a Shops entry.

        Writable fields:
            Name, URL, Description, Categories (relation), Type (relation), Note

        SKIP (never write):
            Items, Costs, Costs Total (rollups),
            Costs Relation, Items Relation (manual relations),
            Created time (auto)

        Returns (props, icon_dict_or_None).
        """
        bm = bookmark
        breadcrumb = [s.strip() for s in bm["location"].split("›") if s.strip()]
        meta = self.map_metadata(breadcrumb)

        cat_map  = relation_maps.get("categories", {})
        type_map = relation_maps.get("type", {})

        props: dict = {}

        # ── title — cleaned shop name ─────────────────────────────────────────
        props["Name"] = {
            "title": [{"text": {"content": _clean_shop_name(bm["name"])[:2000]}}]
        }

        # ── url ───────────────────────────────────────────────────────────────
        if bm["url"]:
            props["URL"] = {"url": bm["url"]}

        # ── description — full original anchor text ───────────────────────────
        if bm["name"]:
            props["Description"] = {
                "rich_text": [{"text": {"content": bm["name"][:2000]}}]
            }

        # ── categories (multi relation) ───────────────────────────────────────
        # ⚠️ NO auto-create — if name not found, warn and skip
        cat_ids = resolve_relation_ids(meta["categories"], cat_map)
        missing_cats = [
            c for c in meta["categories"]
            if c.strip().lower() not in cat_map
        ]
        if missing_cats:
            log.warning(
                f"Shops: Categories not found (skipped): {missing_cats} "
                f"for bookmark '{bm['name'][:50]}'"
            )
        if cat_ids:
            props["Categories"] = {"relation": cat_ids}

        # ── type (single relation) ────────────────────────────────────────────
        # ⚠️ NO auto-create — if name not found, warn and skip
        type_ids = resolve_relation_ids(meta["type"], type_map)
        missing_types = [
            t for t in meta["type"]
            if t.strip().lower() not in type_map
        ]
        if missing_types:
            log.warning(
                f"Shops: Type not found (skipped): {missing_types} "
                f"for bookmark '{bm['name'][:50]}'"
            )
        if type_ids:
            # Single relation — only take the first match
            props["Type"] = {"relation": type_ids[:1]}

        # ── note — folder breadcrumb path + ADD_DATE ───────────────────────────
        note_parts = []
        if bm["location"]:
            note_parts.append(bm["location"])
        if bm.get("added_iso"):
            note_parts.append(f"Added: {bm['added_iso']}")
        if note_parts:
            props["Note"] = {
                "rich_text": [{"text": {"content": " │ ".join(note_parts)[:2000]}}]
            }

        # ── icon ──────────────────────────────────────────────────────────────
        icon: dict | None = None
        if bm.get("icon_b64"):
            decoded = decode_icon(bm["icon_b64"])
            if decoded and notion_token:
                upload_id = upload_icon(
                    token=notion_token,
                    decoded=decoded,
                    mime=bm.get("icon_mime", "image/png"),
                    name=_clean_shop_name(bm["name"])[:60],
                )
                if upload_id:
                    icon = icon_payload(upload_id)

        return props, icon
