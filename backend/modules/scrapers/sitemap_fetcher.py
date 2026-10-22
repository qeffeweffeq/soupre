"""
sitemap_fetcher.py — auto-discover and parse a website's sitemap.
"""
import asyncio
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


GENERIC_SITEMAP_PATTERNS = re.compile(
    r"^(sitemap[-_]?(index|\d+)?|index|urllist|sitemap)(\.xml|\.txt)?(\.gz)?$",
    re.IGNORECASE
)


def determine_content_type(url: str, sitemap_hint: Optional[str] = None) -> str:
    parsed = urlparse(url)
    path = parsed.path.lower().strip("/")
    segments = [s for s in path.split("/") if s]

    # Strip language prefix if present (it, en, de, fr, etc.)
    if segments and re.match(r"^[a-z]{2}(-[a-z]{2})?$", segments[0]):
        segments = segments[1:]

    if not segments:
        return "pages"

    first_seg = segments[0]

    # Canonical content type mappings based on URL segment
    if first_seg:
        if first_seg in ("category", "categories", "categoria", "categorie"):
            return "categories"
        if first_seg in ("tag", "tags"):
            return "tags"
        if first_seg in ("blog", "post", "posts", "articoli", "artikel", "actualites", "news", "notizie", "neuigkeiten"):
            return "blog"
        if first_seg in ("experiences", "esperienze", "erlebnisse", "erlebnis", "activities", "attivita"):
            return "experiences"
        if first_seg in ("events", "eventi", "evenements", "veranstaltungen"):
            return "events"
        if first_seg in ("products", "prodotti", "produkte", "shop", "negozio"):
            return "products"
        if any(r in first_seg for r in ("room", "camera", "camere", "alloggi", "alloggio", "zimmer", "chambre", "suite", "villa", "accommodation")):
            return "rooms"
        if any(d in first_seg for d in ("dining", "ristorant", "restaurant", "food", "cuisine")):
            return "dining"
        if any(w in first_seg for w in ("spa", "wellness", "benessere")):
            return "wellness"
        if first_seg in ("hotel", "hotels", "albergo", "alberghi"):
            return "hotels"
        if first_seg in ("offers", "offerte", "angebote"):
            return "offers"
        if first_seg in ("services", "servizi", "leistungen"):
            return "services"

    # Fallback to child sitemap name hint if available and NOT a generic sitemap
    if sitemap_hint:
        hint = sitemap_hint.lower().strip()
        if not GENERIC_SITEMAP_PATTERNS.match(hint) and hint not in ("sitemap", "sitemap.xml", "sitemap_index.xml"):
            if any(w in hint for w in ("category", "categories", "categoria", "categorie")):
                return "categories"
            if any(w in hint for w in ("tag", "tags")):
                return "tags"
            if "page" in hint:
                return "pages"
            if "post" in hint:
                return "blog"
            clean_hint = re.sub(r"^sitemap[-_]?|[-_]sitemap(\d+)?(\.xml(\.gz)?)?$|\.xml(\.gz)?$", "", hint).strip("-_")
            clean_hint = re.sub(r"[^a-zA-Z0-9_\-]", "", clean_hint).lower()
            if clean_hint and "sitemap" not in clean_hint:
                return clean_hint

    # Multi-segment fallback (first directory is the category/type)
    if len(segments) > 1 and first_seg:
        candidate = re.sub(r"[^a-zA-Z0-9_\-]", "", first_seg).lower()
        if candidate and "sitemap" not in candidate:
            return candidate

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


def _strip_www(netloc: str) -> str:
    return (netloc or "").lower().split(":")[0].removeprefix("www.")


