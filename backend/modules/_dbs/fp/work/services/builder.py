"""
services/builder.py
─────────────────────────────────────────────────────────────────────────────
Build Notion API payloads for Services page updates.
"""

import logging
from urllib.parse import urlparse
from modules.notion.properties import is_empty_property
from modules.utils.text import parse_name
from modules.utils.url import normalize_url

log = logging.getLogger(__name__)

_NOTES_MAX_LEN = 2000

def _get_domain(url: str) -> str:
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    if domain.startswith("www."):
        domain = domain[4:]
    return domain

def build_update_payload(
    page: dict,
    cleaned_name: str,
    scraped: dict,
    relation_maps: dict,
    cover_upload_id: str | None,
    icon_url: str | None,
    dry_run: bool,
    short_about: str | None = None,
) -> dict:
    """Assemble the full pages.update() payload for a Services page."""
    result: dict = {}
    properties: dict = {}

    # Handle dead links (404, 403, 503, DNS error, timeout) or parked domains
    if scraped.get("status_code") in (404, 403, 503) or scraped.get("dead_link") or scraped.get("is_blank") or scraped.get("is_parked"):
        properties["Name"] = {"title": [{"text": {"content": cleaned_name}}]}

        if "Archive" in page.get("properties", {}):
            properties["Archive"] = {"checkbox": True}

        if "TBP-U" in page.get("properties", {}):
            properties["TBP-U"] = {"checkbox": False}

        if "Not Autofilled" in page.get("properties", {}):
            properties["Not Autofilled"] = {"checkbox": True}

        if scraped.get("status_code") != 404:
            # Maybe add a "Closed" or "Error" tag if available
            closed_tag_id = relation_maps.get("tags", {}).get("closed")
            if closed_tag_id:
                existing_tags = page.get("properties", {}).get("Tags", {}).get("relation", [])
                if not any(t.get("id") == closed_tag_id for t in existing_tags):
                    existing_tags.append({"id": closed_tag_id})
                    properties["Tags"] = {"relation": existing_tags}

        result["properties"] = properties
        return result

    # Name — always write (cleaned name is always an improvement)
    properties["Name"] = {"title": [{"text": {"content": cleaned_name}}]}

    # Redirect handling (URL instead of Web)
    original_url = page.get("properties", {}).get("URL", {}).get("url")
    final_url = scraped.get("final_url")

    if original_url and final_url:
        norm_orig = normalize_url(original_url)
        norm_final = normalize_url(final_url)
        if norm_orig and norm_final and norm_orig != norm_final:
            properties["URL"] = {"url": final_url}

    # Domains — skip if it's a relation (as per bookmarks schema), only fill if rich_text
    active_url = final_url or original_url
    if active_url and "Domains" in page.get("properties", {}) and page["properties"]["Domains"].get("type") == "rich_text":
        if is_empty_property(page, "Domains", "rich_text"):
            domain_str = _get_domain(active_url)
            if domain_str:
                properties["Domains"] = {
                    "rich_text": [{"text": {"content": domain_str}}]
                }

    # Generic Autofill: Find any empty date fields and populate them based on creation time
    from modules.utils.dates import populate_empty_date_fields
    populate_empty_date_fields(page, properties)

    # Short Description and Description handling
    page_props = page.get("properties", {})
    has_short_desc = "Short Description" in page_props
    has_desc = "Description" in page_props
    has_notes = "Notes" in page_props
    about_text = scraped.get("about")

    if has_short_desc and short_about and is_empty_property(page, "Short Description", "rich_text"):
        properties["Short Description"] = {
            "rich_text": [{"text": {"content": short_about}}]
        }

    if has_desc and is_empty_property(page, "Description", "rich_text"):
        # If no Short Description property exists, fallback field/topic to Description; otherwise use about
        if not has_short_desc and short_about:
            desc_val = short_about
        else:
            desc_val = about_text or short_about

        if desc_val:
            properties["Description"] = {
                "rich_text": [{"text": {"content": desc_val[:_NOTES_MAX_LEN]}}]
            }

    if has_notes and is_empty_property(page, "Notes", "rich_text"):
        notes_val = short_about if (not has_desc and not has_short_desc) else (about_text or short_about)
        if notes_val:
            properties["Notes"] = {
                "rich_text": [{"text": {"content": notes_val[:_NOTES_MAX_LEN]}}]
            }

    # Roles (relation) — only if empty
    if "Roles" in page.get("properties", {}) and is_empty_property(page, "Roles", "relation") and scraped.get("roles_inferred"):
        ids = [
            relation_maps.get("roles", {}).get(r.lower())
            for r in scraped["roles_inferred"]
        ]
        ids = [i for i in ids if i]
        if ids:
            properties["Roles"] = {"relation": [{"id": i} for i in ids]}

    # Types (relation) — only if empty
    if "Types" in page.get("properties", {}) and is_empty_property(page, "Types", "relation") and scraped.get("types_inferred"):
        ids = [
            relation_maps.get("types", {}).get(t.lower())
            for t in scraped["types_inferred"]
        ]
        ids = [i for i in ids if i]
        if ids:
            properties["Types"] = {"relation": [{"id": i} for i in ids]}

    # Tags (relation) — only if empty
    if "Tags" in page.get("properties", {}) and is_empty_property(page, "Tags", "relation") and scraped.get("tags_inferred"):
        ids = [
            relation_maps.get("tags", {}).get(t.lower())
            for t in scraped["tags_inferred"]
        ]
        ids = [i for i in ids if i]
        if ids:
            properties["Tags"] = {"relation": [{"id": i} for i in ids]}

    # Flags
    if "TBP-U" in page.get("properties", {}):
        properties["TBP-U"] = {"checkbox": False}

    if "Autofilled" in page.get("properties", {}):
        properties["Autofilled"] = {"checkbox": True}

    result["properties"] = properties

    # Cover
    if cover_upload_id:
        result["cover"] = {
            "type": "file_upload",
            "file_upload": {"id": cover_upload_id},
        }

    # Icon
    if icon_url:
        result["icon"] = {
            "type": "external",
            "external": {"url": icon_url},
        }

    return result
