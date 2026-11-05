from sqlmodel import Field, SQLModel, Relationship
from typing import Optional, List
from datetime import datetime, timezone

class Category(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)
    slug: str = Field(unique=True, index=True)
    description: Optional[str] = None
    notion_id: Optional[str] = None

    types: List["Types"] = Relationship(back_populates="category", cascade_delete=True)
    pages: List["Page"] = Relationship(back_populates="category")

class Types(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    category_id: int = Field(foreign_key="category.id")
    name: str = Field(index=True)
    slug: str = Field(index=True)
    description: Optional[str] = None
    notion_id: Optional[str] = None

    category: Optional[Category] = Relationship(back_populates="types")
    pages: List["Page"] = Relationship(back_populates="types_rel")

class PageTagLink(SQLModel, table=True):
    page_id: Optional[int] = Field(default=None, foreign_key="page.id", primary_key=True)
    tag_id: Optional[int] = Field(default=None, foreign_key="tag.id", primary_key=True)

class Tag(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)
    slug: str = Field(unique=True, index=True)
    notion_id: Optional[str] = None

    pages: List["Page"] = Relationship(back_populates="tags", link_model=PageTagLink)

class Genre(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)
    slug: str = Field(unique=True, index=True)

    pages: List["Page"] = Relationship(back_populates="genre")

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
    clean_url: Optional[str] = None
    title: str
    description: Optional[str] = None
    icon_url: Optional[str] = Field(default=None)
    markdown_path: Optional[str] = None
    screenshot_path: Optional[str] = None
    favicon_path: Optional[str] = None
    status: str = Field(default="pending")            # "pending" | "completed" | "failed"
    url_path: Optional[str] = None                   # e.g. "/blog/my-post"
    url_depth: int = Field(default=0)                # depth in URL hierarchy
    hreflang: Optional[str] = None                   # e.g. "it", "en", "de", "fr"
    lastmod: Optional[str] = None                    # e.g. "2026-08-31T15:16:29+00:00"
    content_type: Optional[str] = None               # e.g. "blog", "experiences", "pages"

    # Relational Taxonomies
    category_id: Optional[int] = Field(default=None, foreign_key="category.id")
    types_id: Optional[int] = Field(default=None, foreign_key="types.id")
    genre_id: Optional[int] = Field(default=None, foreign_key="genre.id")

    category: Optional[Category] = Relationship(back_populates="pages")
    types_rel: Optional[Types] = Relationship(back_populates="pages")
    genre: Optional[Genre] = Relationship(back_populates="pages")
    tags: List[Tag] = Relationship(back_populates="pages", link_model=PageTagLink)

    # Classification Metadata
    categorization_source: str = Field(default="dual_hybrid")  # jev | embedding | consensus | manual
    categorization_confidence: float = Field(default=0.0)

    # External Notion Sync Status
    notion_page_id: Optional[str] = None
    sync_status: str = Field(default="unmerged")  # unmerged, synced, failed
    last_synced_at: Optional[datetime] = None

    job: Optional[ScrapeJob] = Relationship(back_populates="pages")
    media_items: List["MediaItem"] = Relationship(back_populates="page")

class MediaItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    page_id: int = Field(foreign_key="page.id")
    media_type: str  # 'image', 'video', 'audio'
    local_path: str
    source_url: str

    page: Optional[Page] = Relationship(back_populates="media_items")
