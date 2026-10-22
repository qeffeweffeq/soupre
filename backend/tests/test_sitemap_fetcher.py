import pytest
from modules.scrapers.sitemap_fetcher import determine_content_type, GENERIC_SITEMAP_PATTERNS


def test_generic_sitemap_regex():
    assert GENERIC_SITEMAP_PATTERNS.match("sitemap.xml")
    assert GENERIC_SITEMAP_PATTERNS.match("sitemap_index.xml")
    assert GENERIC_SITEMAP_PATTERNS.match("sitemap-index.xml")
    assert GENERIC_SITEMAP_PATTERNS.match("sitemap1.xml")
    assert GENERIC_SITEMAP_PATTERNS.match("sitemap.xml.gz")
    assert GENERIC_SITEMAP_PATTERNS.match("urllist.txt")
    assert not GENERIC_SITEMAP_PATTERNS.match("post-sitemap.xml")
    assert not GENERIC_SITEMAP_PATTERNS.match("page-sitemap.xml")
    assert not GENERIC_SITEMAP_PATTERNS.match("category-sitemap.xml")


def test_determine_content_type_generic_sitemaps():
    # Should never return "sitemap.xml" or anything containing sitemap
    assert determine_content_type("https://www.castelfalfi.com/en/", "sitemap.xml") == "pages"
    assert determine_content_type("https://www.castelfalfi.com/en/careers", "sitemap.xml") == "pages"
    assert determine_content_type("https://www.pellicanohotels.com/it/la-nostra-storia.html", "sitemap.xml") == "pages"
    assert determine_content_type("https://www.sanluis-hotel.com/de/kontakt", "sitemap.xml") == "pages"
    assert determine_content_type("https://example.com/about", "sitemap_index.xml") == "pages"


def test_determine_content_type_child_sitemaps():
    assert determine_content_type("https://hotelmarinetta.it/my-post", "post-sitemap.xml") == "blog"
    assert determine_content_type("https://hotelmarinetta.it/my-page", "page-sitemap.xml") == "pages"
    assert determine_content_type("https://hotelmarinetta.it/my-cat", "category-sitemap.xml") == "categories"
    assert determine_content_type("https://hotelmarinetta.it/my-tag", "tag-sitemap.xml") == "tags"
    assert determine_content_type("https://goldenresidenceclub.it/block-1", "custom_blocks-sitemap.xml") == "custom_blocks"


def test_determine_content_type_url_segments():
    # Canonical categories from URL
    assert determine_content_type("https://www.castelfalfi.com/en/rooms-suites") == "rooms"
    assert determine_content_type("https://www.castelfalfi.com/it/villas-penthouse") == "rooms"
    assert determine_content_type("https://www.castelfalfi.com/en/dining/la-rocca") == "dining"
    assert determine_content_type("https://www.castelfalfi.com/it/spa/wellness") == "wellness"
    assert determine_content_type("https://www.castelfalfi.com/en/experiences/wine-tour") == "experiences"
    assert determine_content_type("https://www.pellicanohotels.com/en/hotels/hotel-il-pellicano") == "hotels"
    assert determine_content_type("https://www.pellicanohotels.com/it/offers/summer-escape") == "offers"


def test_determine_content_type_guarantee_no_sitemap_output():
    # Exhaustive check: sitemap or .xml must never be in returned content_type
    test_urls = [
        ("https://www.castelfalfi.com/", "sitemap.xml"),
        ("https://www.castelfalfi.com/sitemap.xml", "sitemap.xml"),
        ("https://www.castelfalfi.com/sitemap-index.xml", "sitemap_index.xml"),
        ("https://www.castelfalfi.com/en/sitemap.xml", "sitemap.xml"),
    ]
    for url, hint in test_urls:
        ct = determine_content_type(url, hint)
        assert "sitemap" not in ct.lower(), f"Failed for {url} with hint {hint}: returned {ct}"
        assert not ct.lower().endswith(".xml"), f"Failed for {url} with hint {hint}: returned {ct}"


