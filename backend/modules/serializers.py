import os
from typing import Optional
from database.models import Page, ScrapeJob


def is_page_untitled_fast(page: Page) -> bool:
    """Fast check whether a page is 'Untitled' based on title or markdown path."""
    title_norm = (page.title or "").strip().lower()
    if title_norm in ("untitled", "untitled page"):
        return True
    md_path = page.markdown_path or ""
    if md_path:
        base_md = os.path.basename(md_path).lower()
        parent_dir_name = os.path.basename(os.path.dirname(md_path)).lower()
        if base_md == "untitled.md" or parent_dir_name == "untitled" or parent_dir_name.startswith("untitled_"):
            return True
    return False


# Backward-compatible alias
_is_page_untitled_fast = is_page_untitled_fast


def serialize_job_enriched(job: ScrapeJob) -> dict:
    """Serialize a ScrapeJob with taxonomy names and page status counters."""
    data = job.model_dump()
    ws = job.workspace or (job.category.workspace if job.category else None)
    data["workspace_id"] = job.workspace_id or (job.category.workspace_id if job.category else None)
    data["workspace_name"] = ws.name if ws else None
    data["workspace_slug"] = ws.slug if ws else None
    data["workspace_icon"] = ws.icon if ws else None
    data["workspace_color"] = ws.color if ws else None

    data["category_name"] = job.category.name if job.category else None
    data["category_slug"] = job.category.slug if job.category else None
    data["category_section"] = job.category.section if job.category else None
    data["types_name"] = job.types_rel.name if job.types_rel else None
    data["types_slug"] = job.types_rel.slug if job.types_rel else None

    job_pages = job.pages or []
    data["untitled_count"] = sum(1 for p in job_pages if is_page_untitled_fast(p))
    data["failed_count"] = sum(1 for p in job_pages if p.status == "failed")
    return data


def serialize_page_enriched(page: Page, job: Optional[ScrapeJob] = None) -> dict:
    """Serialize a Page with enriched taxonomy names, tags, and timestamps."""
    scraped_at = None
    if job and job.created_at:
        scraped_at = job.created_at.isoformat()
    elif page.job and page.job.created_at:
        scraped_at = page.job.created_at.isoformat()
    elif page.lastmod:
        scraped_at = page.lastmod

    data = page.model_dump()
    ws = page.workspace or (page.category.workspace if page.category else None)
    data["workspace_id"] = page.workspace_id or (page.category.workspace_id if page.category else None)
    data["workspace_name"] = ws.name if ws else None
    data["workspace_slug"] = ws.slug if ws else None
    data["workspace_icon"] = ws.icon if ws else None
    data["workspace_color"] = ws.color if ws else None

    data["category_name"] = page.category.name if page.category else None
    data["category_slug"] = page.category.slug if page.category else None
    data["category_section"] = page.category.section if page.category else None
    data["types_name"] = page.types_rel.name if page.types_rel else None
    data["types_slug"] = page.types_rel.slug if page.types_rel else None
    data["genre_name"] = page.genre.name if page.genre else None
    data["tag_names"] = [t.name for t in page.tags] if page.tags else []
    data["tag_ids"] = [t.id for t in page.tags] if page.tags else []
    data["tags"] = [
        {
            "id": t.id,
            "name": t.name,
            "slug": t.slug,
            "workspace_id": t.workspace_id,
            "section": t.section,
            "notion_id": t.notion_id,
        }
        for t in page.tags
    ] if page.tags else []
    data["scraped_at"] = scraped_at
    data["is_untitled"] = is_page_untitled_fast(page)
    return data
