from typing import Optional
from pydantic import BaseModel


class JobTaxonomyUpdate(BaseModel):
    workspace_id: Optional[int] = None
    category_id: Optional[int] = None
    types_id: Optional[int] = None
