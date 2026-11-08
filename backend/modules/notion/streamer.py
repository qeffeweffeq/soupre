"""
Streaming engine for Notion batch synchronization with rate-limiting,
URL deduplication, dry-run preview, and Server-Sent Events (SSE).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Optional
from sqlmodel import Session, select

from notion_client import Client
from notion_client.errors import APIResponseError

from database.models import Category, Page, Tag, Types, Workspace, Element
from modules.notion.client import (
    get_notion_client,
    get_target_db_id,
    SERVICES_DB_ID,
    GOODS_DB_ID,
)
from modules.notion.mapper import (
    clean_notion_title,
    map_goods_payload,
    map_services_payload,
)

logger = logging.getLogger(__name__)


def check_url_exists(client: Client, database_id: str, url: str) -> Optional[str]:
    """
    Query the destination Notion database to check if a page with the given URL exists.
    Returns existing Notion page ID if found, otherwise None.
    """
    if not url or not database_id:
        return None

    for prop_name in ["URL", "Url 1", "Order URL"]:
        try:
            res = client.databases.query(
                database_id=database_id,
                filter={
                    "property": prop_name,
                    "url": {
                        "equals": url
                    }
                },
                page_size=1
            )
            results = res.get("results", [])
            if results:
                return results[0]["id"]
            # If the query succeeded with 0 results, the URL property exists and has no match
            return None
        except APIResponseError as e:
            # If property name doesn't match database schema, try next candidate
            logger.debug(f"Query check on prop '{prop_name}' failed: {e}")
            continue
        except Exception as e:
            logger.warning(f"Unexpected error checking URL deduplication for {url}: {e}")
            return None

    return None


def _resolve_item_workspace_slug(item: Any, session: Optional[Session] = None) -> str:
    """Resolve workspace slug from item attributes or database session."""
    ws_slug = None
    if isinstance(item, dict):
        ws_slug = item.get("workspace_slug")
        ws_id = item.get("workspace_id")
    else:
        ws_slug = getattr(item, "workspace_slug", None)
        ws_id = getattr(item, "workspace_id", None)

    if ws_slug:
        return str(ws_slug).strip().lower()

    if session and ws_id:
        ws = session.get(Workspace, ws_id)
        if ws and ws.slug:
            return ws.slug.strip().lower()

    return "fp"  # default fallback workspace


def _resolve_relation_notion_ids(item: Any, session: Optional[Session] = None) -> tuple[Optional[str], Optional[str], list[str], list[str]]:
    """
    Resolve Notion IDs for Category, Types, Tags, and Elements from session or item dict.
    Returns (category_notion_id, type_notion_id, tag_notion_ids, element_notion_ids).
    """
    cat_notion_id = None
    type_notion_id = None
    tag_notion_ids: list[str] = []
    element_notion_ids: list[str] = []

    if isinstance(item, dict):
        cat_notion_id = item.get("category_notion_id") or item.get("notion_id")
        type_notion_id = item.get("type_notion_id")
        tag_notion_ids = list(item.get("tag_notion_ids") or [])
        element_notion_ids = list(item.get("element_notion_ids") or [])
        cat_id = item.get("category_id") or item.get("predicted_category_id")
        type_id = item.get("types_id") or item.get("predicted_types_id")
        tag_ids = item.get("tag_ids") or []
        element_ids = item.get("element_ids") or []
    else:
        cat_notion_id = getattr(item, "category_notion_id", None) or getattr(item, "notion_id", None)
        type_notion_id = getattr(item, "type_notion_id", None)
        tag_notion_ids = list(getattr(item, "tag_notion_ids", []) or [])
        element_notion_ids = list(getattr(item, "element_notion_ids", []) or [])
        cat_id = getattr(item, "category_id", None)
        type_id = getattr(item, "types_id", None)
        tag_ids = getattr(item, "tag_ids", []) or []
        element_ids = getattr(item, "element_ids", []) or []

    if session:
        if not cat_notion_id and cat_id:
            cat_obj = session.get(Category, cat_id)
            if cat_obj and cat_obj.notion_id:
                cat_notion_id = cat_obj.notion_id

        if not type_notion_id and type_id:
            type_obj = session.get(Types, type_id)
            if type_obj and type_obj.notion_id:
                type_notion_id = type_obj.notion_id

        if not tag_notion_ids and tag_ids:
            for tid in tag_ids:
                tag_obj = session.get(Tag, tid)
                if tag_obj and tag_obj.notion_id:
                    tag_notion_ids.append(tag_obj.notion_id)

        if not element_notion_ids and element_ids:
            for eid in element_ids:
                el_obj = session.get(Element, eid)
                if el_obj and el_obj.notion_id:
                    element_notion_ids.append(el_obj.notion_id)

    return cat_notion_id, type_notion_id, tag_notion_ids, element_notion_ids


async def stream_batch_to_notion(
    items: list[Any],
    session: Optional[Session] = None,
    dry_run: bool = False,
    rate_limit_delay: float = 0.4,
) -> AsyncGenerator[str, None]:
    """
    SSE generator that streams progress updates while syncing items to Notion.
    Enforces Full-Site workspace isolation, rate limiting delay, and deduplication.
    """
    total = len(items)
    created_count = 0
    updated_count = 0
    skipped_count = 0
    failed_count = 0

    clients: dict[str, Optional[Client]] = {}

    for idx, item in enumerate(items, start=1):
        # Extract basic info
        url = (
            item.get("url") or item.get("clean_url")
            if isinstance(item, dict)
            else getattr(item, "url", None) or getattr(item, "clean_url", None)
        )
        title_raw = (
            item.get("title") or item.get("name")
            if isinstance(item, dict)
            else getattr(item, "title", None) or getattr(item, "name", None)
        )
        title = clean_notion_title(title_raw or url or "Untitled")
        page_id = (
            item.get("id") or item.get("page_id")
            if isinstance(item, dict)
            else getattr(item, "id", None)
        )

        ws_slug = _resolve_item_workspace_slug(item, session)

        # ── 1. Strict Full-Site & FFFF Isolation ──────────────────────────
        if ws_slug in ("full-site", "fullsite", "ffff"):
            skipped_count += 1
            event = {
                "index": idx,
                "total": total,
                "url": url,
                "name": title,
                "workspace": ws_slug,
                "status": "skipped",
                "reason": f"Workspace '{ws_slug}' is internal and isolated from Notion",
                "notion_page_id": None,
                "notion_url": None,
                "error": None,
            }
            yield f"data: {json.dumps(event)}\n\n"
            continue

        # ── 2. Payload Construction & Mapping ──────────────────────────────
        cat_notion_id, type_notion_id, tag_notion_ids, element_notion_ids = _resolve_relation_notion_ids(item, session)

        if ws_slug in ("qfwfq", "goods"):
            payload = map_goods_payload(
                item,
                category_notion_id=cat_notion_id,
                type_notion_id=type_notion_id,
                tag_notion_ids=tag_notion_ids,
                element_notion_ids=element_notion_ids,
            )
        else:
            payload = map_services_payload(
                item,
                category_notion_id=cat_notion_id,
                type_notion_id=type_notion_id,
                tag_notion_ids=tag_notion_ids,
            )

        # ── 3. Dry-Run Mode ───────────────────────────────────────────────
        if dry_run:
            event = {
                "index": idx,
                "total": total,
                "url": url,
                "name": title,
                "workspace": ws_slug,
                "status": "dry_run",
                "notion_page_id": "dry-run-preview",
                "notion_url": None,
                "payload": payload,
                "error": None,
            }
            yield f"data: {json.dumps(event)}\n\n"
            if rate_limit_delay > 0:
                await asyncio.sleep(min(rate_limit_delay, 0.05))
            continue

        # ── 4. Notion Client & Target Database Resolution ─────────────────
        if ws_slug not in clients:
            clients[ws_slug] = get_notion_client(ws_slug)

        client = clients[ws_slug]
        if not client:
            failed_count += 1
            event = {
                "index": idx,
                "total": total,
                "url": url,
                "name": title,
                "workspace": ws_slug,
                "status": "failed",
                "notion_page_id": None,
                "notion_url": None,
                "error": f"No Notion API token configured for workspace '{ws_slug}'",
            }
            yield f"data: {json.dumps(event)}\n\n"
            continue

        db_id = get_target_db_id(ws_slug)
        if not db_id:
            failed_count += 1
            event = {
                "index": idx,
                "total": total,
                "url": url,
                "name": title,
                "workspace": ws_slug,
                "status": "failed",
                "notion_page_id": None,
                "notion_url": None,
                "error": f"No target Notion database configured for workspace '{ws_slug}'",
            }
            yield f"data: {json.dumps(event)}\n\n"
            continue

        # ── 5. URL Deduplication & Write Execution ─────────────────────────
        notion_page_id = None
        status = "created"
        error_msg = None

        try:
            # Check deduplication
            existing_page_id = check_url_exists(client, db_id, url) if url else None

            if existing_page_id:
                # Update existing page
                updated_page = client.pages.update(page_id=existing_page_id, properties=payload)
                notion_page_id = updated_page.get("id", existing_page_id)
                status = "updated"
                updated_count += 1
            else:
                # Create new page
                new_page = client.pages.create(parent={"database_id": db_id}, properties=payload)
                notion_page_id = new_page.get("id")
                status = "created"
                created_count += 1

            # Update DB Page model if page_id and session available
            if session and page_id and isinstance(page_id, int):
                page_rec = session.get(Page, page_id)
                if page_rec:
                    page_rec.notion_page_id = notion_page_id
                    page_rec.sync_status = "synced"
                    page_rec.last_synced_at = datetime.now(timezone.utc)
                    session.add(page_rec)
                    session.commit()

        except APIResponseError as e:
            if getattr(e, "code", None) == "rate_limited" or getattr(e, "status", None) == 429:
                logger.warning(f"Notion API rate limit hit for {url}, backing off 1.0s...")
                await asyncio.sleep(1.0)
                try:
                    new_page = client.pages.create(parent={"database_id": db_id}, properties=payload)
                    notion_page_id = new_page.get("id")
                    status = "created"
                    created_count += 1
                except Exception as retry_err:
                    status = "failed"
                    error_msg = f"Rate limited: {retry_err}"
                    failed_count += 1
            else:
                status = "failed"
                error_msg = str(e)
                failed_count += 1
        except Exception as e:
            logger.error(f"Error syncing {url} to Notion: {e}")
            status = "failed"
            error_msg = str(e)
            failed_count += 1

        notion_url = (
            f"https://notion.so/{notion_page_id.replace('-', '')}"
            if notion_page_id and notion_page_id != "dry-run-preview"
            else None
        )

        event = {
            "index": idx,
            "total": total,
            "url": url,
            "name": title,
            "workspace": ws_slug,
            "status": status,
            "notion_page_id": notion_page_id,
            "notion_url": notion_url,
            "error": error_msg,
        }
        yield f"data: {json.dumps(event)}\n\n"

        # Sequential rate-limiting delay between write requests
        if rate_limit_delay > 0:
            await asyncio.sleep(rate_limit_delay)

    # Final summary event
    summary = {
        "type": "complete",
        "total": total,
        "created": created_count,
        "updated": updated_count,
        "skipped": skipped_count,
        "failed": failed_count,
        "dry_run": dry_run,
    }
    yield f"data: {json.dumps(summary)}\n\n"
