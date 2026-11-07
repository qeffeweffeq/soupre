from collections import defaultdict
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from sqlalchemy import func

from database.models import Workspace, Category, Types, Tag, Genre, Page, PageTagLink, ScrapeJob
from database.session import get_session
from database.seed_taxonomies import slugify
from schemas.taxonomies import (
    WorkspaceCreate,
    WorkspaceUpdate,
    CategoryCreate,
    CategoryUpdate,
    TypesCreate,
    TypesUpdate,
    TagCreate,
    TagUpdate,
    BulkTagAssignRequest,
    GenreCreate,
    GenreUpdate,
)
from modules.serializers import serialize_page_enriched

router = APIRouter(prefix="/api/taxonomies", tags=["taxonomies"])


@router.get("/workspaces")
def list_workspaces(session: Session = Depends(get_session)):
    """List all workspaces with attached counts."""
    workspaces = session.exec(select(Workspace).order_by(Workspace.name)).all()
    results = []
    for w in workspaces:
        cat_count = session.exec(select(func.count(Category.id)).where(Category.workspace_id == w.id)).one() or 0
        job_count = session.exec(select(func.count(ScrapeJob.id)).where(ScrapeJob.workspace_id == w.id)).one() or 0
        page_count = session.exec(select(func.count(Page.id)).where(Page.workspace_id == w.id)).one() or 0
        results.append({
            "id": w.id,
            "name": w.name,
            "slug": w.slug,
            "icon": w.icon or "📁",
            "color": w.color or "mauve",
            "description": w.description,
            "notion_id": w.notion_id,
            "category_count": cat_count,
            "job_count": job_count,
            "page_count": page_count,
        })
    return results


@router.post("/workspaces")
def create_workspace(payload: WorkspaceCreate, session: Session = Depends(get_session)):
    """Create a new workspace."""
    slug = payload.slug or slugify(payload.name)
    existing = session.exec(select(Workspace).where(Workspace.slug == slug)).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Workspace with slug '{slug}' already exists")
    ws = Workspace(
        name=payload.name,
        slug=slug,
        icon=payload.icon or "📁",
        color=payload.color or "mauve",
        description=payload.description,
        notion_id=payload.notion_id,
    )
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


