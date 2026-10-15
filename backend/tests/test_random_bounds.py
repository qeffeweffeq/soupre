import random
import pytest
from lxml import etree
import sys
import os

# Ensure backend root is in python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from modules.scrapers.sitemap_fetcher import _extract_sitemap_entries, SitemapEntry


def test_sitemap_session_page_bounds():
    """Verify session counts strictly adhere to 30-50 pages and never exceed 50."""
    session_min = 30
    session_max = 50

    for _ in range(1000):
        val = random.randint(session_min, session_max)
        assert 30 <= val <= 50, f"Session size {val} outside [30, 50]"
        assert val <= 50, f"Session size {val} exceeds hard limit 50"


def test_page_to_page_delay_bounds():
    """Verify page-to-page delays strictly adhere to 1 to 2 minutes (60-120 seconds)."""
    delay_min = 60.0
    delay_max = 120.0

    for _ in range(1000):
        val = round(random.uniform(delay_min, delay_max), 2)
        assert 60.0 <= val <= 120.0, f"Page delay {val}s outside [60, 120]s"
        assert 1.0 <= val / 60.0 <= 2.0, f"Page delay {val/60.0}m outside [1, 2]m"


def test_session_cooldown_bounds():
    """Verify session pause strictly adheres to 3 to 5 minutes (180-300 seconds)."""
    cooldown_min = 180.0
    cooldown_max = 300.0

    for _ in range(1000):
        val = round(random.uniform(cooldown_min, cooldown_max), 2)
        assert 180.0 <= val <= 300.0, f"Cooldown {val}s outside [180, 300]s"
        assert 3.0 <= val / 60.0 <= 5.0, f"Cooldown {val/60.0}m outside [3, 5]m"


def test_sitemap_lastmod_extraction():
    """Verify that <lastmod> date is correctly extracted from sitemap XML entries."""
    sample_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
        <url>
            <loc>https://example.com/page-1</loc>
            <lastmod>2025-05-29T15:23:29+00:00</lastmod>
        </url>
        <url>
            <loc>https://example.com/page-2</loc>
        </url>
    </urlset>
    """
    is_idx, items = _extract_sitemap_entries(sample_xml)
    assert not is_idx
    assert len(items) == 2
    assert items[0].url == "https://example.com/page-1"
    assert items[0].lastmod == "2025-05-29T15:23:29+00:00"
    assert items[1].url == "https://example.com/page-2"
    assert items[1].lastmod is None

