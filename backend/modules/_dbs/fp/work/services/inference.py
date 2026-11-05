"""
services/inference.py
─────────────────────────────────────────────────────────────────────────────
Domain rules and heuristics for auto-tagging Services pages based on scraped content.
"""

import re
import logging

log = logging.getLogger(__name__)

# Roles mapping (based on original bookmarks ROOT_ROLE_MAP)
ROLE_KEYWORDS = {
    r"\bfinance\b|\bfinancial\b|\binvestment\b|\binvesting\b": "Financial",
    r"\butil\b|\butility\b|\btool\b|\bhelper\b": "Joker",
    r"\bmanagement\b|\blegal\b|\badmin\b|\baccounting\b": "Management",
    r"\bmarketing\b|\bseo\b|\bads\b": "Marketing",
    r"\bdesign\b|\bui\b|\bux\b|\bgraphics\b": "Design",
    r"\bdevelop\b|\bcode\b|\bapi\b|\bprogramming\b": "Technology",
    r"\bwrite\b|\bcontent\b|\bcopywriting\b": "Writing",
    r"\bart\b|\bmusic\b|\bvideo\b|\bcreative\b": "Artists",
    r"\beconomics\b": "Economics",
    r"\bassistant\b|\bai\b": "Assistant",
    r"\bcreativity\b": "Creativity",
    r"\bbuilder\b": "Builder",
    r"\bcontent creation\b": "Content Creation",
}

# Type override rules
TYPE_RULES = [
    (r"\bservice\b|\bsmtp\b|\bcdn\b|\bhosting\b",  "Service"),
    (r"\bplugin\b|\bextension\b",                  "Plugin"),
    (r"\bmodel\b|\bdiffusion\b|\bgpt\b|\bllm\b",   "Model"),
    (r"\bapp\b|\bapplication\b|\bide\b",           "Application"),
]

# Tag regex patterns mapping known database tags to synonyms/stems
TAG_PATTERNS = {
    "iconics": r"\b(?:icons?|iconics?|iconography|favicons?)\b",
    "archive": r"\b(?:archives?|archiving|archived|wayback)\b",
    "calc": r"\b(?:calc|calculat(?:or|ors|ion|ions))\b",
    "convert": r"\b(?:convert|convert(?:er|ers|ion|ing))\b",
    "fonts": r"\b(?:fonts?|typefaces?)\b",
    "db": r"\b(?:db|databases?|sql|nosql)\b",
    "domains": r"\b(?:domains?|whois|tld|registrars?)\b",
    "flights": r"\b(?:flights?|airfare|airline)\b",
    "maps": r"\b(?:maps?|mapping|geolocat\w+|coordinates)\b",
    "tables": r"\b(?:tables?|spreadsheets?|csv|tsv)\b",
    "trips": r"\b(?:trips?|travel|tourism)\b",
    "strs": r"\b(?:strings?|strs?)\b",
    "cronjob": r"\b(?:cron|crontabs?|cronjobs?|scheduled\s*tasks?)\b",
    "free stock": r"\b(?:free\s*stock|stock\s*(?:photos?|images?|vectors?|videos?))\b",
    "qr": r"\bqr(?:\s*codes?)?\b",
    "grammar": r"\b(?:grammar|spell(?:ing)?\s*check(?:er)?)\b",
    "inspection": r"\b(?:inspect|inspector|inspection)\b",
    "case": r"\b(?:case\s*convert(?:er)?|camelcase|snake_case|kebab-case|title\s*case|uppercase|lowercase)\b",
    "weather": r"\b(?:weather|forecast|meteorolog\w+)\b",
    "crypto": r"\b(?:crypto|cryptocurrency|bitcoin|ethereum|blockchain)\b",
    "currency": r"\b(?:currency|currencies|exchange\s*rate|forex)\b",
    "email": r"\b(?:emails?|newsletter|inbox|smtp|mail)\b",
    "privacy": r"\b(?:privacy|gdpr|cookie\s*consent)\b",
}

# Non-semantic or default-folder tags to exclude from page inference
EXCLUDED_TAGS = {"other", "utils", "application", "model"}

def infer_roles(primary_text: str, body_text: str = "") -> list[str]:
    """
    Return a focused list of at most 2 Role names inferred from the text.
    Prioritizes matches in primary text (title, short about, meta description).
    """
    primary = primary_text.lower()
    body = body_text.lower() if body_text else ""
    scored_roles: dict[str, int] = {}

    for pattern, role in ROLE_KEYWORDS.items():
        score = 0
        if re.search(pattern, primary):
            score += 3
        elif body and re.search(pattern, body):
            # Require at least 2 occurrences in body to count
            if len(re.findall(pattern, body)) >= 2:
                score += 1

        if score >= 2:
            scored_roles[role] = max(scored_roles.get(role, 0), score)

    if not scored_roles:
        return ["Technology"]

    # Sort by score descending, cap at max 2 roles
    sorted_roles = sorted(scored_roles.items(), key=lambda x: x[1], reverse=True)
    return [r[0] for r in sorted_roles[:2]]


def infer_types(primary_text: str, body_text: str = "") -> list[str]:
    """Return a single Type name inferred from the text."""
    primary = primary_text.lower()
    body = body_text.lower() if body_text else ""

    # Check primary text first
    for pattern, type_val in TYPE_RULES:
        if re.search(pattern, primary):
            return [type_val]

    # Check body text
    for pattern, type_val in TYPE_RULES:
        if body and re.search(pattern, body):
            return [type_val]

    return ["Utils"]  # Fallback


def infer_tags(primary_text: str, available_tags: dict, body_text: str = "") -> list[str]:
    """
    Match words against known Tags with strict word boundaries, disambiguation,
    and relevance scoring. Returns at most 4 highly relevant tags.
    available_tags: {lowercase_name: page_id}
    """
    if not available_tags:
        return []

    primary = primary_text.lower()
    body = body_text.lower() if body_text else ""
    scored_tags: dict[str, int] = {}

    tag_keys = available_tags.keys() if hasattr(available_tags, "keys") else available_tags

    for tag_name in tag_keys:
        tag_lower = tag_name.lower().strip()
        if tag_lower in EXCLUDED_TAGS or len(tag_lower) < 2:
            continue

        pat = TAG_PATTERNS.get(tag_lower, rf"\b{re.escape(tag_lower)}\b")

        # Ambiguous keyword guards
        if tag_lower == "sea":
            pat = r"\b(?:search engine advertising|sea)\b"
            if not re.search(r"\b(?:marketing|advertising|sem|ads|adwords)\b", primary + " " + body):
                continue
        elif tag_lower == "ide":
            pat = r"\b(?:ide|integrated development environment)\b"
            if not re.search(r"\b(?:code|editor|programming|developer|development|compiler)\b", primary):
                continue
        elif tag_lower in ("data", "css", "audio", "search", "text", "design", "media"):
            # Highly generic terms: only match if present in primary text (title / about)
            if not re.search(pat, primary):
                continue

        score = 0
        if re.search(pat, primary):
            score += 5
        elif body:
            matches = len(re.findall(pat, body))
            if matches >= 5:
                score += 3
            elif matches >= 3:
                score += 2
            elif matches >= 1:
                score += 1

        if score >= 3:
            scored_tags[tag_lower] = score

    if not scored_tags:
        return []

    # Sort by score descending, cap at max 4 tags
    sorted_tags = sorted(scored_tags.items(), key=lambda x: x[1], reverse=True)
    return [t[0] for t in sorted_tags[:4]]
