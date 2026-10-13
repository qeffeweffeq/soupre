"""
sitemap_fetcher.py — auto-discover and parse a website's sitemap.
"""
import os
import re
import requests
from urllib.parse import urlparse, urljoin
from lxml import etree
from bs4 import BeautifulSoup
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
    """Fetch a URL and return bytes if the response looks like XML or an HTML sitemap."""
    try:
        r = session.get(
            url,
            timeout=15,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/121.0.0.0 Safari/537.36",
                "Accept": "application/xml,text/xml,text/html,*/*;q=0.8",
            },
            allow_redirects=True,
        )
        if r.status_code == 202:
            logger.debug(f"[Sitemap] Skipping {url} — status 202")
            return None
        r.raise_for_status()
        content = r.content
        lower_content = content[:10000].lower()
        is_robots = url.endswith("robots.txt")
        if (b"sgcaptcha" in lower_content or
            b"checking the site connection" in lower_content or
            b"just a moment" in lower_content or
            (len(content) < 500 and not is_robots)):
            logger.debug(f"[Sitemap] Skipping {url} — bot challenge or too short ({len(content)} bytes)")
            return None
        content_type = r.headers.get("Content-Type", "").lower()
        if "html" in content_type:
            if (b"<loc>" not in lower_content and
                b"sitemap" not in lower_content and
                b".xml" not in lower_content):
                logger.debug(f"[Sitemap] Skipping {url} — returned generic HTML (not sitemap)")
                return None
        return content
    except Exception as e:
        logger.warning(f"[Sitemap] Cannot fetch {url}: {e}")
        return None


async def _fetch(url: str, session: requests.Session) -> bytes | None:
    """Try fetching with requests first, fall back to Playwright browser if blocked."""
    content = _get(url, session)
    if content:
        return content
    try:
        from modules.scrapers.fetcher import fetch_url_with_browser
        logger.info(f"[Sitemap] Requests blocked or empty for {url}, falling back to browser...")
        html, _, cookies = await fetch_url_with_browser(url)
        if cookies:
            for c in cookies:
                session.cookies.set(c["name"], c["value"], domain=c.get("domain", ""))
        if html:
            content_bytes = html.encode("utf-8")
            lower = content_bytes[:10000].lower()
            if (b"sgcaptcha" in lower or
                b"checking the site connection" in lower or
                b"just a moment" in lower):
                return None
            return content_bytes
    except Exception as e:
        logger.warning(f"[Sitemap] Browser fallback failed for {url}: {e}")
    return None


def _extract_sitemap_urls(content: bytes) -> tuple[bool, list[str]]:
    """
    Extract (is_index, urls) from content (XML or HTML).
    Handles:
    - Standard XML sitemaps (<url><loc>...</loc></url>)
    - Standard XML sitemap index (<sitemap><loc>...</loc></sitemap>)
    - HTML-rendered sitemaps (e.g. Yoast SEO with XSL stylesheet) with <a href="...">
    - Raw regex <loc> fallback
    """
    # 1. Attempt standard XML parsing
    try:
        root = etree.fromstring(content)
        sitemaps = [u.strip() for u in root.xpath("//*[local-name()='sitemap']/*[local-name()='loc']/text()") if u.strip()]
        if sitemaps:
            return True, sitemaps

        page_locs = [u.strip() for u in root.xpath("//*[local-name()='url']/*[local-name()='loc']/text()") if u.strip()]
        if page_locs:
            return False, page_locs

        all_locs = [u.strip() for u in root.xpath("//*[local-name()='loc']/text()") if u.strip()]
        if all_locs:
            is_index = "index" in root.tag.lower() or bool(root.xpath("//*[local-name()='sitemap']"))
            return is_index, all_locs
    except Exception:
        pass

    # 2. Table / HTML parsing (Yoast SEO / Rank Math)
    try:
        soup = BeautifulSoup(content, "html.parser")
        candidate_tables = soup.find_all("table", id="sitemap") or soup.find_all("table")
        for table in candidate_tables:
            headers = [th.get_text().strip().lower() for th in table.find_all("th")]
            tbody = table.find("tbody") or table
            links = []
            for a in tbody.find_all("a"):
                h = a.get("href")
                if h and isinstance(h, str):
                    h = h.strip()
                    if h and not h.startswith("#") and not h.startswith("javascript:"):
                        links.append(h)

            if any("sitemap" in th for th in headers):
                child_sitemaps = [
                    h for h in links
                    if h.lower().endswith(".xml") and "sitemaps.org" not in h.lower() and "yoa.st" not in h.lower()
                ]
                if child_sitemaps:
                    return True, child_sitemaps

            if any("url" in th for th in headers):
                page_urls = [
                    h for h in links
                    if not h.lower().endswith((".xml", ".xsl", ".css", ".js"))
                ]
                if page_urls:
                    return False, page_urls
    except Exception as e:
        logger.debug(f"[Sitemap] Table HTML parsing failed: {e}")

    # 3. Check for raw <loc> tags using regex (handles XML served with HTML tags/entities)
    content_str = content.decode("utf-8", errors="ignore")
    loc_matches = [m.strip() for m in re.findall(r'<loc>\s*(https?://[^\s<]+)\s*</loc>', content_str, re.IGNORECASE) if m.strip()]
    if loc_matches:
        is_index = "<sitemap>" in content_str.lower() or "sitemapindex" in content_str.lower()
        return is_index, loc_matches

    return False, []


