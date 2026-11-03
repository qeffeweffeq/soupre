from .jobs import router as jobs_router
from .pages import router as pages_router
from .taxonomies import router as taxonomies_router

__all__ = ["jobs_router", "pages_router", "taxonomies_router"]
