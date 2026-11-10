import os
import shutil
from typing import Optional
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from sqlmodel import Session, select

from database.models import Page, ScrapeJob
from modules.config import DOWNLOADS_DIR
from modules.scrapers.parked import is_blank_page

_CHALLENGE_TITLES = (
    "just a moment",
    "checking your browser",
    "checking the site connection security",
    "attention required",
    "security check",
    "robot check",
)


def extract_page_title(soup: Optional[BeautifulSoup]) -> str:
    """
    Extract the page title using a fallback cascade:
    1. <title> tag
    2. <meta property="og:title"> / <meta name="twitter:title">
    3. First non-empty <h1> in <main>, <article>, or <body>
    4. Fallback to "Untitled"
    """
    if not soup:
        return "Untitled"

    def _clean_candidate(val: Optional[str]) -> Optional[str]:
        if not val:
            return None
        cleaned = " ".join(str(val).split()).strip()
        if not cleaned or cleaned.lower() in ("untitled", "untitled page"):
            return None
        if any(ch in cleaned.lower() for ch in _CHALLENGE_TITLES):
            return None
        return cleaned

    # 1. <title>
    if soup.title:
        raw_title = soup.title.string or soup.title.get_text(" ", strip=True)
        cand = _clean_candidate(raw_title)
        if cand:
            return cand

    # 2. og:title / twitter:title
    for meta_attrs in (
        {"property": "og:title"},
        {"name": "og:title"},
        {"name": "twitter:title"},
        {"property": "twitter:title"},
    ):
        meta_tag = soup.find("meta", attrs=meta_attrs)
        if meta_tag and meta_tag.get("content"):
            cand = _clean_candidate(meta_tag.get("content"))
            if cand:
                return cand

    # 3. First non-empty <h1>
    for container in [soup.find("main"), soup.find("article"), soup.body, soup]:
        if not container:
            continue
        for h1 in container.find_all("h1"):
            cand = _clean_candidate(h1.get_text(" ", strip=True))
            if cand:
                return cand

    return "Untitled"


def _get_downloads_dir() -> str:
    """Dynamically get DOWNLOADS_DIR respecting patches in tests."""
    try:
        import modules.scrapers.engine as eng
        if hasattr(eng, "DOWNLOADS_DIR") and eng.DOWNLOADS_DIR and eng.DOWNLOADS_DIR != DOWNLOADS_DIR:
            return eng.DOWNLOADS_DIR
    except Exception:
        pass
    try:
        import main
        if hasattr(main, "DOWNLOADS_DIR") and main.DOWNLOADS_DIR and main.DOWNLOADS_DIR != DOWNLOADS_DIR:
            return main.DOWNLOADS_DIR
    except Exception:
        pass
    return DOWNLOADS_DIR


def resolve_disk_path(rel_path: Optional[str], job_dir: Optional[str] = None) -> Optional[str]:
    """Resolve a relative markdown_path or screenshot_path stored on Page into an absolute filesystem path."""
    if not rel_path:
        return None
    downloads_dir = _get_downloads_dir()
    rel = rel_path.lstrip("./").lstrip("/")
    if rel.startswith("_downloads/"):
        return os.path.join(downloads_dir, rel.replace("_downloads/", "", 1))
    if rel.startswith("../_downloads/"):
        return os.path.join(downloads_dir, rel.replace("../_downloads/", "", 1))
    if job_dir:
        return os.path.join(job_dir, rel)
    return os.path.join(downloads_dir, rel)


