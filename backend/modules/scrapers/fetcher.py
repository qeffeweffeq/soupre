import asyncio
import os
import time
from typing import Optional
from urllib.parse import urlparse
import logging
from modules.config import DOWNLOADS_DIR, COOKIE_BUTTON_SELECTORS
from .browser_engine import get_browser

# Initialize logging
logger = logging.getLogger(__name__)

PRELOADER_SELECTORS = [
    ".blocco-loader",
    "[class*='preloader' i]",
    "[id*='preloader' i]",
    "[class*='loader-overlay' i]",
    "[class*='splash-screen' i]",
    ".loading-mask",
    ".page-loader",
]

_BASE_CONSENT_SELECTORS = [
    "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
    "#onetrust-accept-btn-handler",
    "button#accept-all",
    "button[id*='cookie-accept' i]",
    "button[class*='cookie-accept' i]",
    "button[id*='acceptAll' i]",
    "button[id*='accept-cookies' i]",
    "button:has-text('Accept all')",
    "button:has-text('Accetta tutti')",
    "button:has-text('Allow all')",
    "button:has-text('Accetta')",
    "button:has-text('Accept')",
    "button:has-text('Alle akzeptieren')",
    "button:has-text('Tout accepter')",
]

CONSENT_SELECTORS = list(dict.fromkeys(_BASE_CONSENT_SELECTORS + list(COOKIE_BUTTON_SELECTORS)))

COOKIE_OVERLAY_SELECTOR = (
    "[id*='cookie'], [class*='cookie'], [class*='consent'], [id*='onetrust'], "
    "[class*='cky-'], [id*='usercentrics'], [id*='tarteaucitron'], "
    "[class*='cc-window'], [class*='cm-banner']"
)


async def dismiss_consent_banners(page) -> None:
    """Attempt to auto-dismiss common cookie consent banners and hide remaining overlays."""
    for sel in CONSENT_SELECTORS:
        try:
            loc = page.locator(sel).first
            if await loc.is_visible(timeout=400):
                await loc.click(timeout=1000)
                logger.info(f"[Browser] Dismissed consent banner with selector '{sel}'")
                await asyncio.sleep(0.3)
                break
        except Exception:
            pass

    # Fallback: hide any remaining fixed/sticky/absolute cookie or consent overlays via DOM
    try:
        await page.evaluate(f"""
            () => {{
                const selectors = "{COOKIE_OVERLAY_SELECTOR}";
                document.querySelectorAll(selectors).forEach(el => {{
                    const style = window.getComputedStyle(el);
                    if (style.position === 'fixed' || style.position === 'sticky' || style.position === 'absolute') {{
                        el.style.setProperty('display', 'none', 'important');
                        el.style.setProperty('opacity', '0', 'important');
                        el.style.setProperty('visibility', 'hidden', 'important');
                        el.style.setProperty('pointer-events', 'none', 'important');
                    }}
                }});
            }}
        """)
    except Exception:
        pass


async def wait_for_preloaders_to_hide(page) -> None:
    """Wait for preloader / splash screen overlays to hide or detach."""
    for sel in PRELOADER_SELECTORS:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible(timeout=400):
                logger.info(f"[Browser] Detected active preloader '{sel}', waiting for it to hide...")
                try:
                    await loc.wait_for(state="hidden", timeout=6000)
                    logger.info(f"[Browser] Preloader '{sel}' hidden successfully.")
                except Exception:
                    logger.warning(f"[Browser] Preloader '{sel}' still visible after 6s. Force hiding via DOM...")
                    await page.evaluate(f"""
                        () => {{
                            document.querySelectorAll('{sel}').forEach(el => {{
                                el.style.setProperty('display', 'none', 'important');
                                el.style.setProperty('opacity', '0', 'important');
                                el.style.setProperty('visibility', 'hidden', 'important');
                                el.style.setProperty('pointer-events', 'none', 'important');
                            }});
                        }}
                    """)
        except Exception:
            pass


