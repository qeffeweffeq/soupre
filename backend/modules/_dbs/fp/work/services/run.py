"""
services/run.py
─────────────────────────────────────────────────────────────────────────────
Services enrichment pipeline — orchestration.

Queries the Services DB for entries with TBP-U=true and Archive=false,
scrapes each website using Playwright, and updates the Notion page with
enriched data (Notes, cover screenshot, favicon icon, inferred Roles/Types/Tags).
"""

import time
import logging
from urllib.parse import urlparse

from modules.config import get_token, ConfigError, build_context
from modules.notion.upload import upload_screenshot
from modules.notion.relations import fetch_relation_map
from modules.utils.text import parse_name
from modules.scraper.orchestrator import scrape_website
from modules.taxonomies.jev_matcher import extract_taxonomies_from_text
from modules._dbs.fp.work.services.inference import infer_roles, infer_types, infer_tags
from modules._dbs.fp.work.services.builder import build_update_payload

log = logging.getLogger(__name__)

def _get_page_name(page: dict) -> str:
    """Extract the title/Name from a Notion page object."""
    name_prop = page.get("properties", {}).get("Name", {})
    title_parts = name_prop.get("title", [])
    return "".join(t.get("plain_text", "") for t in title_parts).strip() or "(untitled)"

def _get_web_url(page: dict) -> str | None:
    """Extract the URL from a Notion page object and return the clean domain."""
    web_prop = page.get("properties", {}).get("URL", {})
    url = web_prop.get("url")
    if not url:
        return None

    if not url.startswith(("http://", "https://")):
        url = "http://" + url

    parsed = urlparse(url)
    if parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}/"
    return url

def _query_pages(client, db_id: str, rate_limit_delay: float) -> list[dict]:
    """
    Query pages where TBP-U == true AND Archive == false.
    Handles pagination. Returns [] on failure.
    """
    query_filter = {
        "and": [
            {"property": "TBP-U", "checkbox": {"equals": True}},
            {"property": "Archive", "checkbox": {"equals": False}},
        ]
    }

    pages: list[dict] = []
    cursor = None

    try:
        while True:
            kwargs: dict = {
                "database_id": db_id,
                "filter": query_filter,
                "page_size": 100,
            }
            if cursor:
                kwargs["start_cursor"] = cursor

            from modules.notion.fetch import query_database
            resp = query_database(client, db_id, **kwargs)
            pages.extend(resp.get("results", []))

            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
            time.sleep(rate_limit_delay)
    except Exception as exc:
        log.error(f"Services DB query failed: {exc}")

    return pages

def _query_single_page(client, page_id: str) -> dict | None:
    """Retrieve a single page by ID. Returns None on failure."""
    try:
        return client.pages.retrieve(page_id=page_id)
    except Exception as exc:
        log.error(f"Failed to retrieve page {page_id}: {exc}")
        return None

def _fetch_relation_maps(client, db_ctx) -> dict:
    """
    Pre-fetch all relation maps needed for the services pipeline.
    Returns {relation_key: {lowercase_name: page_id}}.
    """
    maps = {}

    for rel_key, rel_db_id in db_ctx.relations.items():
        if rel_db_id and rel_db_id != "dummy_id_for_dry_run":
            log.info(f"  Loading {rel_key.title()} relation map …")
            maps[rel_key] = fetch_relation_map(client, rel_db_id)
            log.info(f"  → {len(maps[rel_key])} {rel_key}(s)")
        else:
            maps[rel_key] = {}

    return maps

