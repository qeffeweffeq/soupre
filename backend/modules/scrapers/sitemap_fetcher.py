"""
sitemap_fetcher.py — auto-discover and parse a website's sitemap.
"""
import os
import requests
from urllib.parse import urlparse, urljoin
from lxml import etree
from modules.config import logger

COMMON_PATHS = [
    # Standard
    "/sitemap.xml",
    "/sitemap_index.xml",
    "/sitemap-index.xml",
    "/sitemapindex.xml",
    # WordPress native (5.5+)
    "/wp-sitemap.xml",
    # WordPress Yoast SEO plugin
    "/page-sitemap.xml",
    "/post-sitemap.xml",
    "/category-sitemap.xml",
    "/tag-sitemap.xml",
    "/author-sitemap.xml",
    "/news-sitemap.xml",
    # WordPress Rank Math / All-in-One SEO
    "/sitemap_index.xml",
    "/sitemap-pages.xml",
    "/sitemap-posts.xml",
    # Other common patterns
    "/sitemap1.xml",
    "/sitemap-0.xml",
    "/feeds/sitemap.xml",
    "/sitemap/sitemap.xml",
    "/sitemaps/sitemap.xml",
]


def _get(url: str, session: requests.Session) -> bytes | None:
    """Fetch a URL and return bytes only if the response looks like XML."""
    try:
        r = session.get(
            url,
            timeout=15,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/121.0.0.0 Safari/537.36",
                "Accept": "application/xml,text/xml,*/*;q=0.8",
            },
            allow_redirects=True,
        )
        r.raise_for_status()
        content_type = r.headers.get("Content-Type", "").lower()
        if "html" in content_type:
            logger.debug(f"[Sitemap] Skipping {url} — returned HTML (not XML)")
            return None
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
    cookies: list[dict] | None = None
) -> list[str]:
    session = requests.Session()
    if cookies:
        for c in cookies:
            session.cookies.set(c["name"], c["value"], domain=c["domain"])
    """
    Discover all page URLs from base_url's sitemap.
    Strategy: robots.txt Sitemap: directive → common paths → fail gracefully.
    Saves the raw sitemap XML to {job_dir}/sitemap.xml.
    Returns up to max_pages same-domain URLs, or [] if no sitemap found.
    """
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    # If the submitted URL itself looks like a sitemap, try it directly first
    path_lower = parsed.path.lower()
    if path_lower.endswith('.xml') or 'sitemap' in path_lower:
        logger.info(f"[Sitemap] URL looks like a direct sitemap, trying it first: {base_url}")
        direct_content = _get(base_url, session)
        if direct_content:
            try:
                direct_urls: list[str] = []
                if _is_index(direct_content):
                    child_sitemap_urls = _locs(direct_content)
                    for cu in child_sitemap_urls:
                        child = _get(cu, session)
                        if child:
                            direct_urls.extend(_locs(child))
                        if len(direct_urls) >= max_pages:
                            break
                else:
                    direct_urls.extend(_locs(direct_content))

                if direct_urls:
                    # Save sitemap to disk
                    if job_dir:
                        os.makedirs(job_dir, exist_ok=True)
                        with open(os.path.join(job_dir, "sitemap.xml"), "wb") as f:
                            f.write(direct_content)
                        logger.info(f"[Sitemap] Saved direct sitemap.xml to {job_dir}")

                    # Deduplicate, same-domain filter, cap
                    seen: set[str] = set()
                    result: list[str] = []
                    for url in direct_urls:
                        parsed_url = urlparse(url)
                        ext = parsed_url.path.lower()
                        if ext.endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.mp4', '.avi', '.pdf', '.zip')):
                            continue
                        if url not in seen and parsed_url.netloc == parsed.netloc:
                            seen.add(url)
                            result.append(url)
                        if len(result) >= max_pages:
                            break

                    logger.info(f"[Sitemap] {len(result)} URLs from direct sitemap {base_url}")
                    return result
            except Exception as e:
                logger.warning(f"[Sitemap] Direct sitemap parse failed for {base_url}: {e}")
                # Fall through to normal discovery

    # Step 1: robots.txt — look for Sitemap: directive
    candidates: list[str] = []
    robots = _get(urljoin(origin, "/robots.txt"), session)
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
        content = _get(sitemap_url, session)
        if not content:
            continue
        try:
            if _is_index(content):
                # Sitemap index: expand child sitemaps
                child_sitemap_urls = _locs(content)
                for cu in child_sitemap_urls:
                    child = _get(cu, session)
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
        parsed_url = urlparse(url)
        ext = parsed_url.path.lower()
        if ext.endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.mp4', '.avi', '.pdf', '.zip')):
            continue
        if url not in seen and parsed_url.netloc == parsed.netloc:
            seen.add(url)
            result.append(url)
        if len(result) >= max_pages:
            break

    logger.info(f"[Sitemap] Discovered {len(result)} URLs via {sitemap_url_used or 'none'} for {base_url}")
    return result
