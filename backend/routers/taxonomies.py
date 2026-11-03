from collections import defaultdict
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from sqlalchemy import func

from database.models import Category, Types, Tag, Genre, Page, PageTagLink, ScrapeJob
from database.session import get_session
from database.seed_taxonomies import slugify
from schemas.taxonomies import (
    CategoryCreate,
    CategoryUpdate,
    TypesCreate,
    TypesUpdate,
    TagCreate,
    TagUpdate,
    GenreCreate,
    GenreUpdate,
)
from modules.serializers import serialize_page_enriched

router = APIRouter(prefix="/api/taxonomies", tags=["taxonomies"])


@router.get("/explorer")
def get_taxonomies_explorer(
    status: Optional[str] = None,
    session: Session = Depends(get_session),
):
    categories = session.exec(select(Category).order_by(Category.name)).all()
    all_types = session.exec(select(Types).order_by(Types.name)).all()

    query = select(Page)
    if status is not None:
        query = query.where(Page.status == status)
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

        tree.append({
            "id": cat.id,
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
def get_taxonomies_tree(session: Session = Depends(get_session)):
    categories = session.exec(select(Category).order_by(Category.name)).all()
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
        tree.append({
            "id": cat.id,
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
def list_categories(session: Session = Depends(get_session)):
    return session.exec(select(Category).order_by(Category.name)).all()


@router.post("/categories")
def create_category(payload: CategoryCreate, session: Session = Depends(get_session)):
    slug = payload.slug or slugify(payload.name)
    existing = session.exec(select(Category).where(Category.slug == slug)).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Category with slug '{slug}' already exists")
    category = Category(
        name=payload.name,
        slug=slug,
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
def list_tags(session: Session = Depends(get_session)):
    return session.exec(select(Tag).order_by(Tag.name)).all()


@router.post("/tags")
def create_tag(payload: TagCreate, session: Session = Depends(get_session)):
    slug = payload.slug or slugify(payload.name)
    existing = session.exec(select(Tag).where(Tag.slug == slug)).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Tag with slug '{slug}' already exists")
    tag = Tag(
        name=payload.name,
        slug=slug,
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
    if payload.notion_id is not None:
        tag.notion_id = payload.notion_id
    session.add(tag)
    session.commit()
    session.refresh(tag)
    return tag


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
