"""
extractors/icons_url.py
─────────────────────────────────────────────────────────────────────────────
Find the best high-quality icon URL from a webpage's ``<head>``.

Resolution priority (all attempted on already-fetched HTML):
  1. Verified candidates sorted by declared or filename resolution descending
  2. In case of equal sizes, prefer android-icon > apple-touch-icon > icon > shortcut icon
  3. Google Favicon API fallback (sz=128) if all candidates fail or none exist
"""

import logging
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
import requests

log = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_sizes(sizes_attr: str | None, href: str | None = None) -> int:
    """Parse a ``sizes`` attribute or fallback to ``href`` dimensions into a single integer.

    Handles:
    - ``"180x180"``         → 180
    - ``"16x16 32x32"``     → 32  (space-separated, take max)
    - ``"any"``             → 0   (vector / unknown)
    - ``None`` / ``""``     → 0 (or inferred from href)
    - Dimension in filename (e.g. ``icon-512.png``) → 512
    """
    best = 0
    if sizes_attr:
        for token in sizes_attr.strip().lower().split():
            if token == "any":
                continue
            match = re.match(r"(\d+)x(\d+)", token)
            if match:
                dim = max(int(match.group(1)), int(match.group(2)))
                best = max(best, dim)

    if best > 0:
        return best

    if href and isinstance(href, str):
        clean_href = href.split("?")[0].split("#")[0]
        match = re.search(
            r"[-_](\d{2,4})(?:x(\d{2,4}))?\.(?:png|jpe?g|webp|ico|svg)",
            clean_href,
            re.IGNORECASE,
        )
        if match:
            dim1 = int(match.group(1))
            dim2 = int(match.group(2)) if match.group(2) else dim1
            dim = max(dim1, dim2)
            if 16 <= dim <= 1024:
                return dim

    return 0


def _best_by_sizes(tags: list, base_url: str) -> str | None:
    """Pick the tag with the largest ``sizes`` from a list of <link> tags.
    Falls back to the first tag if none declare sizes. Skips data: URIs."""
    best: tuple[int, str] | None = None
    for tag in tags:
        href = (tag.get("href") or "").strip()
        if not href or href.startswith("data:"):
            continue
        size = parse_sizes(tag.get("sizes"), href)
        if best is None or size > best[0]:
            best = (size, href)
    if best:
        return urljoin(base_url, best[1])
    return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def extract_best_icon(soup: BeautifulSoup | None, base_url: str | None) -> str | None:
    """Return the best high-quality icon URL from the page's ``<head>``.

    Selection priority (highest first):
      1. Verified candidates sorted by resolution descending (e.g. 512, 192, 180, ...).
      2. If sizes are equal, prefer android-icon > apple-touch-icon > icon > shortcut icon.
      3. Google Favicon API fallback (sz=128) if all page candidates fail or none found.

    Candidate reachability is verified with a fast HTTP HEAD request (timeout=2.5s).
    Candidates returning 404, 403, 500, or failing with connection/timeout errors are skipped.

    ``data:`` URIs and empty hrefs are always skipped.
    Never crashes — returns None if no viable icon can be found or constructed.
    """
    if not base_url:
        return None

    try:
        if soup:
            candidates: list[dict] = []
            link_tags = soup.find_all("link", href=True)
            for idx, tag in enumerate(link_tags):
                href = (tag.get("href") or "").strip()
                if not href or href.startswith("data:") or href.startswith("javascript:"):
                    continue

                rel_attr = tag.get("rel")
                if not rel_attr:
                    continue
                if isinstance(rel_attr, list):
                    rel_tokens = {r.lower() for r in rel_attr}
                else:
                    rel_tokens = {r.lower() for r in rel_attr.split()}

                if "mask-icon" in rel_tokens:
                    continue

                is_android = "android-icon" in rel_tokens
                is_apple = bool(rel_tokens & {"apple-touch-icon", "apple-touch-icon-precomposed"})
                is_icon = bool(rel_tokens & {"icon", "shortcut icon"}) or ("icon" in rel_tokens)

                if not (is_android or is_apple or is_icon):
                    continue

                size = parse_sizes(tag.get("sizes"), href)
                if size == 0:
                    if is_android:
                        size = 192
                    elif is_apple:
                        size = 180

                if is_android:
                    priority = 4
                elif is_apple:
                    priority = 3
                elif is_icon and "shortcut" not in rel_tokens:
                    priority = 2
                else:
                    priority = 1

                full_url = urljoin(base_url, href)
                candidates.append({
                    "url": full_url,
                    "size": size,
                    "priority": priority,
                    "idx": idx,
                })

            # Sort by size descending, then priority descending, then document order ascending
            candidates.sort(key=lambda c: (c["size"], c["priority"], -c["idx"]), reverse=True)

            seen_urls = set()
            for cand in candidates:
                cand_url = cand["url"]
                if cand_url in seen_urls:
                    continue
                seen_urls.add(cand_url)

                try:
                    resp = requests.head(
                        cand_url,
                        timeout=2.5,
                        headers={"User-Agent": DEFAULT_USER_AGENT},
                        allow_redirects=True,
                    )
                    if resp.status_code == 200:
                        return cand_url
                    log.debug("Icon candidate %s returned status %d", cand_url, resp.status_code)
                except Exception as exc:
                    log.debug("Icon candidate %s verification failed: %s", cand_url, exc)

        # Fall back to Google Favicon API if all page candidates fail
        parsed = urlparse(base_url)
        domain = parsed.hostname or parsed.netloc or parsed.path.split("/")[0]
        if domain:
            return f"https://www.google.com/s2/favicons?domain={domain}&sz=128"

        return None

    except Exception as exc:
        log.warning("extract_best_icon encountered error for %s: %s", base_url, exc)
        try:
            parsed = urlparse(base_url)
            domain = parsed.hostname or parsed.netloc or parsed.path.split("/")[0]
            if domain:
                return f"https://www.google.com/s2/favicons?domain={domain}&sz=128"
        except Exception:
            pass
        return None
