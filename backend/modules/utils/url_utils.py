"""
modules/utils/url_utils.py
─────────────────────────────────────────────────────────────────────────────
URL normalization, domain extraction, parameter sanitization, and URL extraction utilities.
"""

import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

TRACKING_PARAMS: frozenset[str] = frozenset({
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "utm_reader",
    "utm_referrer",
    "utm_name",
    "fbclid",
    "gclid",
    "gbraid",
    "wbraid",
    "msclkid",
    "dclid",
    "yclid",
    "twclid",
    "ttclid",
    "ref",
    "ref_src",
    "ref_url",
    "source",
    "feature",
    "mc_cid",
    "mc_eid",
    "igshid",
    "_hsenc",
    "_hsmi",
})

URL_REGEX = re.compile(
    r'https?://[^\s<>\",;()]+'
    r'|(?:www\.)[^\s<>\",;()]+'
    r'|(?<![@\w])(?:[a-zA-Z0-9-]+\.)+(?:com|org|net|io|ai|co|app|dev|edu|gov|xyz|info|tech|me)(?:/[^\s<>\",;()]*)?',
    re.IGNORECASE,
)


def sanitize_url(raw_url: str) -> str:
    """
    Sanitize a URL by stripping marketing/tracking query parameters,
    stripping fragments, ensuring valid scheme, and stripping trailing slash.
    Preserves functional query parameters.
    """
    if not raw_url or not isinstance(raw_url, str):
        return ""
    url = raw_url.strip()
    if not url:
        return ""
    if not url.startswith("http://") and not url.startswith("https://"):
        if "://" in url:
            pass
        else:
            url = "https://" + url

    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/")

    # Strip marketing/tracking query params
    filtered_query = []
    if parsed.query:
        for k, v in parse_qsl(parsed.query, keep_blank_values=True):
            lower_k = k.lower()
            if lower_k in TRACKING_PARAMS or lower_k.startswith("utm_"):
                continue
            filtered_query.append((k, v))

    query_str = urlencode(filtered_query) if filtered_query else ""
    return urlunparse((scheme, netloc, path, "", query_str, ""))


def extract_urls_from_text(raw_text: str) -> list[str]:
    """
    Extract URLs from raw text (including links and bare domains),
    sanitizes each with sanitize_url, and deduplicates while preserving order.
    """
    if not raw_text or not isinstance(raw_text, str):
        return []
    seen = set()
    urls = []
    for match in URL_REGEX.finditer(raw_text):
        raw_found = match.group(0).rstrip(".,;:!?'\"")
        sanitized = sanitize_url(raw_found)
        if sanitized and sanitized not in seen:
            seen.add(sanitized)
            urls.append(sanitized)
    return urls


def normalize_url(url: str) -> str:
    """Normalize URL to ignore protocol, www, and trailing slashes."""
    if not url:
        return ""
    u = url.lower().strip()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\.", "", u)
    u = u.rstrip("/")
    return u


def normalize_target_url(url: str) -> str:
    """Normalize a target URL ensuring scheme, lowercase netloc, and stripped trailing slash."""
    if not url:
        return ""
    url = url.strip()
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/")
    return f"{scheme}://{netloc}{path}"


def get_domain_from_url(url: str, replace_dots: bool = False) -> str:
    """Extract hostname/netloc from a URL, optionally replacing dots with underscores."""
    if not url:
        return ""
    u = url.strip()
    if "://" not in u:
        u = "https://" + u
    parsed = urlparse(u)
    domain = (parsed.hostname or parsed.netloc or "").lower()
    if replace_dots:
        return domain.replace(".", "_")
    return domain


def analyze_url_parameters(raw_url: str) -> dict:
    """
    Analyzes URL query parameters:
    - raw_url: original string
    - clean_url: sanitized URL without marketing/tracking params and fragments
    - stripped_params: list of parameter strings removed (e.g. ['utm_source=spring', 'fbclid=...'])
    - retained_params: list of parameter strings kept (e.g. ['variant=42'])
    - has_stripped_params: bool
    """
    if not raw_url or not isinstance(raw_url, str):
        return {
            "raw_url": "",
            "clean_url": "",
            "stripped_params": [],
            "retained_params": [],
            "has_stripped_params": False,
        }
    raw = raw_url.strip()
    clean = sanitize_url(raw)

    url_for_parsing = raw
    if not url_for_parsing.startswith("http://") and not url_for_parsing.startswith("https://"):
        if "://" not in url_for_parsing:
            url_for_parsing = "https://" + url_for_parsing

    parsed_raw = urlparse(url_for_parsing)
    parsed_clean = urlparse(clean)

    raw_params = parse_qsl(parsed_raw.query, keep_blank_values=True)
    clean_params = parse_qsl(parsed_clean.query, keep_blank_values=True)

    clean_copy = list(clean_params)
    stripped = []
    for k, v in raw_params:
        if (k, v) in clean_copy:
            clean_copy.remove((k, v))
        else:
            stripped.append(f"{k}={v}" if v else k)

    retained = [f"{k}={v}" if v else k for (k, v) in clean_params]

    return {
        "raw_url": raw,
        "clean_url": clean,
        "stripped_params": stripped,
        "retained_params": retained,
        "has_stripped_params": len(stripped) > 0,
    }


def extract_and_analyze_urls_from_text(raw_text: str) -> list[dict]:
    """
    Finds URLs in raw text and returns detailed parameter analysis for each unique URL.
    """
    if not raw_text or not isinstance(raw_text, str):
        return []
    seen = set()
    results = []
    for match in URL_REGEX.finditer(raw_text):
        raw_found = match.group(0).rstrip(".,;:!?'\"")
        analysis = analyze_url_parameters(raw_found)
        clean = analysis["clean_url"]
        if clean and clean not in seen:
            seen.add(clean)
            results.append(analysis)
    return results

