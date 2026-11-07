"""
Pure content mappers transforming scraped data and URL preview items
into validated Notion page property payloads.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def clean_notion_title(text: Optional[str], max_len: int = 100) -> str:
    """
    Sanitize and truncate text for Notion page title property.
    Removes redundant whitespace, newlines, and truncates cleanly.
    """
    if not text:
        return "Untitled"
    clean = " ".join(str(text).split()).strip()
    if not clean:
        return "Untitled"
    if len(clean) > max_len:
        return clean[:max_len].rstrip() + "..."
    return clean


def extract_domain(url: Optional[str]) -> str:
    """Extract host domain from URL string."""
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        return parsed.netloc or ""
    except Exception:
        return ""


def _extract_field(item: Any, *keys: str, default: Any = None) -> Any:
    """Helper to safely fetch property from dict or model instance."""
    for key in keys:
        if isinstance(item, dict):
            if key in item and item[key] is not None:
                return item[key]
        else:
            val = getattr(item, key, None)
            if val is not None:
                return val
    return default


def _extract_description(item: Any) -> Optional[str]:
    """
    Extract description for rich or lightweight payload.
    Checks markdown_summary, meta_description, description, or markdown file content.
    """
    desc = _extract_field(item, "markdown_summary", "meta_description", "description")
    if desc:
        return str(desc).strip()

    markdown_path = _extract_field(item, "markdown_path")
    if markdown_path:
        p = Path(markdown_path)
        if p.is_file():
            try:
                content = p.read_text(encoding="utf-8")
                # Strip excessive whitespace and take first chunk
                stripped = "\n".join(line.strip() for line in content.splitlines() if line.strip())
                return stripped[:2000]
            except Exception as e:
                logger.debug(f"Failed to read markdown file {markdown_path}: {e}")

    return None


def map_goods_payload(
    item: Any,
    category_notion_id: Optional[str] = None,
    type_notion_id: Optional[str] = None,
    tag_notion_ids: Optional[list[str]] = None,
) -> dict[str, Any]:
    """
    Maps an item to a Notion page properties payload for the Goods / Items DB.
    Properties:
      - Name: title (up to 2000 chars)
      - Url 1 / Buy Now: url
      - Categories: relation [category_notion_id]
      - Type: relation [type_notion_id]
      - Price: number or rich_text
      - Description: rich_text (up to 2000 chars)
      - Tags: relation [tag_notion_ids]
    """
    title_raw = _extract_field(item, "title", "name") or extract_domain(_extract_field(item, "url", "clean_url")) or "Untitled"
    clean_title = clean_notion_title(title_raw, max_len=100)

    url = _extract_field(item, "url", "clean_url")
    buy_now_val = _extract_field(item, "buy_now", "order_url")

    props: dict[str, Any] = {
        "Name": {
            "title": [{"text": {"content": clean_title}}]
        }
    }

    # URL / Buy Now mapping
    if url:
        props["Url 1"] = {"url": url}
    if buy_now_val:
        if isinstance(buy_now_val, str) and (buy_now_val.startswith("http://") or buy_now_val.startswith("https://")):
            props["Buy Now"] = {"url": buy_now_val}
            props["Order URL"] = {"url": buy_now_val}
        elif isinstance(buy_now_val, bool):
            props["Buy Now"] = {"checkbox": buy_now_val}

    # Relations
    cat_id = category_notion_id or _extract_field(item, "category_notion_id", "notion_id")
    if cat_id:
        props["Categories"] = {"relation": [{"id": cat_id}]}

    t_id = type_notion_id or _extract_field(item, "type_notion_id")
    if t_id:
        props["Type"] = {"relation": [{"id": t_id}]}

    tags = tag_notion_ids or _extract_field(item, "tag_notion_ids") or []
    if isinstance(tags, str):
        tags = [tags]
    tag_relations = [{"id": tid} for tid in tags if tid]
    if tag_relations:
        props["Tags"] = {"relation": tag_relations}

    # Price mapping
    price = _extract_field(item, "price")
    if price is not None and str(price).strip() != "":
        try:
            num = float(price)
            props["Price"] = {"number": num}
        except (ValueError, TypeError):
            props["Price"] = {"rich_text": [{"text": {"content": str(price)[:2000]}}]}

    # Description mapping (lightweight or rich)
    desc = _extract_description(item)
    if desc:
        props["Description"] = {"rich_text": [{"text": {"content": desc[:2000]}}]}

    return props


def map_services_payload(
    item: Any,
    category_notion_id: Optional[str] = None,
    type_notion_id: Optional[str] = None,
    tag_notion_ids: Optional[list[str]] = None,
) -> dict[str, Any]:
    """
    Maps an item to a Notion page properties payload for the Services DB.
    Properties:
      - Name: title (up to 2000 chars)
      - URL: url
      - Roles: relation [category_notion_id]
      - Types: relation [type_notion_id]
      - Tags: relation [tag_notion_ids]
      - Domains: rich_text
      - Description: rich_text (up to 2000 chars)
    """
    url = _extract_field(item, "url", "clean_url")
    domain = _extract_field(item, "domain") or extract_domain(url)

    title_raw = _extract_field(item, "title", "name") or domain or "Untitled"
    clean_title = clean_notion_title(title_raw, max_len=100)

    props: dict[str, Any] = {
        "Name": {
            "title": [{"text": {"content": clean_title}}]
        }
    }

    if url:
        props["URL"] = {"url": url}

    if domain:
        props["Domains"] = {"rich_text": [{"text": {"content": domain[:2000]}}]}

    # Roles relation (FP categories map to Roles in Notion)
    cat_id = category_notion_id or _extract_field(item, "category_notion_id", "notion_id")
    if cat_id:
        props["Roles"] = {"relation": [{"id": cat_id}]}

    # Types relation
    t_id = type_notion_id or _extract_field(item, "type_notion_id")
    if t_id:
        props["Types"] = {"relation": [{"id": t_id}]}

    # Tags relation
    tags = tag_notion_ids or _extract_field(item, "tag_notion_ids") or []
    if isinstance(tags, str):
        tags = [tags]
    tag_relations = [{"id": tid} for tid in tags if tid]
    if tag_relations:
        props["Tags"] = {"relation": tag_relations}

    # Description mapping (lightweight or rich)
    desc = _extract_description(item)
    if desc:
        props["Description"] = {"rich_text": [{"text": {"content": desc[:2000]}}]}

    return props


def build_notion_payload(
    item: Any,
    target_db_id: Optional[str] = None,
    category_notion_id: Optional[str] = None,
    type_notion_id: Optional[str] = None,
    tag_notion_ids: Optional[list[str]] = None,
) -> dict[str, Any]:
    """
    Dynamically build a Notion page property payload adapted to the target database's
    properties schema (from no-db-man), automatically mapping Title, URL, Tags,
    Categories/Roles, Types, Description, Price, and related fields.
    """
    from modules.notion.databases import get_database_meta

    meta = get_database_meta(target_db_id) if target_db_id else None
    props_keys = [p.lower() for p in meta.properties] if meta and meta.properties else []

    # If no metadata or unknown target, fall back to standard mappers based on workspace
    if not meta or not meta.properties:
        ws = getattr(item, "workspace", None)
        ws_slug = getattr(ws, "slug", "") if ws else ""
        if isinstance(item, dict):
            ws_slug = item.get("workspace_slug") or ws_slug
        if ws_slug in ("qfwfq", "goods"):
            return map_goods_payload(item, category_notion_id, type_notion_id, tag_notion_ids)
        return map_services_payload(item, category_notion_id, type_notion_id, tag_notion_ids)

    url = _extract_field(item, "url", "clean_url")
    domain = _extract_field(item, "domain") or extract_domain(url)
    title_raw = _extract_field(item, "title", "name") or domain or "Untitled"
    clean_title = clean_notion_title(title_raw, max_len=100)

    props: dict[str, Any] = {}

    # 1. Title property
    title_key = "Name"
    if "obj" in props_keys and "name" not in props_keys:
        title_key = "Obj"
    elif "title" in props_keys and "name" not in props_keys:
        title_key = "Title"
    props[title_key] = {"title": [{"text": {"content": clean_title}}]}

    # 2. URL property
    if url:
        if "url 1" in props_keys:
            props["Url 1"] = {"url": url}
        elif "url" in props_keys:
            props["URL"] = {"url": url}
        elif "page url" in props_keys:
            props["Page URL"] = {"url": url}
        elif "source" in props_keys:
            props["Source"] = {"url": url}
        elif "web services" in props_keys:
            props["Web Services"] = {"url": url}

    # 3. Buy Now / Order URL
    buy_now_val = _extract_field(item, "buy_now", "order_url")
    if buy_now_val:
        if "buy now" in props_keys:
            if isinstance(buy_now_val, str) and (buy_now_val.startswith("http://") or buy_now_val.startswith("https://")):
                props["Buy Now"] = {"url": buy_now_val}
            elif isinstance(buy_now_val, bool):
                props["Buy Now"] = {"checkbox": buy_now_val}
        if "order url" in props_keys and isinstance(buy_now_val, str):
            props["Order URL"] = {"url": buy_now_val}

    # 4. Categories / Roles
    cat_id = category_notion_id or _extract_field(item, "category_notion_id", "notion_id")
    if cat_id:
        if "roles" in props_keys:
            props["Roles"] = {"relation": [{"id": cat_id}]}
        elif "categories" in props_keys:
            props["Categories"] = {"relation": [{"id": cat_id}]}
        elif "category" in props_keys:
            props["Category"] = {"relation": [{"id": cat_id}]}

    # 5. Type / Types
    t_id = type_notion_id or _extract_field(item, "type_notion_id")
    if t_id:
        if "types" in props_keys:
            props["Types"] = {"relation": [{"id": t_id}]}
        elif "type" in props_keys:
            props["Type"] = {"relation": [{"id": t_id}]}

    # 6. Tags
    tags = tag_notion_ids or _extract_field(item, "tag_notion_ids") or []
    if isinstance(tags, str):
        tags = [tags]
    tag_relations = [{"id": tid} for tid in tags if tid]
    if tag_relations:
        if "tags" in props_keys:
            props["Tags"] = {"relation": tag_relations}
        elif "tag" in props_keys:
            props["Tag"] = {"relation": tag_relations}

    # 7. Description / Notes / Summary
    desc = _extract_description(item)
    if desc:
        if "description" in props_keys:
            props["Description"] = {"rich_text": [{"text": {"content": desc[:2000]}}]}
        elif "notes" in props_keys:
            props["Notes"] = {"rich_text": [{"text": {"content": desc[:2000]}}]}
        elif "summary" in props_keys:
            props["Summary"] = {"rich_text": [{"text": {"content": desc[:2000]}}]}

    # 8. Domains
    if domain and "domains" in props_keys:
        props["Domains"] = {"rich_text": [{"text": {"content": domain[:2000]}}]}

    # 9. Price
    price = _extract_field(item, "price")
    if price is not None and str(price).strip() != "" and "price" in props_keys:
        try:
            num = float(price)
            props["Price"] = {"number": num}
        except (ValueError, TypeError):
            props["Price"] = {"rich_text": [{"text": {"content": str(price)[:2000]}}]}

    return props


class NotionContentMapper:
    """Wrapper class providing unified access to content mappers."""
    clean_notion_title = staticmethod(clean_notion_title)
    map_goods_payload = staticmethod(map_goods_payload)
    map_services_payload = staticmethod(map_services_payload)
    build_notion_payload = staticmethod(build_notion_payload)
