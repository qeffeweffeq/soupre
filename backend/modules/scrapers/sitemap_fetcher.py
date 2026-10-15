"""
sitemap_fetcher.py — auto-discover and parse a website's sitemap.
"""
import os
import re
from dataclasses import dataclass
from typing import Optional
import requests
from urllib.parse import urlparse, urljoin
from lxml import etree
from bs4 import BeautifulSoup
from modules.config import logger, DOWNLOADS_DIR

@dataclass
class SitemapEntry:
    url: str
    lastmod: Optional[str] = None
    hreflang: Optional[str] = None
    content_type: Optional[str] = None

    def __str__(self):
        return self.url


def determine_content_type(url: str, sitemap_hint: Optional[str] = None) -> str:
    parsed = urlparse(url)
    path = parsed.path.lower().strip("/")
    segments = path.split("/")

    # Strip language prefix if present (it, en, de, fr, etc.)
    if segments and re.match(r"^[a-z]{2}(-[a-z]{2})?$", segments[0]):
        segments = segments[1:]

    if not segments or segments == [""]:
        return "pages"

    first_seg = segments[0]

    # Canonical content type mappings
    if first_seg in ("category", "categories", "categoria", "categorie"):
        return "categories"
    if first_seg in ("tag", "tags"):
        return "tags"
    if first_seg in ("blog", "post", "posts", "articoli", "artikel", "actualites"):
        return "blog"
    if first_seg in ("experiences", "esperienze", "erlebnisse", "erlebnis", "activities", "attivita"):
        return "experiences"
    if first_seg in ("news", "notizie", "neuigkeiten"):
        return "news"
    if first_seg in ("events", "eventi", "evenements", "veranstaltungen"):
        return "events"
    if first_seg in ("products", "prodotti", "produkte", "shop", "negozio"):
        return "products"
    if first_seg in ("rooms", "camere", "zimmer", "chambres"):
        return "rooms"
    if first_seg in ("services", "servizi", "leistungen"):
        return "services"

    # Fallback to child sitemap name hint if available
    if sitemap_hint:
        hint = sitemap_hint.lower()
        if any(w in hint for w in ("category", "categories", "categoria", "categorie")):
            return "categories"
        if any(w in hint for w in ("tag", "tags")):
            return "tags"
        if "post" in hint:
            return "blog"
        if "page" in hint:
            return "pages"
        clean_hint = re.sub(r"[-_]sitemap(\d+)?(\.xml)?", "", hint)
        if clean_hint:
            return clean_hint

    # Multi-segment fallback (first directory is the category/type)
    if len(segments) > 1 and first_seg:
        return re.sub(r"[^a-zA-Z0-9_\-]", "", first_seg).lower()

    return "pages"

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
        html, _, cookies = await fetch_url_with_browser(url, take_screenshot=False)
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