def run(
    client,
    config,
    dry_run: bool,
    limit: int | None,
    page_id: str | None = None,
) -> None:
    """
    Run the services enrichment pipeline.
    """
    try:
        ctx = build_context("FP", "services")
    except ConfigError as exc:
        if not dry_run:
            log.error(f"Configuration error: {exc}")
            return
        else:
            # Create dummy context for dry-run if build_context fails
            from modules.config import resolve_database, WorkspaceContext
            try:
                resolved = resolve_database("services")
            except ConfigError as exc2:
                log.error(f"Configuration error: {exc2}")
                return
            ctx = WorkspaceContext(workspace="FP", client=client, database=resolved)

    db_id = ctx.database.db_id
    if not db_id:
        log.error("NOTION_SERVICES_DB_ID not set in .env — aborting.")
        return

    token: str | None = None
    if not dry_run:
        try:
            token = get_token("FP")
        except ConfigError as exc:
            log.error(f"Cannot get FP token for uploads: {exc}")
            return

    # ── Step 1: Query pages ───────────────────────────────────────────────
    if page_id:
        log.info(f"Fetching single page: {page_id}")
        page = _query_single_page(client, page_id)
        pages = [page] if page else []
    else:
        log.info("Querying Services DB (TBP-U=true, Archive=false) …")
        pages = _query_pages(client, db_id, config.RATE_LIMIT_DELAY)

    log.info(f"Found {len(pages)} page(s) to enrich\n")

    if not pages:
        return

    if limit is not None:
        pages = pages[:limit]

    # ── Step 2: Fetch relation maps ───────────────────────────────────────
    relation_maps = {}
    if not dry_run and client:
        log.info("── Loading relation maps ──")
        relation_maps = _fetch_relation_maps(client, ctx.database)
        log.info("")

    # ── Step 3: Launch Playwright browser ─────────────────────────────────
    browser = None
    pw = None

    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        browser = pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        log.info("🌐  Playwright browser launched\n")
    except Exception as exc:
        log.error(f"Failed to launch Playwright: {exc}")
        return

    # ── Step 4: Process each page ─────────────────────────────────────────
    stats = {"enriched": 0, "skipped": 0, "failed": 0}
    total = len(pages)

    try:
        for i, page in enumerate(pages, 1):
            prefix = f"[{i:>3}/{total}]"
            raw_name = _get_page_name(page)
            web_url = _get_web_url(page)

            if not web_url:
                log.warning(f"{prefix} ⏭  {raw_name[:50]} — no URL")
                stats["skipped"] += 1
                continue

            is_untitled = (raw_name == "(untitled)")
            cleaned_name, location_hint, short_about = parse_name(raw_name)

            display_name = "(untitled)" if is_untitled else cleaned_name
            log.info(f"{prefix} 🔍  Scraping {display_name[:40]:<40} │ {web_url}")

            scraped = scrape_website(
                web_url,
                browser,
                cleaned_name="" if is_untitled else cleaned_name
            )

            if scraped and not scraped.get("is_parked") and not scraped.get("is_blank") and not scraped.get("dead_link") and scraped.get("status_code") not in (403, 404, 503):
                # Sanitize name and extract field/topic from page_title if not already present
                if scraped.get("page_title"):
                    pt_name, pt_loc, pt_about = parse_name(scraped["page_title"])
                    if is_untitled:
                        cleaned_name = pt_name
                        short_about = pt_about
                        location_hint = pt_loc
                        log.info(f"{prefix} 🏷️   Inferred name from title: {cleaned_name}")
                    elif not short_about and pt_about:
                        short_about = pt_about

                # Primary text: title, field/topic, and meta description
                primary_text = " ".join(
                    s for s in (cleaned_name, short_about or "", scraped.get("page_title") or "", scraped.get("about") or "") if s
                )
                body_text = scraped.get("body_text", "")

                roles_inferred = infer_roles(primary_text, body_text)
                types_inferred = infer_types(primary_text, body_text)

                tags_map = relation_maps.get("tags", {})
                tags_inferred = infer_tags(primary_text, available_tags=tags_map, body_text=body_text)

                jev_tags = extract_taxonomies_from_text("services", "Tags", primary_text)
                if not jev_tags and body_text:
                    system_tags = {"changed", "other", "closed", "not loading", "403", "503"}
                    body_jev_tags = extract_taxonomies_from_text("services", "Tags", body_text[:5000])
                    jev_tags = [t for t in body_jev_tags if t.lower() not in system_tags]

                combined_tags: list[str] = []
                seen_tag_lowers: set[str] = set()
                for t in (tags_inferred + jev_tags):
                    if t.lower() not in seen_tag_lowers:
                        seen_tag_lowers.add(t.lower())
                        combined_tags.append(t)

                scraped["roles_inferred"] = roles_inferred
                scraped["types_inferred"] = types_inferred
                scraped["tags_inferred"] = combined_tags[:4]

                log.info(
                    f"{prefix} 📊 Taxonomies resolved for '{display_name[:30]}': "
                    f"Roles={scraped['roles_inferred']} │ "
                    f"Types={scraped['types_inferred']} │ "
                    f"Tags={scraped['tags_inferred']} (heur={len(tags_inferred)}, jev={len(jev_tags)})"
                )

            if not scraped:
                log.warning(f"{prefix} ❌  {display_name[:50]} — scrape returned empty")
                stats["failed"] += 1
                if not dry_run and client:
                    try:
                        client.pages.update(
                            page_id=page["id"],
                            properties={
                                "Not Autofilled": {"checkbox": True},
                                "TBP-U": {"checkbox": False},
                            }
                        )
                        log.info(f"{prefix} 📝  Flagged as 'Not Autofilled'")
                    except Exception as exc:
                        log.error(f"{prefix} Failed to flag as 'Not Autofilled': {exc}")
                time.sleep(1)
                continue

            cover_upload_id: str | None = None
            if scraped.get("screenshot_bytes") and token and not dry_run:
                cover_upload_id = upload_screenshot(
                    token, scraped["screenshot_bytes"],
                )
                if cover_upload_id:
                    log.info(f"{prefix}       cover ✓")

            icon_url = scraped.get("icon_url")
            payload = build_update_payload(
                page=page,
                cleaned_name=cleaned_name,
                scraped=scraped,
                relation_maps=relation_maps,
                cover_upload_id=cover_upload_id,
                icon_url=icon_url,
                dry_run=dry_run,
                short_about=short_about,
            )

            if not payload:
                log.info(f"{prefix} ⏭  {cleaned_name[:50]} — nothing to update")
                stats["skipped"] += 1
                time.sleep(1)
                continue

            props = payload.get("properties", {})
            fields_set = [k for k in props if k not in ("TBP-U", "Autofilled")]
            extras = []
            if payload.get("cover"):
                extras.append("cover ✓")
            if payload.get("icon"):
                extras.append("icon ✓")

            if dry_run:
                log.info(
                    f"{prefix} DRY  {cleaned_name[:40]:<40} "
                    f"│ props={fields_set} {' '.join(extras)}"
                )
                stats["enriched"] += 1
            else:
                try:
                    update_kwargs: dict = {"page_id": page["id"]}
                    if "properties" in payload:
                        update_kwargs["properties"] = payload["properties"]
                    if "cover" in payload:
                        update_kwargs["cover"] = payload["cover"]
                    if "icon" in payload:
                        update_kwargs["icon"] = payload["icon"]

                    client.pages.update(**update_kwargs)
                    log.info(
                        f"{prefix} ✅  {cleaned_name[:40]:<40} "
                        f"│ props={fields_set} {' '.join(extras)}"
                    )
                    stats["enriched"] += 1
                except Exception as exc:
                    log.error(f"{prefix} ❌  {cleaned_name[:50]} — {exc}")
                    stats["failed"] += 1

            time.sleep(1)

    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        if pw:
            try:
                pw.stop()
            except Exception:
                pass
        log.info("🌐  Playwright browser closed")

    log.info(f"\n{'─' * 55}")
    log.info(f"✅  {stats['enriched']} page(s) enriched")
    log.info(f"⏭   {stats['skipped']} page(s) skipped")
    log.info(f"❌  {stats['failed']} page(s) failed")
