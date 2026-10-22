"""
creatives/inference.py
─────────────────────────────────────────────────────────────────────────────
Infer type, fields, and tags from text.
"""

import re

ORG_KEYWORDS = {
    "studio", "agency", "group", "collective", "lab",
    "factory", "bureau", "atelier", "partners", "associates",
    "consulenza", "agenzia", "società",
}
SOLO_KEYWORDS = {
    "designer", "photographer", "developer", "freelance",
    "personal", "portfolio", "artist", "illustrator",
    "consultant", "coder", "consulente",
}

FIELD_KEYWORDS = {
    r"\b(?:design|progettazione)\b": "Design",
    r"\bbranding\b": "Branding",
    r"\b(?:agency|agenzia)\b": "Agency",
    r"\bweb\b": "Web",
    r"\bdigital\b": "Digital",
    r"\b(?:photography|fotografia)\b": "Photography",
    r"\b(?:illustration|illustrazione)\b": "Illustration",
    r"\b(?:typography|tipografia)\b": "Typography",
    r"\b(?:ui/ux|ux/ui|ui\s+design|ux\s+design)\b": "UI/UX",
    r"\bfrontend\b": "Frontend",
    r"\b(?:development|sviluppo)\b": "Development",
    r"\b(?:creative|creativo)\b": "Creative",
    r"\bvideo\b": "Video",
    r"\b(?:fashion|moda)\b": "Fashion",
    r"\b(?:print|stampa)\b": "Print",
    r"\b(?:ai|intelligenza artificiale|artificial intelligence)\b": "AI",
    r"\bstudio\b": "Studio",
    r"\b(?:marketing|promozione)\b": "Marketing",
    r"\bcopywriter\b": "Copywriter",
    r"\b(?:3d|three.?d)\b": "3D",
    r"\b(?:architecture|architettura)\b": "Architecture",
    r"\b(?:product|prodotto)\b": "Product",
    r"\bbackend\b": "Backend",
    r"\b(?:hotellerie|hotel|alberghiera|hospitality)\b": "Hotellerie",
    r"\b(?:information|informazione)\b": "Information",
    r"\b(?:data|dati)\b": "Data",
}

TAG_KEYWORDS = {
    r"\bportfolio\b": "Portfolio",
    r"\banimation\b": "Animation",
    r"\b(?:interactive|interaction|interactions)\b": "Interactions",
    r"\bcontents?\b": "Contents",
    r"\blayout\b": "Layout",
    r"\btypography\b": "Typography",
    r"\b(?:collaborate|collaboration)\b": "Collaboration",
    r"\b(?:ui|ux)\b": "UI/UX",
    r"\b(?:hotel marketing|marketing alberghiero)\b": "Hotel Marketing",
}


# Known software studio tools to mask out so they don't trigger "studio" Org match
SOFTWARE_STUDIO_PATTERNS = re.compile(
    r"\b(?:google\s+ai\s+studio|visual\s+studio(?:\s+code)?|android\s+studio|"
    r"obs\s+studio|roblox\s+studio|apple\s+studio|sound\s+studio|game\s+studio|fl\s+studio)\b",
    re.IGNORECASE,
)

# High-confidence personal indicators
POSSESSIVE_NAME_PATTERN = re.compile(
    r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)'s\b"
)

LINKEDIN_PERSONAL_PATTERN = re.compile(
    r"linkedin\.com/in/[a-zA-Z0-9_\-]+",
    re.IGNORECASE,
)


def _sanitize_for_org_detection(text: str) -> str:
    """Mask out software tools like Visual Studio Code or Google AI Studio."""
    return SOFTWARE_STUDIO_PATTERNS.sub(" ", text)


def infer_type(
    primary_text: str = "",
    body_text: str = "",
    linkedin_url: str | None = None,
) -> str | None:
    """
    Infer creative type (Org / Solo) from primary text (title, about, name)
    and optional body text, plus contact signals like personal LinkedIn profiles.
    Weighted scoring avoids false positives from random body mentions.
    """
    primary = _sanitize_for_org_detection(primary_text or "")
    body = _sanitize_for_org_detection(body_text or "")
    combined = (primary + " " + body).lower()

    # 1. Strong Solo indicators
    has_personal_linkedin = bool(linkedin_url and LINKEDIN_PERSONAL_PATTERN.search(linkedin_url))
    has_possessive_person = bool(POSSESSIVE_NAME_PATTERN.search(primary_text or ""))

    # Count weighted scores
    org_score = 0
    solo_score = 0

    if has_personal_linkedin:
        solo_score += 3
    if has_possessive_person:
        solo_score += 3

    for kw in ORG_KEYWORDS:
        pat = r"\b" + re.escape(kw) + r"\b"
        if re.search(pat, primary.lower()):
            org_score += 4
        elif body:
            matches = len(re.findall(pat, body.lower()))
            if matches >= 2:
                org_score += 2
            elif matches == 1:
                org_score += 1

    for kw in SOLO_KEYWORDS:
        pat = r"\b" + re.escape(kw) + r"\b"
        if re.search(pat, primary.lower()):
            solo_score += 4
        elif body:
            matches = len(re.findall(pat, body.lower()))
            if matches >= 2:
                solo_score += 2
            elif matches == 1:
                solo_score += 1

    if org_score == 0 and solo_score == 0:
        return None

    if solo_score > org_score:
        return "Solo"
    elif org_score > solo_score:
        return "Org"
    else:
        # Tie-breaker: if personal indicators exist, prefer Solo, otherwise Org
        return "Solo" if (has_personal_linkedin or has_possessive_person) else "Org"


