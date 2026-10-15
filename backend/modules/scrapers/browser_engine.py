import os
import logging
from contextlib import asynccontextmanager
from typing import Optional, Any
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

try:
    from modules.config import logger
except ImportError:
    logger = logging.getLogger(__name__)


class BrowserSession:
    """
    Unified browser session abstraction managing Camoufox and Playwright Chromium engines.
    """

    def __init__(
        self,
        browser: Any,
        engine_name: str,
        playwright_cm: Optional[Any] = None,
        camoufox_cm: Optional[Any] = None,
    ):
        self.browser = browser
        self.engine_name = engine_name
        self._playwright_cm = playwright_cm
        self._camoufox_cm = camoufox_cm
        self._closed = False

    async def create_context(self, **kwargs) -> Any:
        """
        Creates a browser context.
        If Camoufox, uses Camoufox's fingerprinted defaults unless explicit overrides are passed.
        If Chromium, defaults viewport to 1920x1080 and a desktop user agent.
        """
        if self.engine_name == "camoufox":
            return await self.browser.new_context(**kwargs)
        else:
            context_kwargs = {
                "viewport": {"width": 1920, "height": 1080},
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            }
            context_kwargs.update(kwargs)
            return await self.browser.new_context(**context_kwargs)

    async def new_page(self, context: Optional[Any] = None, **kwargs) -> Any:
        """
        Creates a page either within a given context or directly on the browser.
        Applies Stealth().apply_stealth_async(page) ONLY if engine_name == "playwright_chromium"
        (playwright-stealth is Chromium-specific and must not be applied to Camoufox Firefox).
        """
        target = context if context is not None else self.browser
        page = await target.new_page(**kwargs)

        if self.engine_name == "playwright_chromium":
            try:
                await Stealth().apply_stealth_async(page)
            except Exception as e:
                logger.warning(f"[BrowserSession] Failed to apply stealth to Chromium page: {e}")

        return page

    async def close(self):
        """
        Safely closes the browser instance and internal context managers.
        """
        if self._closed:
            return
        self._closed = True

        if self.browser:
            try:
                await self.browser.close()
            except Exception as e:
                logger.debug(f"[BrowserSession] Exception closing browser: {e}")

        if self._camoufox_cm:
            try:
                await self._camoufox_cm.__aexit__(None, None, None)
            except Exception as e:
                logger.debug(f"[BrowserSession] Exception exiting camoufox_cm: {e}")

        if self._playwright_cm:
            try:
                await self._playwright_cm.__aexit__(None, None, None)
            except Exception as e:
                logger.debug(f"[BrowserSession] Exception exiting playwright_cm: {e}")


@asynccontextmanager
async def get_browser(headless: bool = True, force_engine: Optional[str] = None):
    """
    Async context manager returning a BrowserSession.
    Attempts to launch Camoufox (with humanize=True) as the primary stealth engine.
    Transparently falls back to Playwright Chromium if Camoufox fails to launch, is missing,
    or if engine is explicitly set to chromium/playwright.
    """
    engine_choice = (force_engine or os.environ.get("SOUPRE_BROWSER_ENGINE", "auto")).strip().lower()
    session: Optional[BrowserSession] = None

    if engine_choice not in ("chromium", "playwright", "playwright_chromium"):
        camoufox_cm = None
        try:
            from camoufox.async_api import AsyncCamoufox
            camoufox_cm = AsyncCamoufox(headless=headless, humanize=True)
            browser = await camoufox_cm.__aenter__()
            session = BrowserSession(
                browser=browser,
                engine_name="camoufox",
                camoufox_cm=camoufox_cm,
            )
            logger.info("[BrowserEngine] Camoufox engine launched successfully.")
        except Exception as e:
            logger.warning(
                f"[BrowserEngine] Camoufox failed to launch ({type(e).__name__}: {e}). Falling back to standard Playwright Chromium..."
            )
            if camoufox_cm:
                try:
                    await camoufox_cm.__aexit__(None, None, None)
                except Exception:
                    pass
            session = None

    if session is None:
        playwright_cm = async_playwright()
        try:
            pw = await playwright_cm.__aenter__()
            browser = await pw.chromium.launch(
                headless=headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-features=IsolateOrigins,site-per-process",
                ],
            )
            session = BrowserSession(
                browser=browser,
                engine_name="playwright_chromium",
                playwright_cm=playwright_cm,
            )
            logger.info("[BrowserEngine] Playwright Chromium fallback engine launched successfully.")
        except Exception:
            if playwright_cm:
                try:
                    await playwright_cm.__aexit__(None, None, None)
                except Exception:
                    pass
            raise

    try:
        yield session
    finally:
        if session:
            await session.close()