def _get_with_status(url: str, session: requests.Session) -> tuple[str, bytes | None, str | None]:
    """
    Fetch a URL and classify the result:
      - ("ok", content, final_url)
      - ("challenge", None, final_url) — WAF/bot challenge (403/429/503/202 or challenge HTML)
      - ("not_found", None, final_url) — 404/410 or generic non-sitemap HTML
      - ("unreachable", None, None)    — connection or read timeout / DNS failure
    """
    user_agents = [
        "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    ]
    last_unreachable = False
    for ua in user_agents:
        try:
            r = session.get(
                url,
                timeout=(6, 10),
                headers={
                    "User-Agent": ua,
                    "Accept": "application/xml,text/xml,application/xhtml+xml,text/html;q=0.9,*/*;q=0.8",
                },
                allow_redirects=True,
            )
            final_url = r.url
            if r.status_code in (404, 410, 400):
                logger.debug(f"[Sitemap] {url} returned HTTP {r.status_code}")
                return "not_found", None, final_url

            content = r.content or b""
            lower_content = content[:10000].lower()
            has_challenge_markers = (
                b"sgcaptcha" in lower_content
                or b"checking the site connection" in lower_content
                or b"checking your browser" in lower_content
                or b"confirm you are human" in lower_content
                or b"just a moment" in lower_content
                or b"cf-browser-verification" in lower_content
                or b"challenge-platform" in lower_content
            )

            if r.status_code in (202, 403, 429, 503) or has_challenge_markers:
                logger.debug(f"[Sitemap] Bot/WAF challenge detected on {url} (HTTP {r.status_code})")
                return "challenge", None, final_url

            if r.status_code >= 400:
                logger.debug(f"[Sitemap] {url} returned HTTP {r.status_code}")
                return "not_found", None, final_url

            is_robots = url.endswith("robots.txt")
            if len(content) < 200 and not is_robots:
                logger.debug(f"[Sitemap] Skipping {url} — response too short ({len(content)} bytes)")
                continue

            # Check if redirected away from a sitemap URL to the homepage or generic HTML page
            if r.history:
                final_path = urlparse(r.url).path.lower().rstrip("/")
                content_type = r.headers.get("Content-Type", "").lower()
                if (final_path in ("", "/") or "html" in content_type) and b"<loc>" not in lower_content and b"<sitemap>" not in lower_content:
                    logger.debug(f"[Sitemap] {url} redirected to non-sitemap URL {r.url}")
                    return "not_found", None, final_url

            content_type = r.headers.get("Content-Type", "").lower()
            if "html" in content_type and not is_robots:
                if (b"<loc>" not in lower_content and
                    b"sitemap" not in lower_content and
                    b".xml" not in lower_content):
                    logger.debug(f"[Sitemap] Skipping {url} — returned generic HTML (not sitemap)")
                    return "not_found", None, final_url

            return "ok", content, final_url
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            logger.warning(f"[Sitemap] Unreachable or timed out fetching {url} with UA {ua[:30]}: {e}")
            last_unreachable = True
            continue
        except Exception as e:
            logger.debug(f"[Sitemap] Cannot fetch {url} with UA {ua[:30]}: {e}")
            continue

    if last_unreachable:
        return "unreachable", None, None
    return "not_found", None, None


def _get(url: str, session: requests.Session) -> bytes | None:
    """Fetch a URL and return bytes if the response looks like XML or an HTML sitemap."""
    status, content, _ = _get_with_status(url, session)
    return content if status == "ok" else None


async def _fetch_with_status(url: str, session: requests.Session) -> tuple[str, bytes | None, str | None]:
    """
    Try fetching with requests in a worker thread first.
    ONLY fall back to the headless browser if a WAF/bot challenge was detected.
    Never launch a browser for 404s, generic HTML, or network timeouts.
    """
    status, content, final_url = await asyncio.to_thread(_get_with_status, url, session)
    if status == "ok" and content:
        return "ok", content, final_url
    if status != "challenge":
        return status, None, final_url

    try:
        from modules.scrapers.fetcher import fetch_url_with_browser
        logger.info(f"[Sitemap] Bot challenge detected on {url}, falling back to browser...")
        html, _, cookies = await fetch_url_with_browser(url, take_screenshot=False)
        if cookies:
            for c in cookies:
                session.cookies.set(c["name"], c["value"], domain=c.get("domain", ""))
        if html:
            content_bytes = html.encode("utf-8")
            lower = content_bytes[:10000].lower()
            if (b"sgcaptcha" in lower or
                b"checking the site connection" in lower or
                b"checking your browser" in lower or
                b"just a moment" in lower):
                return "challenge", None, final_url
            is_robots = url.endswith("robots.txt")
            if not is_robots and b"<loc>" not in lower and b"<sitemap>" not in lower and b"sitemap" not in lower and b".xml" not in lower:
                logger.debug(f"[Sitemap] Browser rendered non-sitemap HTML for {url}")
                return "not_found", None, final_url
            return "ok", content_bytes, final_url
    except Exception as e:
        logger.warning(f"[Sitemap] Browser fallback failed for {url}: {e}")
    return "not_found", None, final_url


async def _fetch(url: str, session: requests.Session) -> bytes | None:
    """Try fetching with requests first, fall back to browser only if blocked by challenge."""
    _, content, _ = await _fetch_with_status(url, session)
    return content


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
    allowed_domains: set[str] = {_strip_www(parsed.netloc)}

    def _filter_same_domain(entries_list: list[SitemapEntry]) -> list[SitemapEntry]:
        seen_urls: set[str] = set()
        filtered: list[SitemapEntry] = []
        for entry in entries_list:
            u = entry.url
            pu = urlparse(u)
            ext = pu.path.lower()
            if ext.endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.mp4', '.avi', '.pdf', '.zip', '.xml', '.xsl')):
                continue
            if u not in seen_urls and _strip_www(pu.netloc) in allowed_domains:
                seen_urls.add(u)
                filtered.append(entry)
        return filtered

    # If the submitted URL itself looks like a sitemap, try it directly first
    path_lower = parsed.path.lower()
    if path_lower.endswith('.xml') or 'sitemap' in path_lower:
        logger.info(f"[Sitemap] URL looks like a direct sitemap, trying it first: {base_url}")
        _, direct_content, direct_final = await _fetch_with_status(base_url, session)
        if direct_final:
            allowed_domains.add(_strip_www(urlparse(direct_final).netloc))
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

                result = _filter_same_domain(direct_entries)
                if result:
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

    consecutive_unreachable = 0

    # Step 1: robots.txt — look for Sitemap: directive
    same_domain_robots_candidates: list[str] = []
    other_robots_candidates: list[str] = []
    r_status, robots, r_final = await _fetch_with_status(urljoin(origin, "/robots.txt"), session)
    if r_status == "unreachable":
        consecutive_unreachable += 1
    if r_final:
        redirected_netloc = urlparse(r_final).netloc
        if redirected_netloc:
            allowed_domains.add(_strip_www(redirected_netloc))
            origin = f"{urlparse(r_final).scheme or parsed.scheme}://{redirected_netloc}"

    if robots:
        for line in robots.decode("utf-8", errors="ignore").splitlines():
            if "sitemap:" in line.lower():
                match = re.search(r'sitemap:\s*(https?://[^\s<"\']+)', line, re.IGNORECASE)
                cand = match.group(1).strip() if match else (line.split(":", 1)[1].strip() if ":" in line else "")
                if cand:
                    if _strip_www(urlparse(cand).netloc) in allowed_domains:
                        if cand not in same_domain_robots_candidates:
                            same_domain_robots_candidates.append(cand)
                    else:
                        if cand not in other_robots_candidates:
                            other_robots_candidates.append(cand)

    candidates: list[str] = list(same_domain_robots_candidates)

    # Step 2: common fallback paths on the target origin
    for p in COMMON_PATHS:
        url = urljoin(origin, p)
        if url not in candidates:
            candidates.append(url)

    # If submitted URL was sitemap.xml, ensure sitemap_index.xml is also a top candidate
    if path_lower.endswith("sitemap.xml"):
        idx_candidate = urljoin(origin, "/sitemap_index.xml")
        if idx_candidate not in candidates:
            candidates.append(idx_candidate)

    # Priority sorting: candidates on the EXACT target domain come first, with sitemap indexes prioritized
    def candidate_priority(u: str) -> tuple[int, int]:
        u_parsed = urlparse(u)
        u_netloc = u_parsed.netloc.lower()
        target_netloc = parsed.netloc.lower()
        if u_netloc == target_netloc:
            netloc_rank = 0
        elif u_netloc.endswith(target_netloc) or target_netloc.endswith(u_netloc):
            netloc_rank = 1
        elif u_netloc.split(".")[-2:] == target_netloc.split(".")[-2:]:
            netloc_rank = 2
        else:
            netloc_rank = 3
        path_p = u_parsed.path.lower()
        path_rank = 0 if "index" in path_p else 1
        return (netloc_rank, path_rank)

    candidates.sort(key=candidate_priority)

    raw_xml: bytes | None = None
    sitemap_url_used: str | None = None
    result: list[SitemapEntry] = []
    used_is_index = False

    for sitemap_url in candidates:
        status, content, final_u = await _fetch_with_status(sitemap_url, session)
        if status == "unreachable":
            consecutive_unreachable += 1
            if consecutive_unreachable >= 2:
                logger.error(f"[Sitemap] Host {origin} is unreachable (connection timed out). Aborting sitemap discovery.")
                break
            continue
        consecutive_unreachable = 0

        if final_u:
            allowed_domains.add(_strip_www(urlparse(final_u).netloc))

        if not content:
            continue
        try:
            candidate_entries: list[SitemapEntry] = []
            is_index, extracted = _extract_sitemap_entries(content, sitemap_hint=os.path.basename(sitemap_url))
            if is_index and extracted:
                # Sitemap index: expand child sitemaps
                for ce in extracted:
                    cu = urljoin(origin, ce.url)
                    if _strip_www(urlparse(cu).netloc) not in allowed_domains:
                        continue
                    child = await _fetch(cu, session)
                    if child:
                        _, child_entries = _extract_sitemap_entries(child, sitemap_hint=os.path.basename(cu))
                        for che in child_entries:
                            che.url = urljoin(origin, che.url)
                            che.content_type = determine_content_type(che.url, sitemap_hint=os.path.basename(cu))
                            candidate_entries.append(che)
            elif not is_index and extracted:
                for pe in extracted:
                    pe.url = urljoin(origin, pe.url)
                    candidate_entries.append(pe)

            filtered = _filter_same_domain(candidate_entries)
            if filtered:
                used_is_index = is_index
                result = filtered
                raw_xml = content
                sitemap_url_used = sitemap_url
                break
            else:
                logger.debug(f"[Sitemap] Candidate {sitemap_url} had no URLs matching {parsed.netloc}, trying next...")
        except Exception as e:
            logger.warning(f"[Sitemap] Parse error for {sitemap_url}: {e}")

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
