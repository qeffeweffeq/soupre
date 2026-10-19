"""
modules/utils/url_utils.py
─────────────────────────────────────────────────────────────────────────────
URL normalization and domain extraction utilities.
"""

import re
from urllib.parse import urlparse


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
