import os
import sys
import pytest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from modules.scrapers.browser_engine import get_browser, BrowserSession
from modules.scrapers.fetcher import fetch_url_with_browser


@pytest.mark.asyncio
async def test_get_browser_camoufox_default():
    """Verify that get_browser defaults to Camoufox engine."""
    async with get_browser(headless=True) as session:
        assert isinstance(session, BrowserSession)
        assert session.engine_name == "camoufox"
        assert session.browser is not None

        context = await session.create_context()
        page = await session.new_page(context=context)
        await page.goto("data:text/html,<h1>Camoufox Ready</h1>")
        title = await page.inner_text("h1")
        assert title == "Camoufox Ready"

        await page.close()
        await context.close()


@pytest.mark.asyncio
async def test_get_browser_chromium_forced(monkeypatch):
    """Verify that forcing chromium engine launches Playwright Chromium."""
    monkeypatch.setenv("SOUPRE_BROWSER_ENGINE", "chromium")
    async with get_browser(headless=True) as session:
        assert isinstance(session, BrowserSession)
        assert session.engine_name == "playwright_chromium"
        assert session.browser is not None

        context = await session.create_context()
        page = await session.new_page(context=context)
        await page.goto("data:text/html,<h1>Chromium Ready</h1>")
        title = await page.inner_text("h1")
        assert title == "Chromium Ready"

        await page.close()
        await context.close()


@pytest.mark.asyncio
async def test_get_browser_fallback_on_camoufox_failure(monkeypatch):
    """Verify that when Camoufox fails to launch, it transparently falls back to Playwright Chromium."""
    monkeypatch.setenv("SOUPRE_BROWSER_ENGINE", "auto")

    class FailingCamoufox:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            raise RuntimeError("Simulated Camoufox launch crash")

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    with patch("camoufox.async_api.AsyncCamoufox", FailingCamoufox):
        async with get_browser(headless=True) as session:
            assert isinstance(session, BrowserSession)
            assert session.engine_name == "playwright_chromium"
            assert session.browser is not None

            context = await session.create_context()
            page = await session.new_page(context=context)
            await page.goto("data:text/html,<h1>Fallback Chromium Ready</h1>")
            content = await page.content()
            assert "Fallback Chromium Ready" in content

            await page.close()
            await context.close()


@pytest.mark.asyncio
async def test_fetch_url_with_browser(tmp_path):
    """Verify fetch_url_with_browser fetches HTML, produces screenshot, and extracts cookies."""
    target_screenshot = str(tmp_path / "test_screenshot.png")
    url = "https://example.com"

    html, screenshot_path, cookies = await fetch_url_with_browser(
        url=url,
        take_screenshot=True,
        target_screenshot_path=target_screenshot,
    )

    assert "Example Domain" in html
    assert screenshot_path == target_screenshot
    assert os.path.exists(target_screenshot)
    assert os.path.getsize(target_screenshot) > 0
    assert isinstance(cookies, list)


@pytest.mark.asyncio
async def test_wait_for_preloaders_and_dismiss_consent():
    """Verify dismiss_consent_banners and wait_for_preloaders_to_hide remove overlays."""
    from modules.scrapers.fetcher import dismiss_consent_banners, wait_for_preloaders_to_hide
    async with get_browser(headless=True) as session:
        context = await session.create_context()
        page = await session.new_page(context=context)
        html = """
        <html>
          <body>
            <button id="CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll" onclick="this.remove()">Accept All</button>
            <div class="blocco-loader" style="opacity: 0.98;">Loading...</div>
          </body>
        </html>
        """
        await page.set_content(html)
        await dismiss_consent_banners(page)
        # Cookie banner should have been clicked and removed
        btn_count = await page.locator("#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll").count()
        assert btn_count == 0

        await wait_for_preloaders_to_hide(page)
        preloader_display = await page.evaluate("() => document.querySelector('.blocco-loader').style.display")
        assert preloader_display == "none"

        await page.close()
        await context.close()

