import logging
import asyncio
from playwright.async_api import async_playwright

async def fetch_url_with_browser(url: str, config=None) -> tuple[str, str]:
    """Fetches fully JS-rendered HTML and a screenshot using Playwright."""
    logging.info(f"[Browser] Fetching page: {url}")
    import os
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    media_dir = os.path.join(base_dir, "_downloads", "media")
    url_hash = abs(hash(url))
    abs_screenshot_path = os.path.join(media_dir, f"screenshot_{url_hash}.png")
    db_screenshot_path = f"../_downloads/media/screenshot_{url_hash}.png"
    
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
                ignore_https_errors=True,
            )
            page = await context.new_page()
            await page.goto(url, wait_until='load', timeout=30000)
            # Give SPA a brief moment to render dynamic content
            await page.wait_for_timeout(2000)
            
            # Save screenshot
            await page.screenshot(path=abs_screenshot_path, full_page=True)
            
            html = await page.content()
            await browser.close()
            logging.info(f"[Browser] Successfully rendered {url}")
            return html, db_screenshot_path
    except Exception as e:
        logging.error(f"[Browser] Error rendering {url}: {e}")
        return "", ""