def infer_fields(primary_text: str = "", body_text: str = "") -> list[str]:
    """Infer creative fields from text. Prioritizes primary text and caps at top 4."""
    primary = _sanitize_for_org_detection(primary_text or "").lower()
    body = _sanitize_for_org_detection(body_text or "").lower()
    scored_fields: dict[str, int] = {}

    for pattern, label in FIELD_KEYWORDS.items():
        score = 0
        if re.search(pattern, primary, re.IGNORECASE):
            score += 3
        elif body:
            matches = len(re.findall(pattern, body, re.IGNORECASE))
            if matches >= 2:
                score += 2
            elif matches == 1:
                score += 1

        if score >= 2:
            scored_fields[label] = max(scored_fields.get(label, 0), score)

    # Sort descending by score, capped at top 4
    sorted_fields = sorted(scored_fields.items(), key=lambda x: x[1], reverse=True)
    return [f[0] for f in sorted_fields[:4]]


def infer_tags(
    primary_text: str = "",
    available_tags: dict[str, str] | None = None,
    body_text: str = "",
) -> list[str]:
    """
    Infer creative tags from text with strict relevance scoring.
    Requires contextual evidence for ambiguous words (e.g. Interactions, Contents)
    and caps at top 3 to prevent noise.
    """
    primary = (primary_text or "").lower()
    body = (body_text or "").lower()
    combined = (primary + " " + body).lower()
    scored_tags: dict[str, int] = {}

    # Static keywords with disambiguation
    for pattern, label in TAG_KEYWORDS.items():
        # Specific guards for ambiguous tags
        if label == "Interactions":
            # Guard: Must refer to creative interaction design / digital interactive work,
            # NOT CLI interactive prompts, interactive directory browsing, or API utilities.
            has_interaction_design = bool(
                re.search(r"\b(?:interaction\s+design|interactive\s+experiences?|interactive\s+installations?|creative\s+technolog(?:y|ist))\b", combined)
                or (re.search(r"\b(?:interactive|interaction)\b", primary) and re.search(r"\b(?:design|creative|art|portfolio|studio|agency)\b", primary))
            )
            if not has_interaction_design:
                continue

        elif label == "Contents":
            # Guard: Must refer to content creation/strategy/media, NOT "skip to content" or file content
            is_generic_content = bool(re.search(r"\b(?:skip to content|table of contents|file contents?|read the content)\b", combined))
            has_media_context = bool(re.search(r"\b(?:content creation|content strategy|editorial|media|copy)\b", combined))
            if is_generic_content and not has_media_context:
                continue

        elif label == "UI/UX":
            # Guard: Only infer UI/UX if it is about product design or design services,
            # not random technical settings mentioning "in the UI"
            has_uiux_design = bool(
                re.search(r"\b(?:ui/ux|ux/ui|ui\s+designer|ux\s+designer|product\s+design|user\s+experience)\b", combined)
                or (re.search(r"\b(?:ui|ux)\b", primary) and re.search(r"\b(?:design|portfolio|studio|agency)\b", primary))
            )
            if not has_uiux_design:
                continue

        score = 0
        if re.search(pattern, primary, re.IGNORECASE):
            score += 3
        elif body:
            matches = len(re.findall(pattern, body, re.IGNORECASE))
            if matches >= 2:
                score += 2
            elif matches == 1:
                score += 1

        if score >= 2:
            scored_tags[label] = max(scored_tags.get(label, 0), score)

    # Dynamic tags from DB
    if available_tags:
        for tag_name in available_tags.keys():
            if len(tag_name) < 3 and tag_name.upper() not in ("AI", "3D", "UI", "UX"):
                continue
            tag_lower = tag_name.lower().strip()
            if tag_lower in ("other", "changed", "closed", "not loading", "403", "503"):
                continue

            pattern = r"\b" + re.escape(tag_name) + r"\b"
            score = 0
            if re.search(pattern, primary, re.IGNORECASE):
                score += 3
            elif body:
                matches = len(re.findall(pattern, body, re.IGNORECASE))
                if matches >= 5:
                    score += 3
                elif matches >= 3:
                    score += 2
                elif matches >= 1:
                    score += 1

            if score >= 3:
                scored_tags[tag_name] = max(scored_tags.get(tag_name, 0), score)

    sorted_tags = sorted(scored_tags.items(), key=lambda x: x[1], reverse=True)
    return [t[0] for t in sorted_tags[:3]]
