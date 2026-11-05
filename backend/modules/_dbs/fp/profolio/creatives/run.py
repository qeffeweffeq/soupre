"""
creatives/run.py
─────────────────────────────────────────────────────────────────────────────
Creatives enrichment pipeline — orchestration.

Queries the Creatives DB for entries with TBP-U=true and Archive=false,
scrapes each website using Playwright, and updates the Notion page with
enriched data (metadata, cover screenshot, favicon icon, inferred relations).

The Playwright browser lifecycle is managed here: one browser instance is
opened for the entire batch and closed when done.
"""

import time
import logging

from modules.config import get_token, ConfigError
from modules.scraper.orchestrator import scrape_website
from modules.taxonomies.jev_matcher import extract_taxonomies_from_text
from modules._dbs.fp.profolio.creatives.inference import infer_type, infer_fields, infer_tags
from modules._dbs.fp.profolio.creatives.builder import build_update_payload
from modules.notion.upload import upload_screenshot
from modules.utils.text import parse_name
from modules.notion.relations import fetch_relation_map
from modules.notion.fetch import query_database

log = logging.getLogger(__name__)


def _get_page_name(page: dict) -> str:
    """Extract the title/Name from a Notion page object."""
    name_prop = page.get("properties", {}).get("Name", {})
    title_parts = name_prop.get("title", [])
    return "".join(t.get("plain_text", "") for t in title_parts).strip() or "(untitled)"


from urllib.parse import urlparse

def _get_web_url(page: dict) -> str | None:
    """Extract the Web URL from a Notion page object and return the clean domain."""
    web_prop = page.get("properties", {}).get("Web", {})
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
            {"property": "Duplicated", "checkbox": {"equals": False}},
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

            resp = query_database(client, db_id=kwargs.get("database_id"), **kwargs)
            pages.extend(resp.get("results", []))

            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
            time.sleep(rate_limit_delay)
    except Exception as exc:
        log.error(f"Creatives DB query failed: {exc}")

    return pages


def _query_single_page(client, page_id: str) -> dict | None:
    """Retrieve a single page by ID. Returns None on failure."""
    try:
        return client.pages.retrieve(page_id=page_id)
    except Exception as exc:
        log.error(f"Failed to retrieve page {page_id}: {exc}")
        return None


def _fetch_relation_maps(client, relations: dict[str, str] | None = None) -> dict:
    """
    Pre-fetch all relation maps needed for the creatives pipeline.
    Returns {relation_key: {lowercase_name: page_id}}.
    """
    maps = {}
    rel_dict = relations or {}

    types_id = rel_dict.get("type") or getattr(config, "CREATIVES_TYPES_DB_ID", "")
    if types_id and types_id != "dummy_id_for_dry_run":
        log.info("  Loading Types relation map …")
        maps["types"] = fetch_relation_map(client, types_id)
        log.info(f"  → {len(maps['types'])} type(s)")
    else:
        maps["types"] = {}

    fields_id = rel_dict.get("fields") or getattr(config, "CREATIVES_FIELDS_DB_ID", "")
    if fields_id and fields_id != "dummy_id_for_dry_run":
        log.info("  Loading Fields relation map …")
        maps["fields"] = fetch_relation_map(client, fields_id)
        log.info(f"  → {len(maps['fields'])} field(s)")
    else:
        maps["fields"] = {}

    tags_id = rel_dict.get("tags") or getattr(config, "CREATIVES_TAGS_DB_ID", "")
    if tags_id and tags_id != "dummy_id_for_dry_run":
        log.info("  Loading Tags relation map …")
        maps["tags"] = fetch_relation_map(client, tags_id)
        log.info(f"  → {len(maps['tags'])} tag(s)")
    else:
        maps["tags"] = {}

    return maps