async def fetch_url_with_browser(
    url: str,
    take_screenshot: bool = True,
    target_screenshot_path: Optional[str] = None
):
    """
    Fetch a URL using the unified browser engine (Camoufox primary, Playwright Chromium fallback)
    and return its rendered HTML, screenshot path, and cookies.
    """
    logger.info(f"[Browser] Fetching page: {url} (screenshot={take_screenshot})")
    
    html_content = ""
    cookies_list = []
    screenshot_path = ""
    
    try:
        async with get_browser(headless=True) as session:
            context = await session.create_context()
            page = await session.new_page(context=context)
            
            # Goto URL and wait
            try:
                try:
                    response = await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                    html_check = await page.content()
                except Exception as e:
                    logger.info("[Browser] Page is navigating (likely bypassing challenge). Waiting...")
                    try:
                        await page.wait_for_load_state('domcontentloaded', timeout=10000)
                    except Exception:
                        pass
                    html_check = await page.content()
                    
                # Check for SiteGround / Cloudflare / generic bot challenges
                html_lower = html_check.lower()
                if (
                    "checking the site connection security" in html_lower
                    or "checking your browser" in html_lower
                    or "confirm you are human" in html_lower
                    or "just a moment" in html_lower
                    or "sg-captcha" in html_lower
                    or "cloudflare" in html_lower
                    or len(html_check) < 15000
                ):
                    logger.info("[Browser] Detected Bot Challenge. Waiting for Javascript challenge to clear...")
                    try:
                        # Wait up to 15 seconds for the challenge to resolve and page to reload
                        await page.wait_for_function(
                            "() => { if (!document.body) return false; const t = (document.body.innerText || '').toLowerCase(); const title = (document.title || '').toLowerCase(); return !t.includes('checking the site connection security') && !t.includes('just a moment') && !t.includes('checking your browser') && !t.includes('confirm you are human') && !title.includes('just a moment') && !title.includes('checking your browser'); }",
                            timeout=15000
                        )
                        # Wait an additional 3 seconds for the actual DOM to settle after redirect
                        await asyncio.sleep(3)
                        try:
                            await page.wait_for_load_state('domcontentloaded', timeout=5000)
                        except Exception:
                            pass
                    except Exception as e:
                        logger.warning(f"[Browser] Bot challenge wait timed out or failed: {e}")
                
                    await page.mouse.wheel(0, 500)
                    await asyncio.sleep(0.5)
                
                # Acquire cookies
                cookies_list = await context.cookies()

                # Auto-dismiss consent banners & wait for splash preloader overlays to clear
                await dismiss_consent_banners(page)
                await wait_for_preloaders_to_hide(page)
                await asyncio.sleep(1.0)

                # Take screenshot for the UI if requested
                if take_screenshot:
                    if target_screenshot_path:
                        screenshot_path = target_screenshot_path
                        os.makedirs(os.path.dirname(os.path.abspath(screenshot_path)), exist_ok=True)
                    else:
                        domain = urlparse(url).netloc.replace(".", "_")
                        filename = f"screenshot_{domain}_{int(time.time())}.png"
                        screenshot_path = os.path.join(DOWNLOADS_DIR, filename)
                        os.makedirs(DOWNLOADS_DIR, exist_ok=True)

                    try:
                        await page.screenshot(path=screenshot_path, full_page=False)
                    except Exception as e:
                        logger.warning(f"[Browser] Failed to take screenshot for {url}: {e}")
                        screenshot_path = ""
                else:
                    screenshot_path = ""

                # Auto-expand "Load more" / infinite scroll pagination if present
                load_more_selector = '#load_more_button, [id*="load_more" i], [title*="Load more" i], .load-more'
                try:
                    load_more_btn = page.locator(load_more_selector).first
                    has_load_more = await load_more_btn.is_visible(timeout=1000)
                except Exception:
                    has_load_more = False

                if has_load_more:
                    logger.info(f"[Browser] Detected 'Load more' button on {url}. Expanding infinite content...")
                    clicks = 0
                    max_clicks = 200
                    consecutive_failures = 0

                    while clicks < max_clicks:
                        try:
                            prev_count = await page.locator('.post, article, .item, .entry').count()
                            if not await load_more_btn.is_visible(timeout=1000):
                                logger.info(f"[Browser] 'Load more' button no longer visible after {clicks} clicks.")
                                break

                            await load_more_btn.scroll_into_view_if_needed(timeout=2000)
                            await load_more_btn.click(timeout=2000)

                            try:
                                await page.wait_for_function(
                                    f"document.querySelectorAll('.post, article, .item, .entry').length > {prev_count}",
                                    timeout=5000
                                )
                                clicks += 1
                                consecutive_failures = 0
                                if clicks % 10 == 0:
                                    curr_count = await page.locator('.post, article, .item, .entry').count()
                                    logger.info(f"[Browser] Loaded more content: {clicks} clicks ({curr_count} items)...")
                            except Exception:
                                consecutive_failures += 1
                                if consecutive_failures >= 2:
                                    logger.info(f"[Browser] Finished loading more content after {clicks} clicks.")
                                    break
                                await asyncio.sleep(1)
                        except Exception as e:
                            logger.debug(f"[Browser] Load more expansion ended: {e}")
                            break

                html_content = await page.content()
                logger.info(f"[Browser] Successfully rendered {url} using engine '{session.engine_name}'")
                
            except Exception as nav_e:
                logger.error(f"[Browser] Error rendering {url}: {nav_e}")
            finally:
                try:
                    await page.close()
                except Exception:
                    pass
                try:
                    await context.close()
                except Exception:
                    pass
                
    except Exception as e:
        logger.error(f"[Browser] Error launching browser: {e}")
        
    return html_content, screenshot_path, cookies_list
