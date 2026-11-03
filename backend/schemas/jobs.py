from typing import Optional
from pydantic import BaseModel


class JobTaxonomyUpdate(BaseModel):
    category_id: Optional[int] = None
    types_id: Optional[int] = None
