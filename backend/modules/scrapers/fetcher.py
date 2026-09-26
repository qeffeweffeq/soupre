import asyncio
import os
import time
from urllib.parse import urlparse
import logging
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

# Initialize logging
logger = logging.getLogger(__name__)

async def fetch_url_with_browser(url):
    """
    Fetch a URL using Playwright (Chromium) and return its rendered HTML.
    Includes playwright-stealth to bypass Cloudflare/Bot Challenges.
    """
    logger.info(f"[Browser] Fetching page: {url}")
    
    html_content = ""
    cookies_list = []
    screenshot_path = ""
    
    # We will use stealth
    try:
        async with async_playwright() as p:
            # We can also wrap the context manager using Stealth().use_async(p) but this works too
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--disable-features=IsolateOrigins,site-per-process',
                ]
            )
            
            # Create context with typical browser settings
            context = await browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'
            )
            
            page = await context.new_page()
            
            # Apply stealth
            await Stealth().apply_stealth_async(page)
            
            # Goto URL and wait
            try:
                # Wait until domcontentloaded or networkidle
                # Wait until domcontentloaded
                # Wait until domcontentloaded
                try:
                    response = await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                    html_check = await page.content()
                except Exception as e:
                    logger.info("[Browser] Page is navigating (likely bypassing challenge). Waiting...")
                    await page.wait_for_load_state('networkidle', timeout=15000)
                    html_check = await page.content()
                    
                # Check for SiteGround / Cloudflare bot challenges
                if "Checking the site connection security" in html_check or "sg-captcha" in html_check or "cloudflare" in html_check.lower() or len(html_check) < 15000:
                    logger.info("[Browser] Detected Bot Challenge. Waiting for Javascript challenge to clear...")
                    try:
                        # Wait up to 10 seconds for the challenge to resolve and page to reload
                        await page.wait_for_function(
                            "() => !document.body.innerText.includes('Checking the site connection security') && !document.body.innerText.includes('Just a moment')", 
                            timeout=15000
                        )
                        # Wait an additional 3 seconds for the actual DOM to settle after redirect
                        await asyncio.sleep(3)
                        await page.wait_for_load_state('domcontentloaded')
                    except Exception as e:
                        logger.warning(f"[Browser] Bot challenge wait timed out or failed: {e}")
                
                    await page.mouse.wheel(0, 500)
                    await asyncio.sleep(0.5)
                
                html_content = await page.content()
                
                # Take screenshot for the UI
                base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
                temp_media_dir = os.path.join(base_dir, "_downloads")
                os.makedirs(temp_media_dir, exist_ok=True)
                
                cookies_list = await context.cookies()
                domain = urlparse(url).netloc.replace(".", "_")
                filename = f"screenshot_{domain}_{int(time.time())}.png"
                screenshot_path = os.path.join(temp_media_dir, filename)
                
                await page.screenshot(path=screenshot_path, full_page=False)
                logger.info(f"[Browser] Successfully rendered {url}")
                
            except Exception as nav_e:
                logger.error(f"[Browser] Error rendering {url}: {nav_e}")
            finally:
                await browser.close()
                
    except Exception as e:
        logger.error(f"[Browser] Error launching browser: {e}")
        
    return html_content, screenshot_path, cookies_list