def run(
    client,
    config,
    dry_run: bool,
    limit: int | None,
    page_id: str | None = None,
    dedupe: bool = False,
    cleanup_duplicates: bool = False,
) -> None:
    """
    Run the creatives enrichment pipeline.

    Parameters
    ----------
    client : notion_client.Client | None
        Initialised Notion SDK client (None when dry_run is True).
    config : module
        The core.config module, providing CREATIVES_DB_ID, etc.
    dry_run : bool
        If True, scrape only — no Notion API writes.
    limit : int | None
        Max number of pages to process. None = all matching pages.
    page_id : str | None
        If set, process only this single page (bypass query filter).
    dedupe : bool
        If True, run the deduplication job instead of the enrichment pipeline.
    cleanup_duplicates : bool
        If True, run the cleanup job to link/archive duplicates.
    """
    relations = {}
    try:
        from modules.config import resolve_database
        resolved = resolve_database("creatives", client=client)
        db_id = resolved.db_id
        relations = resolved.relations
    except Exception:
        db_id = getattr(config, "CREATIVES_DB_ID", "")

    if not db_id:
        log.error("NOTION_CREATIVES_DB_ID not set in .env and could not be resolved from schema manifest — aborting.")
        return

    if dedupe:
        return _run_deduplication(client, db_id, dry_run)

    if cleanup_duplicates:
        return _run_cleanup_duplicates(client, db_id, dry_run)

    # ── Resolve Notion token for uploads ──────────────────────────────────
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
        log.info("Querying Creatives DB (TBP-U=true, Archive=false, Duplicated=false) …")
        pages = _query_pages(client, db_id, config.RATE_LIMIT_DELAY)

    log.info(f"Found {len(pages)} page(s) to enrich\n")

    if not pages:
        return

    # Apply limit
    if limit is not None:
        pages = pages[:limit]

    # ── Step 2: Fetch relation maps ───────────────────────────────────────
    relation_maps = {}
    if not dry_run and client:
        log.info("── Loading relation maps ──")
        relation_maps = _fetch_relation_maps(client, relations)
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
        log.error("Install with: pip install playwright && playwright install chromium")
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
                log.warning(f"{prefix} ⏭  {raw_name[:50]} — no Web URL")
                stats["skipped"] += 1
                continue

            # ── Parse name ────────────────────────────────────────────────
            is_untitled = (raw_name == "(untitled)")
            cleaned_name, location_hint, short_about = parse_name(raw_name)

            # ── Scrape ────────────────────────────────────────────────────
            display_name = "(untitled)" if is_untitled else cleaned_name
            log.info(f"{prefix} 🔍  Scraping {display_name[:40]:<40} │ {web_url}")
            scraped = scrape_website(
                web_url,
                browser,
                cleaned_name="" if is_untitled else cleaned_name
            )

            if scraped and not scraped.get("is_parked") and not scraped.get("is_blank") and not scraped.get("dead_link") and scraped.get("status_code") not in (403, 404, 503):
                # Update cleaned_name/short_about from page_title if untitled or missing
                if scraped.get("page_title"):
                    pt_name, pt_loc, pt_about = parse_name(scraped["page_title"])
                    if is_untitled:
                        cleaned_name = pt_name
                        short_about = pt_about
                        location_hint = pt_loc
                        log.info(f"{prefix} 🏷️   Inferred name from title: {cleaned_name}")
                    elif not short_about and pt_about:
                        short_about = pt_about

                primary_text = " ".join(
                    s for s in (cleaned_name, short_about or "", scraped.get("page_title") or "", scraped.get("about") or "") if s
                )
                body_text = scraped.get("body_text", "")
                linkedin_url = scraped.get("linkedin")

                type_inferred = infer_type(primary_text, body_text=body_text, linkedin_url=linkedin_url)
                fields_inferred = infer_fields(primary_text, body_text=body_text)

                tags_map = relation_maps.get("tags", {})
                tags_inferred = infer_tags(primary_text, available_tags=tags_map, body_text=body_text)

                # Scan primary text first for JEV taxonomies, fallback to full text
                jev_fields = extract_taxonomies_from_text("creatives", "Fields", primary_text)
                if not jev_fields and body_text:
                    jev_fields = extract_taxonomies_from_text("creatives", "Fields", body_text[:5000])

                jev_tags = extract_taxonomies_from_text("creatives", "Tags", primary_text)
                if not jev_tags and body_text:
                    # Allow body matches for non-system/status tags
                    system_tags = {"changed", "other", "closed", "not loading", "403", "503"}
                    body_jev_tags = extract_taxonomies_from_text("creatives", "Tags", body_text[:5000])
                    jev_tags = [t for t in body_jev_tags if t.lower() not in system_tags]

                jev_types = extract_taxonomies_from_text("creatives", "Type", primary_text)
                if not type_inferred and jev_types:
                    type_inferred = "Solo" if "Solo" in jev_types else jev_types[0]

                scraped["fields_inferred"] = list(dict.fromkeys(fields_inferred + jev_fields))[:4]

                # Combine tags ensuring case-insensitive deduplication
                combined_tags: list[str] = []
                seen_tag_lowers: set[str] = set()
                for t in (tags_inferred + jev_tags):
                    if t.lower() not in seen_tag_lowers:
                        seen_tag_lowers.add(t.lower())
                        combined_tags.append(t)
                scraped["tags_inferred"] = combined_tags[:3]
                scraped["type_inferred"] = type_inferred

                log.info(
                    f"{prefix} 📊 Taxonomies resolved for '{display_name[:30]}': "
                    f"Type={type_inferred or '-'} │ "
                    f"Fields={scraped['fields_inferred']} (heur={len(fields_inferred)}, jev={len(jev_fields)}) │ "
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

            # ── Name fallback from title ──────────────────────────────────
            if is_untitled and scraped.get("page_title"):
                cleaned_name, location_hint, short_about = parse_name(scraped["page_title"])
                log.info(f"{prefix} 🏷️   Inferred name from title: {cleaned_name}")

            # ── Upload screenshot (if available) ──────────────────────────
            cover_upload_id: str | None = None
            if scraped.get("screenshot_bytes") and token and not dry_run:
                cover_upload_id = upload_screenshot(
                    token, scraped["screenshot_bytes"],
                )
                if cover_upload_id:
                    log.info(f"{prefix}       cover ✓")

            # ── Build payload ─────────────────────────────────────────────
            icon_url = scraped.get("icon_url")
            payload = build_update_payload(
                page=page,
                cleaned_name=cleaned_name,
                location_hint=location_hint,
                short_about=short_about,
                scraped=scraped,
                relation_maps=relation_maps,
                cover_upload_id=cover_upload_id,
                icon_url=icon_url,
                dry_run=dry_run,
            )

            if not payload:
                log.info(f"{prefix} ⏭  {cleaned_name[:50]} — nothing to update")
                stats["skipped"] += 1
                time.sleep(1)
                continue

            # ── Log what we found ─────────────────────────────────────────
            props = payload.get("properties", {})
            fields_set = [k for k in props if k not in ("TBP-U", "Autofilled")]
            extras = []
            if payload.get("cover"):
                extras.append("cover ✓")
            if payload.get("icon"):
                extras.append("icon ✓")

            # ── Update Notion page ────────────────────────────────────────
            if dry_run:
                log.info(
                    f"{prefix} DRY  {cleaned_name[:40]:<40} "
                    f"│ props={fields_set} {' '.join(extras)}"
                )
                stats["enriched"] += 1
            else:
                try:
                    # Separate cover, icon, and properties
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
        # ── Cleanup Playwright ────────────────────────────────────────────
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

    # ── Step 5: Summary ───────────────────────────────────────────────────
    log.info(f"\n{'─' * 55}")
    log.info(f"✅  {stats['enriched']} page(s) enriched")
    log.info(f"⏭   {stats['skipped']} page(s) skipped")
    log.info(f"❌  {stats['failed']} page(s) failed")

def _run_deduplication(client, db_id: str, dry_run: bool) -> None:
    """Run the deduplication job on the Creatives database."""
    log.info("\n── Starting Deduplication Job ───────────────────────")

    # 1. Query all pages
    pages = []
    cursor = None
    while True:
        kwargs = {"database_id": db_id, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor

        try:
            resp = query_database(client, db_id=kwargs.get("database_id"), **kwargs) if client else {"results": [], "has_more": False}
        except Exception as exc:
            log.error(f"Failed to query database: {exc}")
            return

        if client:
            pages.extend(resp.get("results", []))
            cursor = resp.get("next_cursor")
            if not resp.get("has_more") or not cursor:
                break
        else:
            log.info("Dry-run with no client provided. Skipping query.")
            break

    log.info(f"Fetched {len(pages)} pages.")

    # 2. Group by normalized URL
    from collections import defaultdict
    groups = defaultdict(list)
    for p in pages:
        url = _get_web_url(p)
        if url:
            groups[url].append(p)

    # 3. Find duplicates
    duplicates = {url: group for url, group in groups.items() if len(group) > 1}
    log.info(f"Found {len(duplicates)} URLs with duplicates.")

    stats = {"master_set": 0, "duplicated_set": 0, "failed": 0}

    for url, group in duplicates.items():
        log.info(f"\nProcessing group for: {url} ({len(group)} pages)")

        # Score each page to find the best master
        def _score_page(p):
            props = p.get("properties", {})
            score = 0
            if props.get("About", {}).get("rich_text"): score += 5
            if props.get("Location", {}).get("rich_text"): score += 1
            if props.get("Phone", {}).get("phone_number"): score += 1
            if props.get("Email", {}).get("email"): score += 1
            if props.get("Tags", {}).get("relation"): score += 1
            if props.get("Fields", {}).get("relation"): score += 1

            cover = p.get("cover")
            if cover: score += 1
            return score

        scored = [(p, _score_page(p)) for p in group]
        # Sort by score descending, then by created time ascending (older is better tie-breaker)
        scored.sort(key=lambda x: (-x[1], x[0].get("created_time", "")))

        master_page = scored[0][0]
        duplicate_pages = [s[0] for s in scored[1:]]

        master_name = _get_page_name(master_page)
        log.info(f"  → Master selected: {master_name} (Score: {scored[0][1]})")

        # Update Notion
        if dry_run:
            for dp in duplicate_pages:
                dp_name = _get_page_name(dp)
                log.info(f"  → Would mark as Duplicated: {dp_name}")
            continue

        # Build merge properties for master
        master_props = {"Master": {"checkbox": True}}
        for prop_name, prop_data in master_page.get("properties", {}).items():
            prop_type = prop_data.get("type")
            if not prop_type or prop_type in ("checkbox", "formula", "rollup", "created_time", "last_edited_time", "created_by", "last_edited_by"):
                continue

            is_empty = False
            if prop_type in ("rich_text", "title"):
                is_empty = not prop_data.get(prop_type)
            elif prop_type in ("number", "url", "email", "phone_number", "date"):
                is_empty = prop_data.get(prop_type) is None
            elif prop_type == "relation":
                is_empty = not prop_data.get("relation")

            if is_empty:
                for dp in duplicate_pages:
                    dp_prop = dp.get("properties", {}).get(prop_name)
                    if not dp_prop or dp_prop.get("type") != prop_type:
                        continue

                    dp_empty = True
                    if prop_type in ("rich_text", "title"):
                        dp_empty = not dp_prop.get(prop_type)
                    elif prop_type in ("number", "url", "email", "phone_number", "date"):
                        dp_empty = dp_prop.get(prop_type) is None
                    elif prop_type == "relation":
                        dp_empty = not dp_prop.get("relation")

                    if not dp_empty:
                        master_props[prop_name] = {prop_type: dp_prop[prop_type]}
                        log.info(f"  → Merging missing '{prop_name}' from duplicate into master.")
                        break

        # Update master
        try:
            client.pages.update(
                page_id=master_page["id"],
                properties=master_props
            )
            stats["master_set"] += 1
        except Exception as exc:
            log.error(f"  ❌ Failed to update Master: {exc}")
            stats["failed"] += 1

        # Update duplicates
        for dp in duplicate_pages:
            dp_name = _get_page_name(dp)
            log.info(f"  → Marking as Duplicated: {dp_name}")
            try:
                client.pages.update(
                    page_id=dp["id"],
                    properties={"Duplicated": {"checkbox": True}}
                )
                stats["duplicated_set"] += 1
            except Exception as exc:
                log.error(f"  ❌ Failed to update Duplicated: {exc}")
                stats["failed"] += 1

        import time
        time.sleep(0.5)

    log.info(f"\n{'─' * 55}")
    log.info(f"✅  {stats['master_set']} Master checkboxes set")
    log.info(f"✅  {stats['duplicated_set']} Duplicated checkboxes set")
    log.info(f"❌  {stats['failed']} page(s) failed to update")

def _run_cleanup_duplicates(client, db_id: str, dry_run: bool) -> None:
    """Map duplicates to masters, archive them if fully subsumed, and link them."""
    log.info("\n── Starting Duplicate Cleanup Job ────────────────────")

    # 1. Query pages
    pages = []
    cursor = None
    while True:
        kwargs = {"database_id": db_id, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor

        try:
            resp = query_database(client, db_id=kwargs.get("database_id"), **kwargs) if client else {"results": [], "has_more": False}
        except Exception as exc:
            log.error(f"Failed to query database: {exc}")
            return

        if client:
            pages.extend(resp.get("results", []))
            cursor = resp.get("next_cursor")
            if not resp.get("has_more") or not cursor:
                break
        else:
            log.info("Dry-run with no client provided. Skipping query.")
            break

    log.info(f"Fetched {len(pages)} pages.")

    # 2. Group by normalized URL
    from collections import defaultdict
    groups = defaultdict(list)
    for p in pages:
        url = _get_web_url(p)
        if url:
            groups[url].append(p)

    duplicates = {url: group for url, group in groups.items() if len(group) > 1}
    log.info(f"Found {len(duplicates)} URLs with duplicates.")

    stats = {"archived": 0, "linked_master": 0, "linked_duplicates": 0, "failed": 0}

    for url, group in duplicates.items():
        log.info(f"\nProcessing group for: {url} ({len(group)} pages)")

        # Identify Master: either it has Master=True, or fallback to older
        masters = [p for p in group if p.get("properties", {}).get("Master", {}).get("checkbox")]
        if masters:
            master_page = masters[0]
            # Duplicates are the rest
            duplicate_pages = [p for p in group if p["id"] != master_page["id"]]
        else:
            # Fallback score if Master wasn't explicitly flagged
            def _score_page(p):
                props = p.get("properties", {})
                score = 0
                if props.get("About", {}).get("rich_text"): score += 5
                if props.get("Location", {}).get("rich_text"): score += 1
                if props.get("Phone", {}).get("phone_number"): score += 1
                if props.get("Email", {}).get("email"): score += 1
                if props.get("Tags", {}).get("relation"): score += 1
                if props.get("Fields", {}).get("relation"): score += 1
                return score

            scored = [(p, _score_page(p)) for p in group]
            scored.sort(key=lambda x: (-x[1], x[0].get("created_time", "")))
            master_page = scored[0][0]
            duplicate_pages = [s[0] for s in scored[1:]]

        master_name = _get_page_name(master_page)
        log.info(f"  → Identified Master: {master_name} ({master_page['id']})")

        master_props = master_page.get("properties", {})

        any_archived = False
        for dp in duplicate_pages:
            dp_name = _get_page_name(dp)
            dp_id = dp["id"]
            dp_props = dp.get("properties", {})

            # Check if all non-empty properties in dp are subsumed by master
            is_subsumed = True
            for prop_name, dp_prop in dp_props.items():
                prop_type = dp_prop.get("type")
                if not prop_type or prop_type in ("checkbox", "formula", "rollup", "created_time", "last_edited_time", "created_by", "last_edited_by", "title"):
                    continue

                # Is dp_prop empty?
                dp_empty = True
                if prop_type in ("rich_text", "title"):
                    dp_empty = not dp_prop.get(prop_type)
                elif prop_type in ("number", "url", "email", "phone_number", "date"):
                    dp_empty = dp_prop.get(prop_type) is None
                elif prop_type == "relation":
                    dp_empty = not dp_prop.get("relation")

                if not dp_empty:
                    # Check master
                    mp_prop = master_props.get(prop_name)
                    if not mp_prop or mp_prop.get("type") != prop_type:
                        is_subsumed = False
                        break

                    # Compare content (basic structural equality or subsets)
                    if prop_type in ("number", "url", "email", "phone_number"):
                        if mp_prop.get(prop_type) != dp_prop.get(prop_type):
                            is_subsumed = False
                            break
                    elif prop_type in ("rich_text", "title"):
                        # Just check if master has *any* rich_text.
                        # In a stricter sense, we should check substring, but usually if master has About it's better
                        if not mp_prop.get(prop_type):
                            is_subsumed = False
                            break
                    elif prop_type == "relation":
                        mp_ids = {r["id"] for r in mp_prop.get("relation", [])}
                        dp_ids = {r["id"] for r in dp_prop.get("relation", [])}
                        if not dp_ids.issubset(mp_ids):
                            is_subsumed = False
                            break
                    elif prop_type == "date":
                        # Assume if master has a date, it covers it
                        if mp_prop.get("date") is None:
                            is_subsumed = False
                            break

            # Process duplicate based on subsumption
            if is_subsumed:
                log.info(f"  → Duplicate {dp_name} is fully subsumed by Master. Will archive.")
                if not dry_run:
                    try:
                        # Notion API archive (soft delete)
                        client.pages.update(page_id=dp_id, archived=True)
                        stats["archived"] += 1
                        any_archived = True

                        # Also attempt to set custom Archive checkbox if it exists just in case
                        # (Doing this in a separate call or same call? Same call is fine but archived=True removes it from view)
                    except Exception as exc:
                        log.error(f"  ❌ Failed to archive duplicate {dp_id}: {exc}")
                        stats["failed"] += 1
            else:
                log.info(f"  → Duplicate {dp_name} has unique data not in Master. Linking only.")

            # Optionally link them (best effort on Duplicate page, assuming 'Master Page' relation or similar)
            # We try to update Duplicate with Master ID if a relation property named 'Master' or 'Master Page' exists
            if not dry_run and not is_subsumed: # if archived, no need to link
                try:
                    # We don't know the exact name of the relation property. We can inspect the duplicate's properties.
                    relation_prop_name = None
                    for name, meta in dp_props.items():
                        if meta.get("type") == "relation" and name.lower() in ("master", "master page"):
                            relation_prop_name = name
                            break

                    if relation_prop_name:
                        client.pages.update(
                            page_id=dp_id,
                            properties={relation_prop_name: {"relation": [{"id": master_page["id"]}]}}
                        )
                        stats["linked_duplicates"] += 1
                except Exception as exc:
                    log.error(f"  ❌ Failed to link Master -> Duplicate: {exc}")

        # Update Master (link relation, and unflag Master checkbox if any duplicate was removed)
        if not dry_run:
            try:
                master_update_props = {}

                # If we archived any duplicates, unflag Master on the retained creative
                if any_archived:
                    master_update_props["Master"] = {"checkbox": False}

                relation_prop_name = None
                for name, meta in master_props.items():
                    if meta.get("type") == "relation" and name.lower() in ("duplicates", "duplicate pages"):
                        relation_prop_name = name
                        break

                if relation_prop_name:
                    # Collect IDs of duplicates that were NOT archived (or all if you want to keep history)
                    # Let's link all of them just in case
                    dup_ids = [{"id": dp["id"]} for dp in duplicate_pages]
                    master_update_props[relation_prop_name] = {"relation": dup_ids}

                if master_update_props:
                    client.pages.update(
                        page_id=master_page["id"],
                        properties=master_update_props
                    )
                    stats["linked_master"] += 1
            except Exception as exc:
                log.error(f"  ❌ Failed to update Master page: {exc}")

        import time
        time.sleep(0.5)

    log.info(f"\n{'─' * 55}")
    log.info(f"✅  {stats['archived']} Duplicate(s) archived (removed)")
    log.info(f"✅  {stats['linked_master']} Master pages linked to their duplicates")
    log.info(f"✅  {stats['linked_duplicates']} Duplicates linked to their master")
    log.info(f"❌  {stats['failed']} operation(s) failed")
