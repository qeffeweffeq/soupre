"""
Schema synchronization engine between Notion databases, canonical local
snapshots (_taxonomies.json), and the SQLModel database.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional
from sqlmodel import Session, select

from database.models import Category, Tag, Types, Workspace, Element
from database.seed_taxonomies import seed_taxonomies, slugify
from modules.notion.client import (
    get_notion_client,
    FP_DATABASES,
    QFWFQ_DATABASES,
    SERVICES_DB_ID,
    ROLES_DB_ID,
    FP_TYPES_DB_ID,
    FP_TAGS_DB_ID,
    FP_CREATIVES_TAGS_DB_ID,
    FP_PALACE_LIDO_TAGS_DB_ID,
    GOODS_DB_ID,
    QFWFQ_TYPE_DB_ID,
    QFWFQ_ELEMENTS_DB_ID,
    QFWFQ_TAGS_DB_ID,
    QFWFQ_PALACE_LIDO_TAGS_DB_ID,
)

logger = logging.getLogger(__name__)


def sync_local_snapshots(session: Session, dbs_dir: Optional[Path] = None) -> dict[str, Any]:
    """
    Sync schemas and taxonomies from canonical local snapshots into SQLModel.
    Sets canonical Notion database IDs for Workspaces, Categories, Types, and Tags.
    """
    # 1. Run canonical seed if tables need population
    seed_taxonomies(session, dbs_dir=dbs_dir)

    # 2. Update Workspaces with target database IDs
    fp_ws = session.exec(select(Workspace).where(Workspace.slug == "fp")).first()
    if fp_ws:
        fp_ws.notion_id = SERVICES_DB_ID
        session.add(fp_ws)

    qfwfq_ws = session.exec(select(Workspace).where(Workspace.slug == "qfwfq")).first()
    if qfwfq_ws:
        qfwfq_ws.notion_id = GOODS_DB_ID
        session.add(qfwfq_ws)

    full_site_ws = session.exec(select(Workspace).where(Workspace.slug == "full-site")).first()
    if full_site_ws:
        full_site_ws.notion_id = None  # Full-Site is strictly isolated
        session.add(full_site_ws)

    # 3. Update Categories with canonical Notion IDs
    services_cats = session.exec(select(Category).where(Category.section == "services")).all()
    for cat in services_cats:
        if not cat.notion_id:
            cat.notion_id = ROLES_DB_ID
            session.add(cat)

    goods_cats = session.exec(select(Category).where(Category.section == "goods")).all()
    for cat in goods_cats:
        if not cat.notion_id:
            cat.notion_id = GOODS_DB_ID
            session.add(cat)

    session.commit()

    total_cats = len(session.exec(select(Category)).all())
    total_types = len(session.exec(select(Types)).all())
    total_tags = len(session.exec(select(Tag)).all())
    total_elements = len(session.exec(select(Element)).all())

    return {
        "status": "success",
        "mode": "local",
        "categories": total_cats,
        "types": total_types,
        "tags": total_tags,
        "elements": total_elements,
        "message": "Local schema snapshots synchronized successfully.",
    }


def _extract_page_title(page: dict[str, Any]) -> str:
    """Extract page title plain text from a Notion page record."""
    properties = page.get("properties", {})
    for key in ("Name", "Title", "Role", "Type", "Tag", "Service"):
        prop = properties.get(key)
        if prop and prop.get("type") == "title":
            title_list = prop.get("title", [])
            if title_list:
                return title_list[0].get("plain_text", "").strip()
    return ""


def sync_remote_notion(session: Session) -> dict[str, Any]:
    """
    Traverse live Notion relation databases, query pages, and update
    Category, Types, and Tag models with exact page Notion IDs.
    Falls back gracefully to local snapshots if Notion client is unavailable.
    """
    fp_client = get_notion_client("fp")
    qfwfq_client = get_notion_client("qfwfq")

    if not fp_client and not qfwfq_client:
        logger.warning("No Notion API tokens found for remote sync, falling back to local snapshots.")
        res = sync_local_snapshots(session)
        res["mode"] = "remote_fallback_local"
        res["message"] = "No Notion API tokens configured; synchronized using local snapshots."
        return res

    # First ensure base taxonomies are present
    sync_local_snapshots(session)

    categories_updated = 0
    types_updated = 0
    tags_updated = 0
    elements_updated = 0

    # ── FP Workspace Queries ───────────────────────────────────────────────
    if fp_client:
        # 1. Query Roles DB (Services Categories)
        try:
            roles_res = fp_client.databases.query(database_id=ROLES_DB_ID, page_size=100)
            for page in roles_res.get("results", []):
                title = _extract_page_title(page)
                page_id = page.get("id")
                if title and page_id:
                    slug = slugify(title)
                    cat = session.exec(
                        select(Category).where(
                            (Category.section == "services") &
                            ((Category.slug == slug) | (Category.name.ilike(title)))
                        )
                    ).first()
                    if cat:
                        cat.notion_id = page_id
                        session.add(cat)
                        categories_updated += 1
        except Exception as e:
            logger.warning(f"Error querying FP Roles DB: {e}")

        # 2. Query Types DB
        try:
            types_res = fp_client.databases.query(database_id=FP_TYPES_DB_ID, page_size=100)
            for page in types_res.get("results", []):
                title = _extract_page_title(page)
                page_id = page.get("id")
                if title and page_id:
                    slug = slugify(title)
                    type_obj = session.exec(
                        select(Types).where(
                            (Types.slug == slug) | (Types.name.ilike(title))
                        )
                    ).first()
                    if type_obj:
                        type_obj.notion_id = page_id
                        session.add(type_obj)
                        types_updated += 1
        except Exception as e:
            logger.warning(f"Error querying FP Types DB: {e}")

        # 3. Query Tags DBs (FP Work Tags, Creatives Tags, Palace Lido Tags)
        for tag_db_id in (FP_TAGS_DB_ID, FP_CREATIVES_TAGS_DB_ID, FP_PALACE_LIDO_TAGS_DB_ID):
            try:
                tags_res = fp_client.databases.query(database_id=tag_db_id, page_size=100)
                for page in tags_res.get("results", []):
                    title = _extract_page_title(page)
                    page_id = page.get("id")
                    if title and page_id:
                        slug = slugify(title)
                        tag_obj = session.exec(
                            select(Tag).where(
                                (Tag.slug == slug) | (Tag.name.ilike(title))
                            )
                        ).first()
                        if tag_obj:
                            tag_obj.notion_id = page_id
                            session.add(tag_obj)
                            tags_updated += 1
            except Exception as e:
                logger.warning(f"Error querying FP Tags DB {tag_db_id}: {e}")

    # ── QFWFQ Workspace Queries ───────────────────────────────────────────
    if qfwfq_client:
        # 1. Query Goods / Categories DB
        try:
            goods_res = qfwfq_client.databases.query(database_id=GOODS_DB_ID, page_size=100)
            for page in goods_res.get("results", []):
                title = _extract_page_title(page)
                page_id = page.get("id")
                if title and page_id:
                    slug = slugify(title)
                    cat = session.exec(
                        select(Category).where(
                            (Category.section == "goods") &
                            ((Category.slug == slug) | (Category.name.ilike(title)))
                        )
                    ).first()
                    if cat:
                        cat.notion_id = page_id
                        session.add(cat)
                        categories_updated += 1
        except Exception as e:
            logger.warning(f"Error querying QFWFQ Goods DB: {e}")

        # 2. Query Goods Type DB
        try:
            gtypes_res = qfwfq_client.databases.query(database_id=QFWFQ_TYPE_DB_ID, page_size=100)
            for page in gtypes_res.get("results", []):
                title = _extract_page_title(page)
                page_id = page.get("id")
                if title and page_id:
                    slug = slugify(title)
                    type_obj = session.exec(
                        select(Types).where(
                            (Types.slug == slug) | (Types.name.ilike(title))
                        )
                    ).first()
                    if type_obj:
                        type_obj.notion_id = page_id
                        session.add(type_obj)
                        types_updated += 1
        except Exception as e:
            logger.warning(f"Error querying QFWFQ Types DB: {e}")

        # 3. Query Qfwfq Tags DBs
        for q_tag_db_id in (QFWFQ_TAGS_DB_ID, QFWFQ_PALACE_LIDO_TAGS_DB_ID):
            try:
                qtags_res = qfwfq_client.databases.query(database_id=q_tag_db_id, page_size=100)
                for page in qtags_res.get("results", []):
                    title = _extract_page_title(page)
                    page_id = page.get("id")
                    if title and page_id:
                        slug = slugify(title)
                        tag_obj = session.exec(
                            select(Tag).where(
                                (Tag.slug == slug) | (Tag.name.ilike(title))
                            )
                        ).first()
                        if tag_obj:
                            tag_obj.notion_id = page_id
                            session.add(tag_obj)
                            tags_updated += 1
            except Exception as e:
                logger.warning(f"Error querying QFWFQ Tags DB {q_tag_db_id}: {e}")

        # 4. Query Qfwfq Elements DB
        try:
            has_more = True
            start_cursor = None
            while has_more:
                query_kwargs: dict[str, Any] = {"database_id": QFWFQ_ELEMENTS_DB_ID, "page_size": 100}
                if start_cursor:
                    query_kwargs["start_cursor"] = start_cursor
                elem_res = qfwfq_client.databases.query(**query_kwargs)
                for page in elem_res.get("results", []):
                    title = _extract_page_title(page)
                    page_id = page.get("id")
                    if title and page_id:
                        slug = slugify(title)
                        el_obj = session.exec(
                            select(Element).where(
                                (Element.slug == slug) | (Element.name.ilike(title))
                            )
                        ).first()
                        if el_obj:
                            el_obj.notion_id = page_id
                            session.add(el_obj)
                            elements_updated += 1
                has_more = elem_res.get("has_more", False)
                start_cursor = elem_res.get("next_cursor")
        except Exception as e:
            logger.warning(f"Error querying QFWFQ Elements DB: {e}")

    session.commit()

    total_cats = len(session.exec(select(Category)).all())
    total_types = len(session.exec(select(Types)).all())
    total_tags = len(session.exec(select(Tag)).all())
    total_elements = len(session.exec(select(Element)).all())

    return {
        "status": "success",
        "mode": "remote",
        "categories": total_cats,
        "types": total_types,
        "tags": total_tags,
        "elements": total_elements,
        "updated": {
            "categories": categories_updated,
            "types": types_updated,
            "tags": tags_updated,
            "elements": elements_updated,
        },
        "message": f"Remote Notion schema synced ({categories_updated} categories, {types_updated} types, {tags_updated} tags, {elements_updated} elements updated with live page IDs).",
    }


def sync_schemas(session: Session, mode: str = "local") -> dict[str, Any]:
    """
    Public entry point for schema synchronization.
    Supports mode='local' (snapshots) and mode='remote' (live Notion API).
    """
    mode = (mode or "local").lower().strip()
    if mode == "remote":
        return sync_remote_notion(session)
    return sync_local_snapshots(session)