def _extract_sitemap_entries(content: bytes, sitemap_hint: Optional[str] = None) -> tuple[bool, list[SitemapEntry]]:
    """
    Extract (is_index, entries) from content (XML or HTML).
    Handles:
    - Standard XML sitemaps (<url><loc>...</loc><lastmod>...</lastmod><xhtml:link.../></url>)
    - Standard XML sitemap index (<sitemap><loc>...</loc></sitemap>)
    - HTML-rendered sitemaps (e.g. Yoast SEO with XSL stylesheet) with <a href="...">
    - Raw regex <loc> fallback
    """
    # 1. Attempt standard XML parsing
    try:
        root = etree.fromstring(content)
        sitemap_nodes = root.xpath("//*[local-name()='sitemap']")
        if sitemap_nodes:
            sitemaps: list[SitemapEntry] = []
            for sn in sitemap_nodes:
                locs = sn.xpath("*[local-name()='loc']/text()")
                lastmods = sn.xpath("*[local-name()='lastmod']/text()")
                if locs and locs[0].strip():
                    sitemaps.append(SitemapEntry(
                        url=locs[0].strip(),
                        lastmod=lastmods[0].strip() if lastmods else None
                    ))
            if sitemaps:
                return True, sitemaps

        url_nodes = root.xpath("//*[local-name()='url']")
        if url_nodes:
            page_entries: list[SitemapEntry] = []
            for un in url_nodes:
                locs = un.xpath("*[local-name()='loc']/text()")
                if not locs or not locs[0].strip():
                    continue
                url = locs[0].strip()
                lastmods = un.xpath("*[local-name()='lastmod']/text()")
                lastmod = lastmods[0].strip() if lastmods else None

                # Look for xhtml:link rel="alternate" hreflang="..."
                hreflang = None
                links = un.xpath("*[local-name()='link']")
                for link in links:
                    rel = link.get("rel", "")
                    hl = link.get("hreflang", "")
                    href = link.get("href", "")
                    if rel == "alternate" and hl:
                        clean_hl = hl.strip().lower()
                        if clean_hl != "x-default":
                            lang_code = clean_hl.split("-")[0].split("_")[0]
                            if href and href.strip() == url:
                                hreflang = lang_code
                                break
                            elif not hreflang:
                                hreflang = lang_code

                if not hreflang:
                    parsed_path = urlparse(url).path
                    match = re.match(r"^/([a-z]{2})(/|$)", parsed_path.lower())
                    if match:
                        hreflang = match.group(1)

                page_entries.append(SitemapEntry(
                    url=url,
                    lastmod=lastmod,
                    hreflang=hreflang,
                    content_type=determine_content_type(url, sitemap_hint)
                ))
            if page_entries:
                return False, page_entries

        all_locs = [u.strip() for u in root.xpath("//*[local-name()='loc']/text()") if u.strip()]
        if all_locs:
            is_index = "index" in root.tag.lower() or bool(sitemap_nodes)
            return is_index, [
                SitemapEntry(
                    url=u,
                    content_type=determine_content_type(u, sitemap_hint) if not is_index else None
                ) for u in all_locs
            ]
    except Exception:
        pass

    # 2. Table / HTML parsing (Yoast SEO / Rank Math)
    try:
        soup = BeautifulSoup(content, "html.parser")
        candidate_tables = soup.find_all("table", id="sitemap") or soup.find_all("table")
        for table in candidate_tables:
            headers = [th.get_text().strip().lower() for th in table.find_all("th")]
            tbody = table.find("tbody") or table
            rows = tbody.find_all("tr")
            if not rows:
                rows = [table]

            child_sitemaps: list[SitemapEntry] = []
            page_entries: list[SitemapEntry] = []
            is_sitemap_col = any("sitemap" in th for th in headers)

            for row in rows:
                a = row.find("a")
                if not a:
                    continue
                h = a.get("href")
                if not h or not isinstance(h, str):
                    continue
                h = h.strip()
                if not h or h.startswith("#") or h.startswith("javascript:"):
                    continue

                lastmod = None
                for td in row.find_all("td"):
                    txt = td.get_text().strip()
                    if re.match(r"^\d{4}-\d{2}-\d{2}", txt):
                        lastmod = txt
                        break

                if is_sitemap_col or (h.lower().endswith(".xml") and "sitemaps.org" not in h.lower() and "yoa.st" not in h.lower()):
                    child_sitemaps.append(SitemapEntry(url=h, lastmod=lastmod))
                elif not h.lower().endswith((".xml", ".xsl", ".css", ".js")):
                    parsed_path = urlparse(h).path
                    match = re.match(r"^/([a-z]{2})(/|$)", parsed_path.lower())
                    hreflang = match.group(1) if match else None
                    page_entries.append(SitemapEntry(
                        url=h,
                        lastmod=lastmod,
                        hreflang=hreflang,
                        content_type=determine_content_type(h, sitemap_hint)
                    ))

            if child_sitemaps:
                return True, child_sitemaps
            if page_entries:
                return False, page_entries
    except Exception as e:
        logger.debug(f"[Sitemap] Table HTML parsing failed: {e}")

    # 3. Check for raw <loc> tags using regex (handles XML served with HTML tags/entities)
    content_str = content.decode("utf-8", errors="ignore")
    loc_matches = [m.strip() for m in re.findall(r'<loc>\s*(https?://[^\s<]+)\s*</loc>', content_str, re.IGNORECASE) if m.strip()]
    if loc_matches:
        is_index = "<sitemap>" in content_str.lower() or "sitemapindex" in content_str.lower()
        return is_index, [
            SitemapEntry(
                url=u,
                content_type=determine_content_type(u, sitemap_hint) if not is_index else None
            ) for u in loc_matches
        ]

    return False, []


def _extract_sitemap_urls(content: bytes) -> tuple[bool, list[str]]:
    is_index, entries = _extract_sitemap_entries(content)
    return is_index, [e.url for e in entries]


def _locs(content: bytes) -> list[str]:
    _, urls = _extract_sitemap_urls(content)
    return urls


def _is_index(content: bytes) -> bool:
    is_index, _ = _extract_sitemap_entries(content)
    return is_index


def generate_sitemap_xml(entries: list[SitemapEntry]) -> str:
    """Generate a clean, standard XML sitemap from entries."""
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"',
        '        xmlns:xhtml="http://www.w3.org/1999/xhtml">'
    ]
    for e in entries:
        lines.append("  <url>")
        lines.append(f"    <loc>{e.url}</loc>")
        if e.lastmod:
            lines.append(f"    <lastmod>{e.lastmod}</lastmod>")
        if e.hreflang:
            lines.append(f'    <xhtml:link rel="alternate" hreflang="{e.hreflang}" href="{e.url}"/>')
        lines.append("  </url>")
    lines.append("</urlset>\n")
    return "\n".join(lines)


