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
