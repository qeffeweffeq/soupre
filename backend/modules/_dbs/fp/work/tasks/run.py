import time
import logging

from modules.config import WorkspaceContext
from modules.notion.fetch import fetch_all_pages
from modules.embeddings.client import get_embedding
from modules.embeddings.vector_store import load_cache, save_cache

log = logging.getLogger(__name__)

def _get_page_text(page: dict) -> str:
    """Extract all relevant text from a Task page to embed."""
    # Start with the task name
    name_prop = page.get("properties", {}).get("Task", {}) or page.get("properties", {}).get("Name", {})
    title_parts = name_prop.get("title", [])
    title = "".join(t.get("plain_text", "") for t in title_parts).strip()

    # Check for a Notes or Description field if it exists
    desc_prop = page.get("properties", {}).get("Notes", {}) or page.get("properties", {}).get("Description", {})
    desc_parts = desc_prop.get("rich_text", [])
    desc = "".join(t.get("plain_text", "") for t in desc_parts).strip()

    # Combine
    return f"{title}\n{desc}".strip()

def run(ctx: WorkspaceContext, config, dry_run: bool, limit: int = None) -> None:
    """
    Run the Tasks embedding sync pipeline.

    Fetches all tasks from the Notion Tasks DB, checks if they are in the local
    vector cache, and generates Gemini embeddings for any missing ones.
    """
    client = ctx.client
    db_id = ctx.database.db_id

    log.info(f"Workspace: {ctx.workspace}  │  Database: {ctx.database.key}")
    log.info(f"Target DB: {db_id}")

    # 1. Fetch all tasks
    log.info("Fetching tasks from Notion...")
    pages = fetch_all_pages(client, db_id, config.RATE_LIMIT_DELAY)
    log.info(f"Retrieved {len(pages)} tasks.")

    if not pages:
        return

    if limit is not None:
        pages = pages[:limit]
        log.info(f"Limiting execution to {limit} tasks.")

    # 2. Load vector cache
    cache = load_cache()
    log.info(f"Loaded {len(cache)} existing embeddings from cache.")

    # 3. Process missing embeddings
    new_embeddings = 0
    cached_count = 0
    errors = 0

    for i, page in enumerate(pages, 1):
        page_id = page["id"]
        text_to_embed = _get_page_text(page)
        task_preview = text_to_embed.splitlines()[0][:45] if text_to_embed else "(empty task)"

        if page_id in cache:
            cached_count += 1
            log.debug(f"[{i:>3}/{len(pages)}] [CACHE:HIT] Task '{task_preview}' already in vector cache.")
            continue

        if not text_to_embed:
            log.debug(f"[{i:>3}/{len(pages)}] [SKIP] Task '{task_preview}' has no text to embed.")
            continue

        log.info(f"[{i:>3}/{len(pages)}] [CACHE:MISS] Generating embedding for: '{task_preview}'...")

        if dry_run:
            log.info("  (Dry-run: skipping API call)")
            continue

        vector = get_embedding(text_to_embed)
        if vector:
            cache[page_id] = vector
            new_embeddings += 1
            time.sleep(0.1)
        else:
            log.warning(f"  ❌ Failed to generate embedding for task {page_id}")
            errors += 1

    # 4. Save updated cache
    if new_embeddings > 0 and not dry_run:
        save_cache(cache)
        log.info(f"✅ Saved {new_embeddings} new embeddings to cache.")

    log.info(f"\n{'─' * 55}")
    log.info(f"Done.  ✨ {new_embeddings} embedded   ❌ {errors} failed")