async def discover_sitemap_urls(
    base_url: str,
    job_dir: str,
    max_pages: int = 0,
    cookies: list[dict] | None = None
) -> list[SitemapEntry]:
    """
    Discover all page URLs from base_url's sitemap.
    Strategy: robots.txt Sitemap: directive → common paths → fail gracefully.
    Guaranteed to save valid sitemap XML containing all unique URLs to {job_dir}/sitemap.xml.
    Returns all discovered same-domain SitemapEntry objects, or [] if no sitemap found.
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
                direct_entries: list[SitemapEntry] = []
                is_index, extracted = _extract_sitemap_entries(direct_content, sitemap_hint=os.path.basename(base_url))
                if is_index and extracted:
                    for ce in extracted:
                        cu = urljoin(origin, ce.url)
                        child = await _fetch(cu, session)
                        if child:
                            _, child_entries = _extract_sitemap_entries(child, sitemap_hint=os.path.basename(cu))
                            for che in child_entries:
                                che.url = urljoin(origin, che.url)
                                che.content_type = determine_content_type(che.url, sitemap_hint=os.path.basename(cu))
                                direct_entries.append(che)
                elif not is_index and extracted:
                    for pe in extracted:
                        pe.url = urljoin(origin, pe.url)
                        direct_entries.append(pe)

                if direct_entries:
                    # Deduplicate, same-domain filter
                    seen: set[str] = set()
                    result: list[SitemapEntry] = []
                    for entry in direct_entries:
                        url = entry.url
                        parsed_url = urlparse(url)
                        ext = parsed_url.path.lower()
                        if ext.endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.mp4', '.avi', '.pdf', '.zip', '.xml', '.xsl')):
                            continue
                        if url not in seen and parsed_url.netloc == parsed.netloc:
                            seen.add(url)
                            result.append(entry)

                    # Save sitemap to disk
                    if job_dir:
                        os.makedirs(job_dir, exist_ok=True)
                        sitemap_dest = os.path.join(job_dir, "sitemap.xml")
                        if not is_index and direct_content.strip().startswith(b"<?xml") and b"<url" in direct_content:
                            with open(sitemap_dest, "wb") as f:
                                f.write(direct_content)
                        else:
                            with open(sitemap_dest, "w", encoding="utf-8") as f:
                                f.write(generate_sitemap_xml(result))
                        logger.info(f"[Sitemap] Saved direct sitemap.xml to {job_dir}")

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
    all_entries: list[SitemapEntry] = []
    used_is_index = False

    for sitemap_url in candidates:
        content = await _fetch(sitemap_url, session)
        if not content:
            continue
        try:
            is_index, extracted = _extract_sitemap_entries(content, sitemap_hint=os.path.basename(sitemap_url))
            if is_index and extracted:
                used_is_index = True
                # Sitemap index: expand child sitemaps
                for ce in extracted:
                    cu = urljoin(origin, ce.url)
                    child = await _fetch(cu, session)
                    if child:
                        _, child_entries = _extract_sitemap_entries(child, sitemap_hint=os.path.basename(cu))
                        for che in child_entries:
                            che.url = urljoin(origin, che.url)
                            che.content_type = determine_content_type(che.url, sitemap_hint=os.path.basename(cu))
                            all_entries.append(che)
                if all_entries:
                    raw_xml = content
                    sitemap_url_used = sitemap_url
                    break
            elif not is_index and extracted:
                for pe in extracted:
                    pe.url = urljoin(origin, pe.url)
                    all_entries.append(pe)
                if all_entries:
                    raw_xml = content
                    sitemap_url_used = sitemap_url
                    break
            else:
                logger.debug(f"[Sitemap] No URLs extracted from {sitemap_url}, trying next candidate")
        except Exception as e:
            logger.warning(f"[Sitemap] Parse error for {sitemap_url}: {e}")

    # Deduplicate, same-domain filter
    seen: set[str] = set()
    result: list[SitemapEntry] = []
    for entry in all_entries:
        url = entry.url
        parsed_url = urlparse(url)
        ext = parsed_url.path.lower()
        if ext.endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.mp4', '.avi', '.pdf', '.zip', '.xml', '.xsl')):
            continue
        if url not in seen and parsed_url.netloc == parsed.netloc:
            seen.add(url)
            result.append(entry)

    # Guaranteed sitemap.xml saved to job_dir
    if job_dir:
        os.makedirs(job_dir, exist_ok=True)
        sitemap_dest = os.path.join(job_dir, "sitemap.xml")
        if raw_xml and not used_is_index and raw_xml.strip().startswith(b"<?xml") and b"<url" in raw_xml:
            with open(sitemap_dest, "wb") as f:
                f.write(raw_xml)
        else:
            with open(sitemap_dest, "w", encoding="utf-8") as f:
                f.write(generate_sitemap_xml(result))
        logger.info(f"[Sitemap] Saved sitemap.xml to {job_dir}")

    logger.info(f"[Sitemap] Discovered {len(result)} URLs via {sitemap_url_used or 'none'} for {base_url}")
    return result
