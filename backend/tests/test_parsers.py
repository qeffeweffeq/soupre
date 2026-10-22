import pytest
from bs4 import BeautifulSoup
from modules.parsers.html_parser import find_main_content, should_skip_element
from modules.parsers.markdown_converter import convert_content_to_markdown, process_all_elements


def test_aspnet_form_unwrapped_and_content_preserved():
    """Verify that ASP.NET WebForms wrapping the entire body does not wipe out content."""
    html = """
    <html>
    <body>
        <div id="globalContainer">
            <form name="aspnetForm" method="post" action="/" id="aspnetForm">
                <div id="ctl00_Header" class="sc-container">
                    <div class="headerVideo">
                        <h1 class="headline homeSite">Your private retreat hotel & lodges.</h1>
                    </div>
                </div>
                <div id="ctl00_MasterContainer" class="sc-masterContainer">
                    <p class="subHeader">A serene sanctuary surrounded by pristine nature.</p>
                    <section class="headerContentImg boxContentHeader" id="section1">
                        <div class="headerHeadline">Luxury Chalets</div>
                        <p>Experience private alpine living with panoramic views.</p>
                    </section>
                </div>
            </form>
        </div>
    </body>
    </html>
    """
    soup = BeautifulSoup(html, 'html.parser')
    main_content = find_main_content(soup)
    md = convert_content_to_markdown(main_content, soup, 'https://example.com/', None, None)

    assert "Your private retreat hotel & lodges." in md
    assert "A serene sanctuary surrounded by pristine nature." in md
    assert "Luxury Chalets" in md
    assert "Experience private alpine living with panoramic views." in md


def test_carousel_clone_deduplication():
    """Verify that carousel clones (owl-item cloned, swiper-slide-duplicate, slick-cloned) are removed."""
    html = """
    <html>
    <body>
        <section class="services">
            <div class="owl-carousel">
                <div class="owl-stage">
                    <div class="owl-item cloned"><div>Duplicated Card 1</div></div>
                    <div class="owl-item active"><div>Duplicated Card 1</div></div>
                    <div class="owl-item active"><div>Unique Card 2</div></div>
                    <div class="owl-item cloned"><div>Unique Card 2</div></div>
                </div>
            </div>
        </section>
    </body>
    </html>
    """
    soup = BeautifulSoup(html, 'html.parser')
    main_content = find_main_content(soup)
    md = convert_content_to_markdown(main_content, soup, 'https://example.com/', None, None)

    # Duplicated Card 1 and Unique Card 2 should each appear only once
    assert md.count("Duplicated Card 1") == 1
    assert md.count("Unique Card 2") == 1


def test_should_skip_element_content_protection():
    """Verify should_skip_element does not skip headings or subheaders even if classes contain header."""
    html = """
    <div>
        <div id="ctl00_Header">
            <h1 class="headerHeadline">Title</h1>
            <p class="subHeader">Subtitle paragraph</p>
        </div>
        <header class="site-header">
            <nav><a href="/">Home</a></nav>
        </header>
    </div>
    """
    soup = BeautifulSoup(html, 'html.parser')
    h1 = soup.find('h1')
    p = soup.find('p')
    nav = soup.find('nav')

    assert should_skip_element(h1) is False
    assert should_skip_element(p) is False
    assert should_skip_element(nav) is True
