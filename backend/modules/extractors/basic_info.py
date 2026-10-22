"""
extractors/basic_info.py
─────────────────────────────────────────────────────────────────────────────
Extract basic metadata from a parsed BeautifulSoup page.
"""

import json
import logging
import re
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

_ABOUT_MAX_LEN = 2000


def extract_about(soup: BeautifulSoup) -> str | None:
    """Extract about / description text. Priority:
    1. <meta name="description">
    2. <meta property="og:description">
    3. JSON-LD description
    4. First <p> inside <main> or <article> with >= 20 chars
    5. Global fallback: first <p> or <h1>-<h5> with 80-1000 chars that isn't a cookie/privacy banner
    """
    try:
        # 1. meta description
        tag = soup.find("meta", attrs={"name": "description"})
        if tag and (tag.get("content") or "").strip():
            return tag["content"].strip()[:_ABOUT_MAX_LEN]

        # 2. og:description
        tag = soup.find("meta", property="og:description")
        if tag and (tag.get("content") or "").strip():
            return tag["content"].strip()[:_ABOUT_MAX_LEN]

        # 3. JSON-LD description
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
            except (json.JSONDecodeError, TypeError):
                continue
            items = []
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict):
                items = data.get("@graph", [data])
            for item in items:
                if isinstance(item, dict):
                    desc = item.get("description", "")
                    if desc and isinstance(desc, str) and desc.strip():
                        return desc.strip()[:_ABOUT_MAX_LEN]

        # 4. First <p> in <main> or <article> >= 20 chars
        for container_tag in ("main", "article"):
            container = soup.find(container_tag)
            if container:
                for p in container.find_all("p"):
                    text = p.get_text(strip=True)
                    if len(text) >= 20:
                        return text[:_ABOUT_MAX_LEN]

        # 5. Global fallback: first <p> or <h1>-<h5> with 80-1000 chars that isn't a cookie/privacy banner
        skip_words = re.compile(
            r'\b(cookie|privacy|newsletter|subscribe|javascript|browser|rights reserved|copyright|terms of service)\b',
            re.IGNORECASE
        )
        for tag in soup.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'h5']):
            text = tag.get_text(separator=" ", strip=True)
            if 80 <= len(text) <= 1000:
                if not skip_words.search(text):
                    return text[:_ABOUT_MAX_LEN]
    except Exception as exc:
        log.debug(f"extract_about error: {exc}")
    return None


def extract_email(soup: BeautifulSoup) -> str | None:
    """Extract email from mailto: links or regex on page text."""
    try:
        # 1. mailto: links
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if href.lower().startswith("mailto:"):
                email = href[7:].split("?")[0].strip()
                if email:
                    return email

        # 2. Regex on full text
        text = soup.get_text(separator=" ")
        match = re.search(r"[\w.+-]+@[\w-]+\.[a-z]{2,}", text, re.IGNORECASE)
        if match:
            return match.group(0)
    except Exception as exc:
        log.debug(f"extract_email error: {exc}")
    return None


def extract_phone(soup: BeautifulSoup) -> str | None:
    """Extract phone from tel: links."""
    try:
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if href.lower().startswith("tel:"):
                phone = href[4:].strip()
                if phone:
                    return phone
    except Exception as exc:
        log.debug(f"extract_phone error: {exc}")
    return None


def extract_instagram(soup: BeautifulSoup) -> str | None:
    """Extract Instagram profile URL (exclude posts, reels, explore, stories)."""
    _exclude = ("/p/", "/reel/", "/explore/", "/stories/")
    try:
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            href_lower = href.lower()
            if "instagram.com/" in href_lower:
                if not any(seg in href_lower for seg in _exclude):
                    return href
    except Exception as exc:
        log.debug(f"extract_instagram error: {exc}")
    return None


def extract_linkedin(soup: BeautifulSoup) -> str | None:
    """Extract LinkedIn profile or company URL."""
    try:
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            href_lower = href.lower()
            if "linkedin.com/in/" in href_lower or "linkedin.com/company/" in href_lower:
                return href
    except Exception as exc:
        log.debug(f"extract_linkedin error: {exc}")
    return None


def extract_founded(soup: BeautifulSoup) -> int | None:
    """Extract founding year from JSON-LD or text regex."""
    try:
        # 1. JSON-LD foundingDate
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
            except (json.JSONDecodeError, TypeError):
                continue
            items = []
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict):
                items = data.get("@graph", [data])
            for item in items:
                if isinstance(item, dict):
                    fd = item.get("foundingDate", "")
                    if fd:
                        m = re.search(r"\b(19|20)\d{2}\b", str(fd))
                        if m:
                            return int(m.group(0))

        # 2. Text regex
        text = soup.get_text(separator=" ")
        match = re.search(
            r"(?:founded|since|est\.?)\s*(?:in\s+)?(\d{4})",
            text,
            re.IGNORECASE,
        )
        if match:
            year = int(match.group(1))
            if 1900 <= year <= 2100:
                return year
    except Exception as exc:
        log.debug(f"extract_founded error: {exc}")
    return None


def extract_location(soup: BeautifulSoup) -> str | None:
    """Extract location from JSON-LD address or geo.placename meta tag."""
    try:
        # 1. JSON-LD address
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
            except (json.JSONDecodeError, TypeError):
                continue
            items = []
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict):
                items = data.get("@graph", [data])
            for item in items:
                if not isinstance(item, dict):
                    continue
                addr = item.get("address")
                if isinstance(addr, dict):
                    city = (addr.get("addressLocality") or "").strip()
                    country = (addr.get("addressCountry") or "").strip()
                    parts = [p for p in (city, country) if p]
                    if parts:
                        return ", ".join(parts)
                elif isinstance(addr, str) and addr.strip():
                    return addr.strip()

        # 2. geo.placename meta
        tag = soup.find("meta", attrs={"name": "geo.placename"})
        if tag and (tag.get("content") or "").strip():
            return tag["content"].strip()
    except Exception as exc:
        log.debug(f"extract_location error: {exc}")
    return None
