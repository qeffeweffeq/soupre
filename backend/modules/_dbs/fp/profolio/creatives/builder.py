"""
creatives/builder.py
─────────────────────────────────────────────────────────────────────────────
Build Notion API payloads for Creatives page updates.

Responsibilities:
  - build_update_payload() — assemble the full pages.update() payload

Never overwrites a non-empty Notion field. Name is always rewritten.
"""

import logging
import re

from modules.notion.properties import is_empty_property
from modules.utils.url import normalize_url
from modules.utils.location import expand_location
from modules.utils.text import parse_name

log = logging.getLogger(__name__)

_ABOUT_MAX_LEN = 2000

# ---------------------------------------------------------------------------
# Payload builder
# ---------------------------------------------------------------------------

def _has_only_url_filled(page: dict) -> bool:
    """Return True if the page has ONLY Web filled (ignoring checkboxes/metadata/Name)."""
    props = page.get("properties", {})
    if not props.get("Web", {}).get("url"):
        return False

    for prop_name, prop_data in props.items():
        if prop_name in ("Name", "Web", "TBP-U", "Archive", "Autofilled", "Not Autofilled", "Duplicated", "Master", "Pin", "Rank"):
            continue

        prop_type = prop_data.get("type")
        if not prop_type or prop_type in ("checkbox", "formula", "rollup", "created_time", "last_edited_time", "created_by", "last_edited_by"):
            continue

        if prop_type in ("rich_text", "title"):
            if prop_data.get(prop_type): return False
        elif prop_type in ("number", "url", "email", "phone_number", "date"):
            if prop_data.get(prop_type) is not None: return False
        elif prop_type == "relation":
            if prop_data.get("relation"): return False

    return True


