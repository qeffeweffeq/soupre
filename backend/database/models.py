from sqlmodel import Field, SQLModel, Relationship
from typing import Optional, List
from datetime import datetime, timezone

class ScrapeJob(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    target_url: str
    title: Optional[str] = Field(default=None)
    icon_url: Optional[str] = Field(default=None)
    status: str = Field(default="pending")  # pending, running, completed, failed
    scrape_mode: str = Field(default="single")       # "single" | "sitemap"
    total_pages: Optional[int] = Field(default=None)  # total URLs in sitemap
    scraped_pages: int = Field(default=0)              # progress counter
    log_path: Optional[str] = Field(default=None)      # relative path to job.log
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    pages: List["Page"] = Relationship(back_populates="job")

class Page(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="scrapejob.id")
    url: str
    title: str
    icon_url: Optional[str] = Field(default=None)
    markdown_path: Optional[str] = None
    screenshot_path: Optional[str] = None
    status: str = Field(default="pending")            # "pending" | "completed" | "failed"
    url_path: Optional[str] = None                   # e.g. "/blog/my-post"
    url_depth: int = Field(default=0)                # depth in URL hierarchy
    hreflang: Optional[str] = None                   # e.g. "it", "en", "de", "fr"
    lastmod: Optional[str] = None                    # e.g. "2026-08-31T15:16:29+00:00"
    content_type: Optional[str] = None               # e.g. "blog", "experiences", "pages"
    job: Optional[ScrapeJob] = Relationship(back_populates="pages")
    media_items: List["MediaItem"] = Relationship(back_populates="page")

class MediaItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    page_id: int = Field(foreign_key="page.id")
    media_type: str  # 'image', 'video', 'audio'
    local_path: str
    source_url: str
    
    page: Optional[Page] = Relationship(back_populates="media_items")