@router.put("/workspaces/{workspace_id}")
def update_workspace(workspace_id: int, payload: WorkspaceUpdate, session: Session = Depends(get_session)):
    """Update workspace details."""
    ws = session.get(Workspace, workspace_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    if payload.name is not None:
        ws.name = payload.name
        if payload.slug is None:
            ws.slug = slugify(payload.name)
    if payload.slug is not None:
        ws.slug = payload.slug
    if payload.icon is not None:
        ws.icon = payload.icon
    if payload.color is not None:
        ws.color = payload.color
    if payload.description is not None:
        ws.description = payload.description
    if payload.notion_id is not None:
        ws.notion_id = payload.notion_id
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


@router.delete("/workspaces/{workspace_id}")
def delete_workspace(workspace_id: int, session: Session = Depends(get_session)):
    """Delete workspace and unlink child categories, jobs, and pages."""
    ws = session.get(Workspace, workspace_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    categories = session.exec(select(Category).where(Category.workspace_id == workspace_id)).all()
    for c in categories:
        c.workspace_id = None
        session.add(c)
    jobs = session.exec(select(ScrapeJob).where(ScrapeJob.workspace_id == workspace_id)).all()
    for j in jobs:
        j.workspace_id = None
        session.add(j)
    pages = session.exec(select(Page).where(Page.workspace_id == workspace_id)).all()
    for p in pages:
        p.workspace_id = None
        session.add(p)
    session.delete(ws)
    session.commit()
    return {"status": "deleted", "id": workspace_id}


@router.get("/explorer")
def get_taxonomies_explorer(
    status: Optional[str] = None,
    workspace_id: Optional[str] = None,
    session: Session = Depends(get_session),
):
    target_ws_id: Optional[int] = None
    if workspace_id and workspace_id not in ("all", "__all__"):
        if workspace_id.isdigit():
            target_ws_id = int(workspace_id)
        else:
            ws = session.exec(select(Workspace).where(Workspace.slug == workspace_id)).first()
            if ws:
                target_ws_id = ws.id

    cat_query = select(Category)
    if target_ws_id is not None:
        cat_query = cat_query.where(Category.workspace_id == target_ws_id)
    categories = session.exec(cat_query.order_by(Category.name)).all()
    all_types = session.exec(select(Types).order_by(Types.name)).all()

    query = select(Page)
    if status is not None:
        query = query.where(Page.status == status)
    if target_ws_id is not None:
        query = query.where(Page.workspace_id == target_ws_id)
    pages = session.exec(query).all()

    job_ids = {p.job_id for p in pages if p.job_id is not None}
    jobs_map = {}
    if job_ids:
        jobs = session.exec(select(ScrapeJob).where(ScrapeJob.id.in_(job_ids))).all()
        jobs_map = {j.id: j for j in jobs}

    pages_by_cat_and_type = defaultdict(list)
    pages_by_cat_no_type = defaultdict(list)
    unassigned_pages = []

    for p in pages:
        sp = serialize_page_enriched(p, jobs_map.get(p.job_id))
        if p.category_id is None:
            unassigned_pages.append(sp)
        elif p.types_id is None:
            pages_by_cat_no_type[p.category_id].append(sp)
        else:
            pages_by_cat_and_type[(p.category_id, p.types_id)].append(sp)

    types_by_cat = defaultdict(list)
    for t in all_types:
        types_by_cat[t.category_id].append(t)

    tree = []
    for cat in categories:
        cat_types = types_by_cat.get(cat.id, [])
        cat_page_total = 0
        types_data = []

        for t in cat_types:
            t_pages = pages_by_cat_and_type.get((cat.id, t.id), [])
            cat_page_total += len(t_pages)
            types_data.append({
                "id": t.id,
                "category_id": t.category_id,
                "name": t.name,
                "slug": t.slug,
                "description": t.description,
                "notion_id": t.notion_id,
                "page_count": len(t_pages),
                "pages": t_pages,
            })

        cat_untyped = pages_by_cat_no_type.get(cat.id, [])
        cat_page_total += len(cat_untyped)
        if cat_untyped:
            types_data.append({
                "id": None,
                "category_id": cat.id,
                "name": "General / Other",
                "slug": "general",
                "description": None,
                "notion_id": None,
                "page_count": len(cat_untyped),
                "pages": cat_untyped,
            })

        ws = cat.workspace
        tree.append({
            "id": cat.id,
            "workspace_id": cat.workspace_id,
            "workspace_name": ws.name if ws else None,
            "workspace_slug": ws.slug if ws else None,
            "workspace_icon": ws.icon if ws else None,
            "workspace_color": ws.color if ws else None,
            "name": cat.name,
            "slug": cat.slug,
            "section": cat.section,
            "description": cat.description,
            "notion_id": cat.notion_id,
            "page_count": cat_page_total,
            "types": types_data,
            "uncategorized_pages": cat_untyped,
        })

    if unassigned_pages:
        tree.append({
            "id": None,
            "workspace_id": target_ws_id,
            "name": "Uncategorized",
            "slug": "uncategorized",
            "description": "Pages without an assigned category",
            "page_count": len(unassigned_pages),
            "types": [
                {
                    "id": None,
                    "category_id": None,
                    "name": "All Unassigned",
                    "slug": "unassigned",
                    "description": None,
                    "notion_id": None,
                    "page_count": len(unassigned_pages),
                    "pages": unassigned_pages,
                }
            ],
            "uncategorized_pages": unassigned_pages,
        })

    return tree


@router.get("/tree")
def get_taxonomies_tree(
    workspace_id: Optional[str] = None,
    session: Session = Depends(get_session),
):
    target_ws_id: Optional[int] = None
    if workspace_id and workspace_id not in ("all", "__all__"):
        if workspace_id.isdigit():
            target_ws_id = int(workspace_id)
        else:
            ws = session.exec(select(Workspace).where(Workspace.slug == workspace_id)).first()
            if ws:
                target_ws_id = ws.id

    cat_query = select(Category)
    if target_ws_id is not None:
        cat_query = cat_query.where(Category.workspace_id == target_ws_id)
    categories = session.exec(cat_query.order_by(Category.name)).all()
    tree = []
    for cat in categories:
        types_list = session.exec(
            select(Types).where(Types.category_id == cat.id).order_by(Types.name)
        ).all()
        types_data = []
        cat_page_count = session.exec(
            select(func.count(Page.id)).where(Page.category_id == cat.id)
        ).one() or 0
        for t in types_list:
            type_page_count = session.exec(
                select(func.count(Page.id)).where(Page.types_id == t.id)
            ).one() or 0
            types_data.append({
                "id": t.id,
                "category_id": t.category_id,
                "name": t.name,
                "slug": t.slug,
                "description": t.description,
                "notion_id": t.notion_id,
                "page_count": type_page_count,
            })
        ws = cat.workspace
        tree.append({
            "id": cat.id,
            "workspace_id": cat.workspace_id,
            "workspace_name": ws.name if ws else None,
            "workspace_slug": ws.slug if ws else None,
            "workspace_icon": ws.icon if ws else None,
            "workspace_color": ws.color if ws else None,
            "name": cat.name,
            "slug": cat.slug,
            "section": cat.section,
            "description": cat.description,
            "notion_id": cat.notion_id,
            "page_count": cat_page_count,
            "types": types_data,
        })
    return tree


@router.get("/categories")
def list_categories(
    workspace_id: Optional[str] = None,
    session: Session = Depends(get_session)
):
    query = select(Category)
    if workspace_id and workspace_id not in ("all", "__all__"):
        if workspace_id.isdigit():
            query = query.where(Category.workspace_id == int(workspace_id))
        else:
            ws = session.exec(select(Workspace).where(Workspace.slug == workspace_id)).first()
            if ws:
                query = query.where(Category.workspace_id == ws.id)
    return session.exec(query.order_by(Category.name)).all()


@router.post("/categories")
def create_category(payload: CategoryCreate, session: Session = Depends(get_session)):
    slug = payload.slug or slugify(payload.name)
    existing = session.exec(select(Category).where(Category.slug == slug)).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Category with slug '{slug}' already exists")
    if payload.workspace_id is not None:
        ws = session.get(Workspace, payload.workspace_id)
        if not ws:
            raise HTTPException(status_code=404, detail="Workspace not found")
    category = Category(
        name=payload.name,
        slug=slug,
        workspace_id=payload.workspace_id,
        section=payload.section,
        description=payload.description,
        notion_id=payload.notion_id,
    )
    session.add(category)
    session.commit()
    session.refresh(category)
    return category


@router.put("/categories/{category_id}")
def update_category(category_id: int, payload: CategoryUpdate, session: Session = Depends(get_session)):
    category = session.get(Category, category_id)
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    fields_set = payload.model_fields_set
    if "workspace_id" in fields_set:
        if payload.workspace_id is not None:
            ws = session.get(Workspace, payload.workspace_id)
            if not ws:
                raise HTTPException(status_code=404, detail="Workspace not found")
        category.workspace_id = payload.workspace_id
    if payload.name is not None:
        category.name = payload.name
        if payload.slug is None:
            category.slug = slugify(payload.name)
    if payload.slug is not None:
        category.slug = payload.slug
    if payload.section is not None:
        category.section = payload.section
    if payload.description is not None:
        category.description = payload.description
    if payload.notion_id is not None:
        category.notion_id = payload.notion_id
    session.add(category)
    session.commit()
    session.refresh(category)
    return category


@router.delete("/categories/{category_id}")
def delete_category(category_id: int, session: Session = Depends(get_session)):
    category = session.get(Category, category_id)
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    pages = session.exec(select(Page).where(Page.category_id == category_id)).all()
    for p in pages:
        p.category_id = None
        session.add(p)
    types_list = session.exec(select(Types).where(Types.category_id == category_id)).all()
    for t in types_list:
        sub_pages = session.exec(select(Page).where(Page.types_id == t.id)).all()
        for sp in sub_pages:
            sp.types_id = None
            session.add(sp)
        session.delete(t)
    session.delete(category)
    session.commit()
    return {"status": "deleted", "id": category_id}


@router.get("/types")
def list_types(category_id: Optional[int] = None, session: Session = Depends(get_session)):
    query = select(Types)
    if category_id is not None:
        query = query.where(Types.category_id == category_id)
    return session.exec(query.order_by(Types.name)).all()


@router.post("/types")
def create_types(payload: TypesCreate, session: Session = Depends(get_session)):
    parent_cat = session.get(Category, payload.category_id)
    if not parent_cat:
        raise HTTPException(status_code=404, detail="Parent category not found")
    slug = payload.slug or slugify(payload.name)
    existing = session.exec(select(Types).where(Types.slug == slug)).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Types with slug '{slug}' already exists")
    item_type = Types(
        category_id=payload.category_id,
        name=payload.name,
        slug=slug,
        description=payload.description,
        notion_id=payload.notion_id,
    )
    session.add(item_type)
    session.commit()
    session.refresh(item_type)
    return item_type


@router.put("/types/{types_id}")
def update_types(types_id: int, payload: TypesUpdate, session: Session = Depends(get_session)):
    item_type = session.get(Types, types_id)
    if not item_type:
        raise HTTPException(status_code=404, detail="Types not found")
    if payload.category_id is not None:
        parent_cat = session.get(Category, payload.category_id)
        if not parent_cat:
            raise HTTPException(status_code=404, detail="Parent category not found")
        item_type.category_id = payload.category_id
    if payload.name is not None:
        item_type.name = payload.name
        if payload.slug is None:
            item_type.slug = slugify(payload.name)
    if payload.slug is not None:
        item_type.slug = payload.slug
    if payload.description is not None:
        item_type.description = payload.description
    if payload.notion_id is not None:
        item_type.notion_id = payload.notion_id
    session.add(item_type)
    session.commit()
    session.refresh(item_type)
    return item_type


@router.delete("/types/{types_id}")
def delete_types(types_id: int, session: Session = Depends(get_session)):
    item_type = session.get(Types, types_id)
    if not item_type:
        raise HTTPException(status_code=404, detail="Types not found")
    pages = session.exec(select(Page).where(Page.types_id == types_id)).all()
    for p in pages:
        p.types_id = None
        session.add(p)
    session.delete(item_type)
    session.commit()
    return {"status": "deleted", "id": types_id}


@router.get("/tags")
def list_tags(
    workspace_id: Optional[int] = None,
    session: Session = Depends(get_session),
):
    query = select(Tag)
    if workspace_id is not None:
        query = query.where((Tag.workspace_id == workspace_id) | (Tag.workspace_id.is_(None)))
    tags = session.exec(query.order_by(Tag.name)).all()

    # Pre-aggregate page counts per tag
    counts_res = session.exec(
        select(PageTagLink.tag_id, func.count(PageTagLink.page_id))
        .group_by(PageTagLink.tag_id)
    ).all()
    count_map = {tid: cnt for tid, cnt in counts_res}

    results = []
    for t in tags:
        results.append({
            "id": t.id,
            "workspace_id": t.workspace_id,
            "workspace_name": t.workspace.name if t.workspace else None,
            "workspace_slug": t.workspace.slug if t.workspace else None,
            "name": t.name,
            "slug": t.slug,
            "section": t.section,
            "notion_id": t.notion_id,
            "page_count": count_map.get(t.id, 0),
        })
    return results


@router.post("/tags")
def create_tag(payload: TagCreate, session: Session = Depends(get_session)):
    slug = payload.slug or slugify(payload.name)
    existing = session.exec(select(Tag).where(Tag.slug == slug)).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Tag with slug '{slug}' already exists")
    tag = Tag(
        name=payload.name,
        slug=slug,
        workspace_id=payload.workspace_id,
        section=payload.section,
        notion_id=payload.notion_id,
    )
    session.add(tag)
    session.commit()
    session.refresh(tag)
    return tag


@router.put("/tags/{tag_id}")
def update_tag(tag_id: int, payload: TagUpdate, session: Session = Depends(get_session)):
    tag = session.get(Tag, tag_id)
    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")
    if payload.name is not None:
        tag.name = payload.name
        if payload.slug is None:
            tag.slug = slugify(payload.name)
    if payload.slug is not None:
        tag.slug = payload.slug
    if payload.workspace_id is not None:
        tag.workspace_id = payload.workspace_id
    if payload.section is not None:
        tag.section = payload.section
    if payload.notion_id is not None:
        tag.notion_id = payload.notion_id
    session.add(tag)
    session.commit()
    session.refresh(tag)
    return tag


@router.post("/tags/bulk-assign")
def bulk_assign_tags(payload: BulkTagAssignRequest, session: Session = Depends(get_session)):
    """Assign a list of tag IDs to a list of page IDs."""
    if not payload.page_ids or not payload.tag_ids:
        return {"assigned_count": 0, "pages_affected": 0}

    valid_tags = session.exec(select(Tag).where(Tag.id.in_(payload.tag_ids))).all()
    valid_tag_ids = [t.id for t in valid_tags]
    if not valid_tag_ids:
        raise HTTPException(status_code=400, detail="No valid tag IDs provided")

    assigned_count = 0
    pages_affected = set()

    for pid in payload.page_ids:
        page = session.get(Page, pid)
        if not page:
            continue
        existing_tids = {t.id for t in page.tags}
        for tid in valid_tag_ids:
            if tid not in existing_tids:
                link = PageTagLink(page_id=pid, tag_id=tid)
                session.add(link)
                assigned_count += 1
                pages_affected.add(pid)

    session.commit()
    return {
        "status": "success",
        "assigned_count": assigned_count,
        "pages_affected": len(pages_affected),
    }


@router.delete("/tags/{tag_id}")
def delete_tag(tag_id: int, session: Session = Depends(get_session)):
    tag = session.get(Tag, tag_id)
    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")
    links = session.exec(select(PageTagLink).where(PageTagLink.tag_id == tag_id)).all()
    for link in links:
        session.delete(link)
    session.delete(tag)
    session.commit()
    return {"status": "deleted", "id": tag_id}


@router.get("/genres")
def list_genres(session: Session = Depends(get_session)):
    return session.exec(select(Genre).order_by(Genre.name)).all()


@router.post("/genres")
def create_genre(payload: GenreCreate, session: Session = Depends(get_session)):
    slug = payload.slug or slugify(payload.name)
    existing = session.exec(select(Genre).where(Genre.slug == slug)).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Genre with slug '{slug}' already exists")
    genre = Genre(
        name=payload.name,
        slug=slug,
    )
    session.add(genre)
    session.commit()
    session.refresh(genre)
    return genre


@router.put("/genres/{genre_id}")
def update_genre(genre_id: int, payload: GenreUpdate, session: Session = Depends(get_session)):
    genre = session.get(Genre, genre_id)
    if not genre:
        raise HTTPException(status_code=404, detail="Genre not found")
    if payload.name is not None:
        genre.name = payload.name
        if payload.slug is None:
            genre.slug = slugify(payload.name)
    if payload.slug is not None:
        genre.slug = payload.slug
    session.add(genre)
    session.commit()
    session.refresh(genre)
    return genre


@router.delete("/genres/{genre_id}")
def delete_genre(genre_id: int, session: Session = Depends(get_session)):
    genre = session.get(Genre, genre_id)
    if not genre:
        raise HTTPException(status_code=404, detail="Genre not found")
    pages = session.exec(select(Page).where(Page.genre_id == genre_id)).all()
    for p in pages:
        p.genre_id = None
        session.add(p)
    session.delete(genre)
    session.commit()
    return {"status": "deleted", "id": genre_id}
