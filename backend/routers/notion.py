"""
FastAPI router for Notion integration: status health checks, batch SSE streaming,
single page synchronization, and taxonomy schema syncing.
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlmodel import Session

from database.models import Page
from database.session import get_session
from modules.notion.client import (
    discover_tokens,
    get_notion_client,
    get_target_db_id,
    FP_DATABASES,
    QFWFQ_DATABASES,
)
from modules.notion.mapper import (
    map_goods_payload,
    map_services_payload,
)
from modules.notion.streamer import (
    check_url_exists,
    stream_batch_to_notion,
)
from modules.notion.schema_syncer import sync_schemas

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/notion", tags=["notion"])


class NotionStreamRequest(BaseModel):
    items: list[dict[str, Any]] = Field(default_factory=list)
    dry_run: bool = False
    rate_limit_delay: float = 0.4


class SchemaSyncRequest(BaseModel):
    mode: str = "local"


@router.get("/status")
def get_notion_status():
    """
    Health check and configuration endpoint.
    Reports configured workspaces, discovered token names, and target database IDs.
    """
    tokens = discover_tokens()
    fp_configured = bool(tokens.get("NOTION_FP_TOKEN") or tokens.get("NOTION_TOKEN"))
    qfwfq_configured = bool(tokens.get("NOTION_QFWFQ_TOKEN") or tokens.get("NOTION_TOKEN"))

    return {
        "status": "ok",
        "configured_workspaces": {
            "fp": fp_configured,
            "qfwfq": qfwfq_configured,
            "full-site": False,  # Permanently unlinked & isolated
        },
        "tokens_found": [k for k in tokens.keys() if "TOKEN" in k],
        "databases": {
            "fp": FP_DATABASES,
            "qfwfq": QFWFQ_DATABASES,
        },
    }


@router.post("/stream")
async def stream_notion_batch(
    req: NotionStreamRequest,
    session: Session = Depends(get_session),
):
    """
    Server-Sent Events (SSE) streaming endpoint for pushing items to Notion.
    Emits progressive status updates per row, respecting rate limits and dry-run mode.
    """
    generator = stream_batch_to_notion(
        items=req.items,
        session=session,
        dry_run=req.dry_run,
        rate_limit_delay=req.rate_limit_delay,
    )
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/pages/{page_id}/sync")
def sync_single_page(
    page_id: int,
    dry_run: bool = Query(default=False),
    session: Session = Depends(get_session),
):
    """
    Synchronize an individual scraped Page into its target Notion database.
    Strictly forbids syncing pages assigned to the Full-Site workspace.
    """
    page = session.get(Page, page_id)
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")

    ws_slug = page.workspace.slug if page.workspace else "fp"
    if ws_slug in ("full-site", "fullsite"):
        raise HTTPException(
            status_code=400,
            detail="Pages in the Full-Site workspace cannot be synced to Notion (strictly isolated).",
        )

    cat_notion_id = page.category.notion_id if page.category else None
    type_notion_id = page.types_rel.notion_id if page.types_rel else None
    tag_notion_ids = [t.notion_id for t in page.tags if t.notion_id]

    if ws_slug in ("qfwfq", "goods"):
        payload = map_goods_payload(
            page,
            category_notion_id=cat_notion_id,
            type_notion_id=type_notion_id,
            tag_notion_ids=tag_notion_ids,
        )
    else:
        payload = map_services_payload(
            page,
            category_notion_id=cat_notion_id,
            type_notion_id=type_notion_id,
            tag_notion_ids=tag_notion_ids,
        )

    if dry_run:
        return {
            "status": "dry_run",
            "page_id": page.id,
            "notion_page_id": "dry-run-preview",
            "notion_url": None,
            "payload": payload,
        }

    client = get_notion_client(ws_slug)
    if not client:
        raise HTTPException(
            status_code=502,
            detail=f"Notion client is not configured for workspace '{ws_slug}'. Check your API token.",
        )

    db_id = get_target_db_id(ws_slug)
    if not db_id:
        raise HTTPException(
            status_code=500,
            detail=f"No target Notion database configured for workspace '{ws_slug}'.",
        )

    try:
        existing_page_id = check_url_exists(client, db_id, page.url) if page.url else None
        if existing_page_id:
            updated = client.pages.update(page_id=existing_page_id, properties=payload)
            notion_page_id = updated.get("id", existing_page_id)
            status = "updated"
        else:
            created = client.pages.create(parent={"database_id": db_id}, properties=payload)
            notion_page_id = created.get("id")
            status = "created"

        page.notion_page_id = notion_page_id
        page.sync_status = "synced"
        page.last_synced_at = datetime.now(timezone.utc)
        session.add(page)
        session.commit()

        return {
            "status": status,
            "page_id": page.id,
            "notion_page_id": notion_page_id,
            "notion_url": f"https://notion.so/{notion_page_id.replace('-', '')}" if notion_page_id else None,
        }
    except Exception as e:
        logger.error(f"Error syncing page {page_id} to Notion: {e}")
        page.sync_status = "failed"
        session.add(page)
        session.commit()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/schemas/sync")
def trigger_schema_sync(
    mode: str = Query(default="local", pattern="^(local|remote)$"),
    session: Session = Depends(get_session),
):
    """
    Trigger schema synchronization for taxonomies and categories.
    Mode:
      - 'local': updates/seeds from canonical JSON snapshots (_taxonomies.json).
      - 'remote': queries live Notion relation databases to link exact Notion page IDs.
    """
    result = sync_schemas(session, mode=mode)
    return result