@pytest.mark.asyncio
async def test_fetch_does_not_launch_browser_on_404_or_timeout(monkeypatch):
    import requests
    from modules.scrapers import sitemap_fetcher

    browser_calls = []

    async def fake_browser(url, take_screenshot=False):
        browser_calls.append(url)
        return "<urlset></urlset>", "", []

    monkeypatch.setattr("modules.scrapers.fetcher.fetch_url_with_browser", fake_browser)

    # 1. 404 Not Found -> should NOT launch browser
    monkeypatch.setattr(sitemap_fetcher, "_get_with_status", lambda u, s: ("not_found", None, u))
    status, content, _ = await sitemap_fetcher._fetch_with_status("https://example.com/sitemap.xml", requests.Session())
    assert status == "not_found"
    assert content is None
    assert browser_calls == []

    # 2. Unreachable timeout -> should NOT launch browser
    monkeypatch.setattr(sitemap_fetcher, "_get_with_status", lambda u, s: ("unreachable", None, None))
    status, content, _ = await sitemap_fetcher._fetch_with_status("https://example.com/sitemap.xml", requests.Session())
    assert status == "unreachable"
    assert content is None
    assert browser_calls == []

    # 3. WAF challenge -> SHOULD launch browser
    monkeypatch.setattr(sitemap_fetcher, "_get_with_status", lambda u, s: ("challenge", None, u))
    status, content, _ = await sitemap_fetcher._fetch_with_status("https://example.com/sitemap.xml", requests.Session())
    assert len(browser_calls) == 1


@pytest.mark.asyncio
async def test_discover_sitemap_urls_multi_domain_robots_and_unreachable_abort(monkeypatch, tmp_path):
    from modules.scrapers import sitemap_fetcher

    # Case A: Unreachable host aborts after 2 consecutive timeouts instead of trying all 19 paths
    calls = []

    async def fake_unreachable(url, session):
        calls.append(url)
        return "unreachable", None, None

    monkeypatch.setattr(sitemap_fetcher, "_fetch_with_status", fake_unreachable)
    entries = await sitemap_fetcher.discover_sitemap_urls("https://dead.example.com/", str(tmp_path))
    assert entries == []
    assert len(calls) == 2  # /robots.txt + first sitemap candidate only!

    # Case B: robots.txt lists foreign TLD (.it) before matching TLD (.com)
    robots_txt = (
        b"Sitemap: https://www.thesenseresort.it/sitemap_index.xml\n"
        b"Sitemap: https://www.thesenseresort.com/sitemap_index.xml\n"
    )
    com_sitemap = b"""<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://www.thesenseresort.com/en/rooms/</loc></url>
    </urlset>"""

    async def fake_multi_domain(url, session):
        if url.endswith("/robots.txt"):
            return "ok", robots_txt, url
        if url == "https://www.thesenseresort.com/sitemap_index.xml":
            return "ok", com_sitemap, url
        return "not_found", None, url

    monkeypatch.setattr(sitemap_fetcher, "_fetch_with_status", fake_multi_domain)
    entries = await sitemap_fetcher.discover_sitemap_urls("https://www.thesenseresort.com/", str(tmp_path))
    assert len(entries) == 1
    assert entries[0].url == "https://www.thesenseresort.com/en/rooms/"


def test_multidomain_sitemap_prioritization():
    from urllib.parse import urlparse
    target = urlparse("https://www.thesenseresort.com/")
    candidates = [
        "https://www.thesenseresort.it/sitemap_index.xml",
        "https://www.thesenseresort.com/sitemap_index.xml",
        "https://www.thesenseresort.fr/sitemap_index.xml",
        "https://www.thesenseresort.com/sitemap.xml",
    ]

    def candidate_priority(u: str) -> tuple[int, int]:
        u_parsed = urlparse(u)
        u_netloc = u_parsed.netloc.lower()
        target_netloc = target.netloc.lower()
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

    sorted_candidates = sorted(candidates, key=candidate_priority)
    assert sorted_candidates[0] == "https://www.thesenseresort.com/sitemap_index.xml"
    assert sorted_candidates[1] == "https://www.thesenseresort.com/sitemap.xml"
