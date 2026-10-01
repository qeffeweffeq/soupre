"""
sitemap_fetcher.py — auto-discover and parse a website's sitemap.
"""
import os
import requests
from urllib.parse import urlparse, urljoin
from lxml import etree
from modules.config import logger

COMMON_PATHS = [
    "/sitemap.xml", "/sitemap_index.xml",
    "/sitemap-index.xml", "/sitemapindex.xml",
]


def _get(url: str) -> bytes | None:
    try:
        r = requests.get(url, timeout=15,
                         headers={"User-Agent": "SoupreBot/1.0"})
        r.raise_for_status()
        return r.content
    except Exception as e:
        logger.warning(f"[Sitemap] Cannot fetch {url}: {e}")
        return None


def _locs(content: bytes) -> list[str]:
    root = etree.fromstring(content)
    return [u.strip() for u in root.xpath("//*[local-name()='loc']/text()") if u.strip()]


def _is_index(content: bytes) -> bool:
    root = etree.fromstring(content)
    return bool(root.xpath("//*[local-name()='sitemap']"))


def discover_sitemap_urls(
    base_url: str,
    job_dir: str,
    max_pages: int = 50,
) -> list[str]:
    """
    Discover all page URLs from base_url's sitemap.
    Strategy: robots.txt Sitemap: directive → common paths → fail gracefully.
    Saves the raw sitemap XML to {job_dir}/sitemap.xml.
    Returns up to max_pages same-domain URLs, or [] if no sitemap found.
    """
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    # Step 1: robots.txt — look for Sitemap: directive
    candidates: list[str] = []
    robots = _get(urljoin(origin, "/robots.txt"))
    if robots:
        for line in robots.decode("utf-8", errors="ignore").splitlines():
            if line.lower().startswith("sitemap:"):
                candidates.append(line.split(":", 1)[1].strip())

    # Step 2: common fallback paths
    for p in COMMON_PATHS:
        url = urljoin(origin, p)
        if url not in candidates:
            candidates.append(url)

    raw_xml: bytes | None = None
    sitemap_url_used: str | None = None
    all_urls: list[str] = []

    for sitemap_url in candidates:
        content = _get(sitemap_url)
        if not content:
            continue
        try:
            if _is_index(content):
                # Sitemap index: expand child sitemaps
                child_sitemap_urls = _locs(content)
                for cu in child_sitemap_urls:
                    child = _get(cu)
                    if child:
                        all_urls.extend(_locs(child))
                    if len(all_urls) >= max_pages:
                        break
                raw_xml = content
            else:
                all_urls.extend(_locs(content))
                raw_xml = content

            sitemap_url_used = sitemap_url
            break
        except Exception as e:
            logger.warning(f"[Sitemap] Parse error for {sitemap_url}: {e}")

    if raw_xml and job_dir:
        os.makedirs(job_dir, exist_ok=True)
        with open(os.path.join(job_dir, "sitemap.xml"), "wb") as f:
            f.write(raw_xml)
        logger.info(f"[Sitemap] Saved sitemap.xml to {job_dir}")

    # Deduplicate, same-domain filter, cap at max_pages
    seen: set[str] = set()
    result: list[str] = []
    for url in all_urls:
        if url not in seen and urlparse(url).netloc == parsed.netloc:
            seen.add(url)
            result.append(url)
        if len(result) >= max_pages:
            break

    logger.info(f"[Sitemap] Discovered {len(result)} URLs via {sitemap_url_used or 'none'} for {base_url}")
    return result