def _locs(content: bytes) -> list[str]:
    _, urls = _extract_sitemap_urls(content)
    return urls


def _is_index(content: bytes) -> bool:
    is_index, _ = _extract_sitemap_urls(content)
    return is_index


async def discover_sitemap_urls(
    base_url: str,
    job_dir: str,
    max_pages: int = 50,
    cookies: list[dict] | None = None
) -> list[str]:
    """
    Discover all page URLs from base_url's sitemap.
    Strategy: robots.txt Sitemap: directive → common paths → fail gracefully.
    Saves the raw sitemap XML to {job_dir}/sitemap.xml.
    Returns up to max_pages same-domain URLs, or [] if no sitemap found.
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/121.0.0.0 Safari/537.36",
        "Accept": "application/xml,text/xml,text/html,*/*;q=0.8",
    })
    if cookies:
        for c in cookies:
            session.cookies.set(c["name"], c["value"], domain=c.get("domain", ""))

    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    # If the submitted URL itself looks like a sitemap, try it directly first
    path_lower = parsed.path.lower()
    if path_lower.endswith('.xml') or 'sitemap' in path_lower:
        logger.info(f"[Sitemap] URL looks like a direct sitemap, trying it first: {base_url}")
        direct_content = await _fetch(base_url, session)
        if direct_content:
            try:
                direct_urls: list[str] = []
                is_index, extracted = _extract_sitemap_urls(direct_content)
                if is_index and extracted:
                    for cu in extracted:
                        cu = urljoin(origin, cu)
                        child = await _fetch(cu, session)
                        if child:
                            _, child_urls = _extract_sitemap_urls(child)
                            direct_urls.extend([urljoin(origin, u) for u in child_urls])
                        if len(direct_urls) >= max_pages:
                            break
                elif not is_index and extracted:
                    direct_urls.extend([urljoin(origin, u) for u in extracted])

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
                        if ext.endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.mp4', '.avi', '.pdf', '.zip', '.xml', '.xsl')):
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
    robots = await _fetch(urljoin(origin, "/robots.txt"), session)
    if robots:
        for line in robots.decode("utf-8", errors="ignore").splitlines():
            if "sitemap:" in line.lower():
                match = re.search(r'sitemap:\s*(https?://[^\s<"\']+)', line, re.IGNORECASE)
                if match:
                    candidates.append(match.group(1).strip())
                elif ":" in line:
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
        content = await _fetch(sitemap_url, session)
        if not content:
            continue
        try:
            is_index, extracted = _extract_sitemap_urls(content)
            if is_index and extracted:
                # Sitemap index: expand child sitemaps
                for cu in extracted:
                    cu = urljoin(origin, cu)
                    child = await _fetch(cu, session)
                    if child:
                        _, child_urls = _extract_sitemap_urls(child)
                        all_urls.extend([urljoin(origin, u) for u in child_urls])
                    if len(all_urls) >= max_pages:
                        break
                if all_urls:
                    raw_xml = content
                    sitemap_url_used = sitemap_url
                    break
            elif not is_index and extracted:
                all_urls.extend([urljoin(origin, u) for u in extracted])
                if all_urls:
                    raw_xml = content
                    sitemap_url_used = sitemap_url
                    break
            else:
                logger.debug(f"[Sitemap] No URLs extracted from {sitemap_url}, trying next candidate")
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
        if ext.endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.mp4', '.avi', '.pdf', '.zip', '.xml', '.xsl')):
            continue
        if url not in seen and parsed_url.netloc == parsed.netloc:
            seen.add(url)
            result.append(url)
        if len(result) >= max_pages:
            break

    logger.info(f"[Sitemap] Discovered {len(result)} URLs via {sitemap_url_used or 'none'} for {base_url}")
    return result
