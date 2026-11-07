from typing import Optional, List
from pydantic import BaseModel


class JobTaxonomyUpdate(BaseModel):
    workspace_id: Optional[int] = None
    notion_id: Optional[str] = None
    category_id: Optional[int] = None
    types_id: Optional[int] = None


class BatchPreviewItem(BaseModel):
    id: str
    raw_url: str
    clean_url: str
    domain: str
    stripped_params: List[str] = []
    has_stripped_params: bool = False
    content_type: str = "pages"
    workspace_id: Optional[int] = None
    workspace_name: Optional[str] = None
    workspace_slug: Optional[str] = None
    workspace_icon: Optional[str] = None
    workspace_color: Optional[str] = None
    notion_id: Optional[str] = None
    notion_db_name: Optional[str] = None
    predicted_category_id: Optional[int] = None
    predicted_category_name: Optional[str] = None
    predicted_types_id: Optional[int] = None
    predicted_types_name: Optional[str] = None
    tag_ids: List[int] = []
    predicted_tags: List[str] = []
    element_ids: List[int] = []
    predicted_elements: List[str] = []
    url_status: Optional[str] = None
    url_status_code: Optional[int] = None
    url_status_label: Optional[str] = None
    final_url: Optional[str] = None
    use_raw_url: bool = False
    selected: bool = True


class UrlVerifyInput(BaseModel):
    id: str
    raw_url: str
    clean_url: str
    has_stripped_params: bool = False


class UrlVerifyResult(BaseModel):
    id: str
    url_status: str
    url_status_code: Optional[int] = None
    url_status_label: str
    final_url: Optional[str] = None
    use_raw_url: bool = False


class BatchVerifyUrlsRequest(BaseModel):
    items: List[UrlVerifyInput]


class BatchVerifyUrlsResponse(BaseModel):
    results: List[UrlVerifyResult]


class BatchPreviewResponse(BaseModel):
    count: int
    filename: Optional[str] = None
    items: List[BatchPreviewItem] = []
    urls: List[str] = []


class BatchCuratedItem(BaseModel):
    url: str
    clean_url: Optional[str] = None
    workspace_id: Optional[int] = None
    notion_id: Optional[str] = None
    category_id: Optional[int] = None
    types_id: Optional[int] = None
    tag_ids: List[int] = []
    element_ids: List[int] = []
    content_type: Optional[str] = None


class BatchExecuteRequest(BaseModel):
    items: List[BatchCuratedItem]
    workspace_id: Optional[int] = None
    scrape_mode: str = "single"
    delay_min: float = 60.0
    delay_max: float = 120.0
    session_min: int = 30
    session_max: int = 50
    cooldown_min: float = 180.0
    cooldown_max: float = 300.0
    large_threshold: int = 20
    large_pause_sec: int = 120
