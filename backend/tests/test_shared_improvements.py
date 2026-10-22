import os
import sys
from unittest.mock import MagicMock, patch
import pytest
from bs4 import BeautifulSoup
from sqlmodel import SQLModel, Session, create_engine
from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.extractors.icons_url import parse_sizes, extract_best_icon
from modules.extractors.basic_info import (
    extract_about,
    extract_email,
    extract_phone,
    extract_instagram,
    extract_linkedin,
    extract_founded,
    extract_location,
)
from modules.scrapers.parked import check_parked_domain, is_blank_page
from modules.scrapers.fetcher import dismiss_consent_banners, CONSENT_SELECTORS
from modules.utils.url_utils import normalize_url, normalize_target_url, get_domain_from_url
from modules.utils.text_utils import to_param_case, slugify, parse_name, expand_location
from database.models import ScrapeJob, Page
from main import _migrate_db


# ---------------------------------------------------------------------------
# 1. Icon URL Extractor Tests
# ---------------------------------------------------------------------------

def test_parse_sizes():
    assert parse_sizes("180x180") == 180
    assert parse_sizes("16x16 32x32 64x64") == 64
    assert parse_sizes("any") == 0
    assert parse_sizes(None, "/assets/apple-touch-icon-180x180.png") == 180
    assert parse_sizes("", "https://example.com/icon_512.png?v=2") == 512
    assert parse_sizes(None, "/favicon.ico") == 0


def test_extract_best_icon_resolution_and_priority():
    html = """
    <html>
      <head>
        <link rel="shortcut icon" href="/favicon.ico" sizes="16x16">
        <link rel="icon" href="/icon-32.png" sizes="32x32">
        <link rel="apple-touch-icon" href="/apple-icon.png" sizes="180x180">
        <link rel="android-icon" href="/android-180.png" sizes="180x180">
        <link rel="mask-icon" href="/safari-pinned-tab.svg" sizes="512x512">
        <link rel="icon" href="data:image/png;base64,iVBORw0KGgo=" sizes="1024x1024">
      </head>
    </html>
    """
    soup = BeautifulSoup(html, "html.parser")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    with patch("modules.extractors.icons_url.requests.head", return_value=mock_resp) as mock_head:
        icon = extract_best_icon(soup, "https://studio.example.com/about")
        # Both android-icon and apple-touch-icon are 180x180, android-icon has higher priority (4 vs 3)
        assert icon == "https://studio.example.com/android-180.png"
        mock_head.assert_called_once()


def test_extract_best_icon_skips_404_and_falls_back_to_google_favicon():
    html = """
    <html>
      <head>
        <link rel="apple-touch-icon" href="/broken-180.png" sizes="180x180">
      </head>
    </html>
    """
    soup = BeautifulSoup(html, "html.parser")

    mock_resp = MagicMock()
    mock_resp.status_code = 404
    with patch("modules.extractors.icons_url.requests.head", return_value=mock_resp):
        icon = extract_best_icon(soup, "https://pentagram.com/work")
        assert icon == "https://www.google.com/s2/favicons?domain=pentagram.com&sz=128"


# ---------------------------------------------------------------------------
# 2. Basic Info Extractors Tests
# ---------------------------------------------------------------------------

def test_extract_about_cascade():
    # 1. meta description
    s1 = BeautifulSoup('<head><meta name="description" content="Meta desc"></head>', "html.parser")
    assert extract_about(s1) == "Meta desc"

    # 2. og:description
    s2 = BeautifulSoup('<head><meta property="og:description" content="OG desc"></head>', "html.parser")
    assert extract_about(s2) == "OG desc"

    # 3. JSON-LD description
    s3 = BeautifulSoup(
        '<script type="application/ld+json">{"@context":"https://schema.org","description":"JSON-LD studio bio"}</script>',
        "html.parser",
    )
    assert extract_about(s3) == "JSON-LD studio bio"

    # 4. First <p> in <main> >= 20 chars
    s4 = BeautifulSoup(
        "<main><p>Short</p><p>We are an independent design studio crafting digital experiences.</p></main>",
        "html.parser",
    )
    assert extract_about(s4) == "We are an independent design studio crafting digital experiences."

    # 5. Global fallback skipping cookie text
    long_cookie = "We use cookies and privacy policy settings to improve your browsing experience across our website and services."
    long_bio = (
        "Founded in Milan, our multidisciplinary architecture practice focuses on sustainable urban spaces "
        "and contemporary residential interiors across Europe."
    )
    s5 = BeautifulSoup(f"<div><p>{long_cookie}</p><p>{long_bio}</p></div>", "html.parser")
    assert extract_about(s5) == long_bio


