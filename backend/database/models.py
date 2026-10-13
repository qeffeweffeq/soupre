from sqlmodel import Field, SQLModel, Relationship
from typing import Optional, List
from datetime import datetime, timezone

class ScrapeJob(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    target_url: str
    status: str = Field(default="pending") # pending, running, completed, failed
    scrape_mode: str = Field(default="single")       # "single" | "sitemap"
    total_pages: Optional[int] = Field(default=None)  # total URLs in sitemap
    scraped_pages: int = Field(default=0)              # progress counter
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    pages: List["Page"] = Relationship(back_populates="job")

class Page(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="scrapejob.id")
    url: str
    title: str
    markdown_path: Optional[str] = None
    screenshot_path: Optional[str] = None
    url_path: Optional[str] = None    # e.g. "/blog/my-post"
    url_depth: int = Field(default=0) # depth in URL hierarchy
    
    job: Optional[ScrapeJob] = Relationship(back_populates="pages")
    media_items: List["MediaItem"] = Relationship(back_populates="page")

class MediaItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    page_id: int = Field(foreign_key="page.id")
    media_type: str  # 'image', 'video', 'audio'
    local_path: str
    source_url: str
    
    page: Optional[Page] = Relationship(back_populates="media_items")