def is_untitled_or_empty_page(page: Page, job_dir: Optional[str] = None) -> bool:
    """
    Return True if a Page record represents an 'Untitled' or empty-body scrape
    that should be re-scraped.
    """
    title_norm = (page.title or "").strip().lower()
    if title_norm in ("untitled", "untitled page"):
        return True

    md_path = page.markdown_path or ""
    if md_path:
        base_md = os.path.basename(md_path).lower()
        parent_dir_name = os.path.basename(os.path.dirname(md_path)).lower()
        if base_md == "untitled.md" or parent_dir_name == "untitled" or parent_dir_name.startswith("untitled_"):
            return True

    if page.status == "completed":
        if not title_norm:
            return True
        disk_path = resolve_disk_path(md_path, job_dir)
        if not disk_path or not os.path.exists(disk_path):
            return True
        try:
            with open(disk_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            if "\n---\n" in content:
                body = content.split("\n---\n", 1)[1].strip()
            else:
                body = content.strip()
            if not body:
                return True
        except Exception:
            return True

    return False


def cleanup_untitled_page_artifacts(page: Page, job_dir: Optional[str] = None) -> None:
    """
    Remove stale 'Untitled' / 'Untitled_<slug>' directories or files on disk for a page
    before re-queuing or updating it, preventing orphan Untitled folders.
    """
    for rel_path in (page.markdown_path, page.screenshot_path):
        disk_path = resolve_disk_path(rel_path, job_dir)
        if not disk_path:
            continue
        parent_dir = os.path.dirname(disk_path)
        parent_name = os.path.basename(parent_dir)
        if parent_name == "Untitled" or parent_name.startswith("Untitled_"):
            if os.path.isdir(parent_dir):
                meta_file = os.path.join(parent_dir, "metadata.csv")
                should_remove_dir = True
                if os.path.exists(meta_file) and page.url:
                    try:
                        with open(meta_file, "r", encoding="utf-8", errors="ignore") as mf:
                            meta_text = mf.read()
                        if page.url not in meta_text and meta_text.strip():
                            should_remove_dir = False
                    except Exception:
                        pass
                if should_remove_dir:
                    shutil.rmtree(parent_dir, ignore_errors=True)
                    continue
        if os.path.isfile(disk_path) and os.path.basename(disk_path).lower().startswith(("untitled", "screenshot_")):
            try:
                os.remove(disk_path)
            except Exception:
                pass


def is_challenge_or_unrendered_html(html_content: Optional[str]) -> bool:
    """Check if raw HTML is empty, a bot challenge, or an unrendered 'Untitled' blank page."""
    if not html_content or len(html_content.strip()) < 500:
        return True
    html_lower = html_content.lower()
    if (
        "robot-suspicion" in html_lower
        or "checking the site connection security" in html_lower
        or "checking your browser" in html_lower
        or "<title>just a moment" in html_lower
        or "confirm you are human" in html_lower
        or "sg-captcha" in html_lower
    ):
        return True
    prelim_soup = BeautifulSoup(html_content, "html.parser")
    prelim_title = extract_page_title(prelim_soup)
    if prelim_title == "Untitled" or is_blank_page(prelim_soup):
        return True
    return False


# Backward-compatible alias
_is_challenge_or_unrendered_html = is_challenge_or_unrendered_html


def get_job_dir(job: ScrapeJob) -> str:
    """Get the standard filesystem directory for a job."""
    downloads_dir = _get_downloads_dir()
    domain = urlparse(job.target_url or "").netloc.replace(".", "_")
    return os.path.join(downloads_dir, f"job_{job.id}_{domain}")


# Backward-compatible alias
_get_job_dir = get_job_dir


def reset_job_retryable_pages(session: Session, job: ScrapeJob) -> int:
    """
    Find all 'failed' or 'Untitled'/empty pages for a job, clean up their stale
    Untitled disk artifacts, reset their status to 'pending', and update job.scraped_pages.
    """
    job_dir = get_job_dir(job)
    pages = session.exec(select(Page).where(Page.job_id == job.id)).all()
    reset_count = 0
    for p in pages:
        if p.status == "failed" or is_untitled_or_empty_page(p, job_dir):
            cleanup_untitled_page_artifacts(p, job_dir)
            p.status = "pending"
            if (p.title or "").strip().lower() in ("", "untitled", "untitled page"):
                p.title = p.url_path or urlparse(p.url).path or p.url
            p.markdown_path = None
            p.screenshot_path = None
            session.add(p)
            reset_count += 1

    completed_count = sum(1 for p in pages if p.status == "completed")
    job.scraped_pages = completed_count
    session.add(job)
    return reset_count


def reconcile_job_statuses(session: Session, active_job_ids: Optional[set[int]] = None) -> int:
    """
    Auto-reconciles jobs whose status is 'running' but have finished processing.
    Updates job status to 'completed' or 'failed' based on page counts.
    Actively running jobs are skipped to prevent premature failure during initialization.
    """
    if active_job_ids is None:
        try:
            from modules.core.job_logger import job_log_manager
            active_job_ids = job_log_manager.get_active_jobs()
        except Exception:
            active_job_ids = set()

    running_jobs = session.exec(select(ScrapeJob).where(ScrapeJob.status == "running")).all()
    updated = 0
    for job in running_jobs:
        if job.id in active_job_ids:
            continue

        pages = session.exec(select(Page).where(Page.job_id == job.id)).all()
        pending_count = sum(1 for p in pages if p.status == "pending")
        completed_count = sum(1 for p in pages if p.status == "completed")
        failed_count = sum(1 for p in pages if p.status == "failed")

        if pending_count == 0:
            if completed_count > 0:
                job.status = "completed"
                job.scraped_pages = completed_count
                if not job.total_pages or job.total_pages < completed_count:
                    job.total_pages = max(1, completed_count)
                session.add(job)
                updated += 1
            elif failed_count > 0 or len(pages) == 0:
                job.status = "failed"
                session.add(job)
                updated += 1
    if updated > 0:
        session.commit()
    return updated
