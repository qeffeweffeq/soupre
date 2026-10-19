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
