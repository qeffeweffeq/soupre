from urllib.parse import urlparse
from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException
from sqlmodel import Session, select

from database.models import Page, ScrapeJob, Category, Types, Genre, Tag, Workspace, Element
from database.session import get_session
from schemas.taxonomies import PageTaxonomyUpdate
from modules.serializers import serialize_page_enriched
from modules.utils.page_recovery import get_job_dir, cleanup_untitled_page_artifacts
from routers.jobs import _run_job_with_logging, _dispatch_run_job

router = APIRouter(prefix="/api/pages", tags=["pages"])


@router.post("/{page_id}/rescrape")
def rescrape_single_page(
    page_id: int,
    background_tasks: BackgroundTasks,
    session_min: int = 30,
    session_max: int = 50,
    cooldown_min: float = 180.0,
    cooldown_max: float = 300.0,
    delay_min: float = 60.0,
    delay_max: float = 120.0,
    session: Session = Depends(get_session),
):
    page = session.get(Page, page_id)
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")
    job = session.get(ScrapeJob, page.job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status == "running":
        raise HTTPException(status_code=400, detail="Job is already running")

    job_dir = get_job_dir(job)
    cleanup_untitled_page_artifacts(page, job_dir)
    page.status = "pending"
    if (page.title or "").strip().lower() in ("", "untitled", "untitled page"):
        page.title = page.url_path or urlparse(page.url).path or page.url
    page.markdown_path = None
    page.screenshot_path = None
    session.add(page)

    all_pages = session.exec(select(Page).where(Page.job_id == job.id)).all()
    job.scraped_pages = sum(1 for p in all_pages if p.status == "completed" and p.id != page.id)
    job.target_url = (job.target_url or "").strip()
    job.status = "running"
    session.add(job)
    session.commit()
    session.refresh(job)

    max_pages = max(0, job.total_pages or 0)
    background_tasks.add_task(
        _dispatch_run_job,
        job.id,
        job.target_url,
        job.scrape_mode,
        max_pages,
        delay_min,
        delay_max,
        session_min,
        session_max,
        cooldown_min,
        cooldown_max,
    )
    return {"status": "resumed", "job_id": job.id, "page_id": page.id}


@router.patch("/{page_id}/taxonomy")
@router.put("/{page_id}/taxonomy")
def update_page_taxonomy(
    page_id: int,
    payload: PageTaxonomyUpdate,
    session: Session = Depends(get_session)
):
    page = session.get(Page, page_id)
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")

    fields_set = payload.model_fields_set

    if "workspace_id" in fields_set:
        if payload.workspace_id is not None:
            ws = session.get(Workspace, payload.workspace_id)
            if not ws:
                raise HTTPException(status_code=404, detail="Workspace not found")
        page.workspace_id = payload.workspace_id

    if "category_id" in fields_set:
        if payload.category_id is not None:
            cat = session.get(Category, payload.category_id)
            if not cat:
                raise HTTPException(status_code=404, detail="Category not found")
            if "workspace_id" not in fields_set and cat.workspace_id:
                page.workspace_id = cat.workspace_id
        page.category_id = payload.category_id

    if "types_id" in fields_set:
        if payload.types_id is not None:
            t = session.get(Types, payload.types_id)
            if not t:
                raise HTTPException(status_code=404, detail="Types not found")
            if "category_id" not in fields_set and t.category_id:
                page.category_id = t.category_id
        page.types_id = payload.types_id

    # If category changed and types_id was not explicitly specified, verify if existing types_id matches category
    if "category_id" in fields_set and "types_id" not in fields_set:
        if page.types_id is not None:
            existing_type = session.get(Types, page.types_id)
            if existing_type and existing_type.category_id != page.category_id:
                page.types_id = None

    if "genre_id" in fields_set:
        if payload.genre_id is not None:
            g = session.get(Genre, payload.genre_id)
            if not g:
                raise HTTPException(status_code=404, detail="Genre not found")
        page.genre_id = payload.genre_id

    if "tag_ids" in fields_set:
        if payload.tag_ids is not None:
            if payload.tag_ids:
                tags = session.exec(select(Tag).where(Tag.id.in_(payload.tag_ids))).all()
                page.tags = tags
            else:
                page.tags = []
        else:
            page.tags = []

    if "element_ids" in fields_set:
        if payload.element_ids is not None:
            if payload.element_ids:
                elements = session.exec(select(Element).where(Element.id.in_(payload.element_ids))).all()
                page.elements = elements
            else:
                page.elements = []
        else:
            page.elements = []

    page.categorization_source = "user_manual"
    page.categorization_confidence = 1.0

    session.add(page)
    session.commit()
    session.refresh(page)

    return serialize_page_enriched(page)