def test_extract_contact_and_social_metadata():
    html = """
    <html>
      <head>
        <meta name="geo.placename" content="Milan, Italy">
        <script type="application/ld+json">
          {"@context": "https://schema.org", "foundingDate": "2016-04-01"}
        </script>
      </head>
      <body>
        <a href="mailto:hello@studio.it?subject=Hi">Email Us</a>
        <a href="tel:+39 02 1234567">Call</a>
        <a href="https://www.instagram.com/p/C12345/">Post</a>
        <a href="https://www.instagram.com/studiomilano/">IG Profile</a>
        <a href="https://www.linkedin.com/company/studiomilano">LinkedIn</a>
      </body>
    </html>
    """
    soup = BeautifulSoup(html, "html.parser")
    assert extract_email(soup) == "hello@studio.it"
    assert extract_phone(soup) == "+39 02 1234567"
    assert extract_instagram(soup) == "https://www.instagram.com/studiomilano/"
    assert extract_linkedin(soup) == "https://www.linkedin.com/company/studiomilano"
    assert extract_founded(soup) == 2016
    assert extract_location(soup) == "Milan, Italy"


# ---------------------------------------------------------------------------
# 3. Parked Domain & Blank Page Checks
# ---------------------------------------------------------------------------

def test_check_parked_and_blank_page():
    assert check_parked_domain("Welcome! This domain is for sale on Sedo.", "https://example.com") is True
    assert check_parked_domain("Normal design studio portfolio content.", "https://example.com") is False

    blank_soup = BeautifulSoup("<html><body><p>Hi</p></body></html>", "html.parser")
    assert is_blank_page(blank_soup) is True

    rich_soup = BeautifulSoup(
        "<html><body><p>" + (" Meaningful content " * 10) + "</p></body></html>",
        "html.parser",
    )
    assert is_blank_page(rich_soup) is False


# ---------------------------------------------------------------------------
# 4. Consent Banner Hiding & Deduplicated Selectors
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dismiss_consent_banners_runs_js_overlay_hider():
    assert len(CONSENT_SELECTORS) == len(set(CONSENT_SELECTORS))
    assert "#onetrust-accept-btn-handler" in CONSENT_SELECTORS
    assert "button[id*='cookie' i]" in CONSENT_SELECTORS

    mock_page = MagicMock()
    mock_locator = MagicMock()
    mock_first = MagicMock()

    async def _not_visible(*args, **kwargs):
        return False

    mock_first.is_visible = _not_visible
    mock_locator.first = mock_first
    mock_page.locator.return_value = mock_locator

    evaluated_scripts = []

    async def _eval(script):
        evaluated_scripts.append(script)

    mock_page.evaluate = _eval

    await dismiss_consent_banners(mock_page)
    assert len(evaluated_scripts) == 1
    assert "onetrust" in evaluated_scripts[0]
    assert "cky-" in evaluated_scripts[0]
    assert "display" in evaluated_scripts[0]


# ---------------------------------------------------------------------------
# 5. URL & Text Utilities + Media Extractor Import
# ---------------------------------------------------------------------------

def test_url_and_text_utilities():
    assert normalize_url("https://www.Example.com/work/") == "example.com/work"
    assert normalize_target_url("Example.com/ Portfolio/ ") == "https://example.com/%20Portfolio" or normalize_target_url("Example.com/portfolio/") == "https://example.com/portfolio"
    assert get_domain_from_url("https://sub.example.co.uk/path", replace_dots=False) == "sub.example.co.uk"
    assert get_domain_from_url("https://sub.example.co.uk/path", replace_dots=True) == "sub_example_co_uk"

    assert to_param_case("🛍 Web Services & Goods") == "web-services-goods"
    assert slugify("camelCaseTitle") == "camel-case-title"

    name, loc, about = parse_name("Studio Dumbar | Rotterdam")
    assert name == "Studio Dumbar"
    assert loc == "Rotterdam"
    assert expand_location(loc) == "Rotterdam, Netherlands"


