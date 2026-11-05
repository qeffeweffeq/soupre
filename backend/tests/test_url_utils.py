"""
tests/test_url_utils.py
Tests for URL sanitization and multi-URL extraction.
"""

import pytest
from modules.utils.url_utils import (
    TRACKING_PARAMS,
    extract_urls_from_text,
    sanitize_url,
)


def test_tracking_params_presence():
    required_params = [
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "utm_id",
        "utm_reader",
        "utm_referrer",
        "utm_name",
        "fbclid",
        "gclid",
        "gbraid",
        "wbraid",
        "msclkid",
        "dclid",
        "yclid",
        "twclid",
        "ttclid",
        "ref",
        "ref_src",
        "ref_url",
        "source",
        "feature",
        "mc_cid",
        "mc_eid",
        "igshid",
        "_hsenc",
        "_hsmi",
    ]
    for param in required_params:
        assert param in TRACKING_PARAMS


def test_sanitize_url_strips_tracking_and_fragment():
    url = "https://example.com/products/item?utm_source=twitter&fbclid=123&page=2#reviews"
    sanitized = sanitize_url(url)
    assert sanitized == "https://example.com/products/item?page=2"


def test_sanitize_url_strips_trailing_slash():
    url = "https://example.com/blog/"
    assert sanitize_url(url) == "https://example.com/blog"

    url_root = "https://example.com/"
    assert sanitize_url(url_root) == "https://example.com"


def test_sanitize_url_preserves_functional_params():
    url = "https://example.com/search?q=recipes&category=soup&utm_campaign=winter"
    assert sanitize_url(url) == "https://example.com/search?q=recipes&category=soup"


def test_sanitize_url_empty_or_none():
    assert sanitize_url("") == ""
    assert sanitize_url("   ") == ""
    assert sanitize_url(None) == ""


def test_sanitize_url_adds_scheme_if_missing():
    url = "example.com/article?ref=news"
    assert sanitize_url(url) == "https://example.com/article"


def test_extract_urls_from_text_deduplication_and_order():
    text = """
    Check out https://site.com/doc?utm_source=slack#intro
    also see http://site.com/doc?utm_medium=email
    and https://another.org/page.
    Duplicate: https://site.com/doc
    """
    urls = extract_urls_from_text(text)
    assert urls == [
        "https://site.com/doc",
        "http://site.com/doc",
        "https://another.org/page",
    ]


def test_extract_urls_from_csv_or_markdown():
    csv_content = """url,name
https://news.ycombinator.com/item?id=12345&utm_source=hn,Item
https://example.org/path/?gclid=xyz,Example
"""
    urls = extract_urls_from_text(csv_content)
    assert urls == [
        "https://news.ycombinator.com/item?id=12345",
        "https://example.org/path",
    ]


def test_extract_urls_empty_text():
    assert extract_urls_from_text("") == []
    assert extract_urls_from_text(None) == []