def build_update_payload(
    page: dict,
    cleaned_name: str,
    location_hint: str | None,
    short_about: str | None,
    scraped: dict,
    relation_maps: dict,
    cover_upload_id: str | None,
    icon_url: str | None,
    dry_run: bool,
) -> dict:
    """Assemble the full ``pages.update()`` payload for a Creatives page."""
    result: dict = {}
    properties: dict = {}

    from datetime import datetime
    # If the page was just a raw URL drop, tag it with today's date & year.
    if _has_only_url_filled(page):
        now = datetime.now()
        properties["Date"] = {"date": {"start": now.strftime("%Y-%m-%d")}}
        properties["Year"] = {"multi_select": [{"name": now.strftime("%y"), "color": "default"}]}

    # Generic Autofill: Find any other empty date fields and populate them based on creation time
    from modules.utils.dates import populate_empty_date_fields, calculate_dynamic_date
    populate_empty_date_fields(page, properties)

    # Autofill Year if empty
    if is_empty_property(page, "Year", "multi_select"):
        # Use computed date if it exists, otherwise fallback to now
        date_str = calculate_dynamic_date(page.get("created_time"))
        if date_str:
            year_val = date_str[2:4] # Extract "yy" from "YYYY-MM-DD"
            properties["Year"] = {"multi_select": [{"name": year_val, "color": "default"}]}

    # -- Rating logic based on Notes ------------------------------------
    notes_prop = page.get("properties", {}).get("Notes", {}).get("rich_text", [])
    existing_notes = "".join(t.get("plain_text", "") or t.get("text", {}).get("content", "") for t in notes_prop)

    a_matches = re.findall(r'A+', existing_notes)
    if a_matches:
        max_a = max(len(m) for m in a_matches)
        if max_a >= 3:
            properties["Pin"] = {"checkbox": True}
        if max_a > 3:
            wow_tag_id = relation_maps.get("tags", {}).get("wow")
            if wow_tag_id:
                existing_tags = page.get("properties", {}).get("Tags", {}).get("relation", [])
                if not any(t.get("id") == wow_tag_id for t in existing_tags):
                    existing_tags.append({"id": wow_tag_id})
                    properties["Tags"] = {"relation": existing_tags}

    # Handle dead links (404, 403, 503, DNS error, timeout) or completely blank pages or parked domains
    if scraped.get("status_code") in (404, 403, 503) or scraped.get("dead_link") or scraped.get("is_blank") or scraped.get("is_parked"):
        properties["Name"] = {"title": [{"text": {"content": cleaned_name}}]}
        properties["Archive"] = {"checkbox": True}
        properties["TBP-U"] = {"checkbox": False}
        properties["Not Autofilled"] = {"checkbox": True}

        # Add the 'Closed' tag for all these conditions except 404
        if scraped.get("status_code") != 404:
            closed_tag_id = relation_maps.get("tags", {}).get("closed")
            if closed_tag_id:
                # Append to existing tags if any
                existing_tags = page.get("properties", {}).get("Tags", {}).get("relation", [])
                # Only add if not already present
                if not any(t.get("id") == closed_tag_id for t in existing_tags):
                    existing_tags.append({"id": closed_tag_id})
                    properties["Tags"] = {"relation": existing_tags}

        if scraped.get("status_code") in (503, 403):
            status_tag_str = str(scraped.get("status_code"))
            tag_id = relation_maps.get("tags", {}).get(status_tag_str)
            if tag_id:
                existing_tags = page.get("properties", {}).get("Tags", {}).get("relation", [])
                if not any(t.get("id") == tag_id for t in existing_tags):
                    existing_tags.append({"id": tag_id})
                    properties["Tags"] = {"relation": existing_tags}

        result["properties"] = properties
        return result

    # Name — always write (cleaned name is always an improvement)
    properties["Name"] = {"title": [{"text": {"content": cleaned_name}}]}

    # Redirect handling
    original_url = page.get("properties", {}).get("Web", {}).get("url")
    final_url = scraped.get("final_url")

    if original_url and final_url:
        norm_orig = normalize_url(original_url)
        norm_final = normalize_url(final_url)
        if norm_orig and norm_final and norm_orig != norm_final:
            properties["Web"] = {"url": final_url}

            notes_prop = page.get("properties", {}).get("Notes", {}).get("rich_text", [])
            existing_notes = "".join(t.get("plain_text", "") or t.get("text", {}).get("content", "") for t in notes_prop)

            new_notes = f"{existing_notes} | {original_url}" if existing_notes else original_url
            properties["Notes"] = {
                "rich_text": [{"text": {"content": new_notes}}]
            }

    # About — only if empty; truncate to 2000 chars
    if is_empty_property(page, "About", "rich_text") and scraped.get("about"):
        properties["About"] = {
            "rich_text": [{"text": {"content": scraped["about"][:_ABOUT_MAX_LEN]}}]
        }

    # Short About — only if empty and we extracted it
    if short_about and is_empty_property(page, "Short About", "rich_text"):
        properties["Short About"] = {
            "rich_text": [{"text": {"content": short_about}}]
        }

    # Location — only if empty; use scraped location or expanded location_hint
    if is_empty_property(page, "Location", "rich_text"):
        loc = scraped.get("location") or (
            expand_location(location_hint) if location_hint else None
        )
        if loc:
            properties["Location"] = {
                "rich_text": [{"text": {"content": loc}}],
            }

    # Email, Phone — only if empty
    if is_empty_property(page, "Email", "email") and scraped.get("email"):
        properties["Email"] = {"email": scraped["email"]}

    if is_empty_property(page, "Phone", "phone_number") and scraped.get("phone"):
        properties["Phone"] = {"phone_number": scraped["phone"]}

    # Instagram, LinkedIn — only if empty
    if is_empty_property(page, "Instagram", "url") and scraped.get("instagram"):
        properties["Instagram"] = {"url": scraped["instagram"]}

    if is_empty_property(page, "Linkedin", "url") and scraped.get("linkedin"):
        properties["Linkedin"] = {"url": scraped["linkedin"]}

    # Founded — only if empty
    if is_empty_property(page, "Founded", "rich_text") and scraped.get("founded"):
        properties["Founded"] = {
            "rich_text": [{"text": {"content": str(scraped["founded"])}}],
        }

    # Type (relation) — only if empty
    if is_empty_property(page, "Type", "relation") and scraped.get("type_inferred"):
        type_name = scraped["type_inferred"]
        page_id = relation_maps.get("types", {}).get(type_name.lower())
        if page_id:
            properties["Type"] = {"relation": [{"id": page_id}]}

    # Fields (relation) — only if empty
    if is_empty_property(page, "Fields", "relation") and scraped.get("fields_inferred"):
        ids = [
            relation_maps.get("fields", {}).get(f.lower())
            for f in scraped["fields_inferred"]
        ]
        ids = [i for i in ids if i]  # filter None
        if ids:
            properties["Fields"] = {"relation": [{"id": i} for i in ids]}

    # Tags (relation) — only if empty
    if is_empty_property(page, "Tags", "relation") and scraped.get("tags_inferred"):
        ids = [
            relation_maps.get("tags", {}).get(t.lower())
            for t in scraped["tags_inferred"]
        ]
        ids = [i for i in ids if i]
        if ids:
            properties["Tags"] = {"relation": [{"id": i} for i in ids]}

    # TBP-U → false, Autofilled → true (markers for successful enrichment)
    properties["TBP-U"] = {"checkbox": False}
    properties["Autofilled"] = {"checkbox": True}

    result["properties"] = properties

    # Cover — file upload (if we uploaded a screenshot)
    if cover_upload_id:
        result["cover"] = {
            "type": "file_upload",
            "file_upload": {"id": cover_upload_id},
        }

    # Icon — external URL (no upload needed for favicons)
    if icon_url:
        result["icon"] = {
            "type": "external",
            "external": {"url": icon_url},
        }

    return result