def test_media_extractor_imports_and_engine_get_best_image():
    from modules.extractors.media import extract_media_urls
    from modules.scrapers import engine as scraper_engine
    from modules.parsers import markdown_converter

    assert scraper_engine.get_best_image is markdown_converter.get_best_image
    assert callable(extract_media_urls)


# ---------------------------------------------------------------------------
# 6. Database Migration & Backfill Tests
# ---------------------------------------------------------------------------

def test_migrate_db_adds_columns_and_backfills_title_and_icon(tmp_path):
    db_file = tmp_path / "test_migrate.db"
    test_engine = create_engine(f"sqlite:///{db_file}")

    # Create legacy tables without title or icon_url
    with test_engine.connect() as conn:
        conn.execute(text(
            "CREATE TABLE scrapejob ("
            "id INTEGER PRIMARY KEY, "
            "target_url TEXT NOT NULL, "
            "status TEXT NOT NULL DEFAULT 'completed', "
            "created_at TIMESTAMP"
            ")"
        ))
        conn.execute(text(
            "CREATE TABLE page ("
            "id INTEGER PRIMARY KEY, "
            "job_id INTEGER NOT NULL, "
            "url TEXT NOT NULL, "
            "title TEXT NOT NULL"
            ")"
        ))
        conn.execute(text(
            "INSERT INTO scrapejob (id, target_url, status) VALUES (1, 'https://pentagram.com', 'completed')"
        ))
        conn.execute(text(
            "INSERT INTO page (id, job_id, url, title) VALUES (1, 1, 'https://pentagram.com', 'Pentagram —The world’s largest independent design consultancy')"
        ))
        conn.commit()

    # Run migration + backfill
    _migrate_db(test_engine)

    with Session(test_engine) as session:
        job = session.get(ScrapeJob, 1)
        assert job is not None
        assert job.title == "Pentagram —The world’s largest independent design consultancy"
        assert job.icon_url == "https://www.google.com/s2/favicons?domain=pentagram.com&sz=128"


# ---------------------------------------------------------------------------
# 9. Elementor Header/Footer Body Class, Cloaking Removal & Sitemap Classification
# ---------------------------------------------------------------------------

def test_elementor_body_class_and_markdown_extraction():
    from modules.parsers.html_parser import should_skip_element
    from modules.parsers.markdown_converter import convert_content_to_markdown

    html = """
    <html>
      <head>
        <title>Ristorante - Hotel Il Settebello</title>
        <style>
          .elementor-2396 .elementor-element.elementor-element-c80fa71 {
              background-image: url("https://hotelsettebello.italianhotelgroup.it/wp-content/uploads/2025/11/hero-dining.jpg");
          }
        </style>
      </head>
      <body class="wp-singular page-template page-template-elementor_header_footer page">
        <div data-elementor-type="header" class="elementor elementor-16">
          <nav><a href="/menu">Header Nav</a></nav>
        </div>
        <h1 style="position:absolute; width:1px; height:1px; clip-path:inset(50%);">Hidden Cloaked H1</h1>
        <div data-seo-content="true" style="position:absolute; width:1px; height:1px; clip-path:inset(50%);">
          <p>Cloaked AI SEO text Rated 7.4/10 su Booking.com</p>
        </div>
        <div data-elementor-type="wp-page" data-elementor-id="2396" class="elementor elementor-2396">
          <div class="elementor-element elementor-element-c80fa71 e-con" data-id="c80fa71">
            <h2>Ristorante Vista Mare</h2>
            <p class="elementor-hidden-mobile">La giornata inizia con una ricca colazione vista mare.</p>
            <p><a href="tel:+390586620039">+39 0586 620039</a></p>
            <p><a href="mailto:info@hotel.it">info@hotel.it</a></p>
          </div>
          <div class="elementor-element elementor-hidden-desktop elementor-hidden-tablet elementor-hidden-mobile">
            <h2>Offerte montagna 2026</h2>
            <p>6 notti Deposito sci in ski room</p>
          </div>
        </div>
        <dialog id="moove_gdpr_cookie_modal">
          <p>Cookie preferences modal</p>
        </dialog>
      </body>
    </html>
    """
    soup = BeautifulSoup(html, "html.parser")
    h2 = soup.find("h2")
    assert should_skip_element(h2) is False
    header_nav = soup.find("nav")
    assert should_skip_element(header_nav) is True

    main_content = soup.find("main") or soup.find("body")
    md = convert_content_to_markdown(main_content, soup, "https://hotelsettebello.italianhotelgroup.it/ristorante/", None, None)

    assert "Ristorante Vista Mare" in md
    assert "La giornata inizia con una ricca colazione vista mare." in md
    assert "hero-dining.jpg" in md
    assert "[+39 0586 620039](tel:+390586620039)" in md
    assert "[info@hotel.it](mailto:info@hotel.it)" in md
    assert "Cloaked AI SEO text" not in md
    assert "Hidden Cloaked H1" not in md
    assert "Offerte montagna 2026" not in md
    assert "Cookie preferences modal" not in md


