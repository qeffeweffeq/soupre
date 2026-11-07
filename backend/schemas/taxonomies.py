from typing import Optional, List
from pydantic import BaseModel


class WorkspaceCreate(BaseModel):
    name: str
    slug: Optional[str] = None
    icon: Optional[str] = "📁"
    color: Optional[str] = "mauve"
    description: Optional[str] = None
    notion_id: Optional[str] = None


class WorkspaceUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    icon: Optional[str] = None
    color: Optional[str] = None
    description: Optional[str] = None
    notion_id: Optional[str] = None


class CategoryCreate(BaseModel):
    name: str
    slug: Optional[str] = None
    workspace_id: Optional[int] = None
    section: Optional[str] = None
    description: Optional[str] = None
    notion_id: Optional[str] = None


class CategoryUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    workspace_id: Optional[int] = None
    section: Optional[str] = None
    description: Optional[str] = None
    notion_id: Optional[str] = None


class TypesCreate(BaseModel):
    category_id: int
    name: str
    slug: Optional[str] = None
    description: Optional[str] = None
    notion_id: Optional[str] = None


class TypesUpdate(BaseModel):
    category_id: Optional[int] = None
    name: Optional[str] = None
    slug: Optional[str] = None
    description: Optional[str] = None
    notion_id: Optional[str] = None


class TagCreate(BaseModel):
    name: str
    slug: Optional[str] = None
    workspace_id: Optional[int] = None
    section: Optional[str] = None
    notion_id: Optional[str] = None


class TagUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    workspace_id: Optional[int] = None
    section: Optional[str] = None
    notion_id: Optional[str] = None


class BulkTagAssignRequest(BaseModel):
    page_ids: List[int]
    tag_ids: List[int]


class ElementCreate(BaseModel):
    name: str
    slug: Optional[str] = None
    icon: Optional[str] = "clothes-button"
    workspace_id: Optional[int] = None
    section: Optional[str] = "goods"
    notion_id: Optional[str] = None


class ElementUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    icon: Optional[str] = None
    workspace_id: Optional[int] = None
    section: Optional[str] = None
    notion_id: Optional[str] = None


class BulkElementAssignRequest(BaseModel):
    page_ids: List[int]
    element_ids: List[int]


class GenreCreate(BaseModel):
    name: str
    slug: Optional[str] = None


class GenreUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None


class PageTaxonomyUpdate(BaseModel):
    workspace_id: Optional[int] = None
    category_id: Optional[int] = None
    types_id: Optional[int] = None
    genre_id: Optional[int] = None
    tag_ids: Optional[List[int]] = None
    element_ids: Optional[List[int]] = None