def test_determine_content_type_wp_page_sitemap_and_italian_segments():
    from modules.scrapers.sitemap_fetcher import determine_content_type

    assert determine_content_type("https://hotelsettebello.italianhotelgroup.it/", "wp-sitemap-posts-page-1.xml") == "pages"
    assert determine_content_type("https://hotelsettebello.italianhotelgroup.it/contatti/", "wp-sitemap-posts-page-1.xml") == "pages"
    assert determine_content_type("https://hotelsettebello.italianhotelgroup.it/dintorni/", "wp-sitemap-posts-page-1.xml") == "pages"
    assert determine_content_type("https://hotelsettebello.italianhotelgroup.it/camere/", "wp-sitemap-posts-page-1.xml") == "rooms"
    assert determine_content_type("https://hotelsettebello.italianhotelgroup.it/ristorante/", "wp-sitemap-posts-page-1.xml") == "dining"
    assert determine_content_type("https://hotelsettebello.italianhotelgroup.it/hotel/", "wp-sitemap-posts-page-1.xml") == "hotels"
    assert determine_content_type("https://hotelsettebello.italianhotelgroup.it/offerte/", "wp-sitemap-posts-page-1.xml") == "offers"
    assert determine_content_type("https://hotelsettebello.italianhotelgroup.it/spa-2/", "wp-sitemap-posts-page-1.xml") == "wellness"
    assert determine_content_type("https://hotelsettebello.italianhotelgroup.it/news-item/", "wp-sitemap-posts-post-1.xml") == "blog"


def test_job_contact_csv_persistence(tmp_path):
    from modules.scrapers.engine import _update_job_contact_csv, _read_job_contact_csv

    job_dir = str(tmp_path / "job_1_canado")
    html_page1 = """
    <html>
      <head><meta name="geo.placename" content="Castagneto Carducci"></head>
      <body>
        <a href="mailto:info@canadofamilyhotel.it">Email</a>
        <a href="tel:+3905657721">+3905657721</a>
        <a href="https://www.instagram.com/canadofamilyhotel/">IG</a>
      </body>
    </html>
    """
    soup1 = BeautifulSoup(html_page1, "html.parser")
    contact = _update_job_contact_csv(
        job_dir,
        soup1,
        icon_url="https://canadofamilyhotel.it/storage/2025/12/logo-fv.png",
    )

    assert contact["Icon URL"] == "https://canadofamilyhotel.it/storage/2025/12/logo-fv.png"
    assert contact["Location"] == "Castagneto Carducci"
    assert contact["Email"] == "info@canadofamilyhotel.it"
    assert contact["Phone"] == "+3905657721"
    assert contact["Instagram"] == "https://www.instagram.com/canadofamilyhotel/"

    saved = _read_job_contact_csv(job_dir)
    assert saved == contact
