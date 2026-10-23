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
    "button:has-text('Accetta tutti i cookie')",
    "button:has-text('Accetta tutti')",
    "button:has-text('Accetto')",
    "button:has-text('Allow all')",
    "button:has-text('Accetta')",
    "button:has-text('Accept')",
    "button:has-text('CONTINUA SU')",
    "button:has-text('CONFERMA')",
    "button:has-text('CONTINUE')",
    "button:has-text('CONFIRM')",
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
    initial_url = getattr(page, "url", "")
    for sel in CONSENT_SELECTORS:
        try:
            loc = page.locator(sel).first
            if await loc.is_visible(timeout=350):
                # Verify that the element is not an anchor tag with an external navigation link
                tag_name = await loc.evaluate("el => el.tagName.toLowerCase()")
                href = await loc.evaluate("el => el.getAttribute('href') || ''")
                if tag_name == "a" and href and not href.startswith("#") and not href.startswith("javascript:"):
                    continue
                await loc.click(timeout=1000)
                logger.info(f"[Browser] Dismissed consent banner with selector '{sel}'")
                await asyncio.sleep(0.4)
                # Safeguard: if clicking navigated away to a cookie/privacy policy URL, return to initial_url
                curr_url = getattr(page, "url", "")
                if curr_url != initial_url and any(k in curr_url.lower() for k in ["cookie", "privacy", "consent", "legal"]):
                    logger.warning(f"[Browser] Selector '{sel}' triggered navigation to policy URL {curr_url}. Returning to {initial_url}...")
                    await page.goto(initial_url, wait_until="domcontentloaded", timeout=15000)
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
                is_aux_resource = urlparse(url).path.lower().endswith((".xml", ".txt", ".gz"))
                try:
                    await page.goto(url, wait_until="load", timeout=22000)
                    html_check = await page.content()
                except Exception:
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=12000)
                        html_check = await page.content()
                    except Exception as e:
                        current_url = getattr(page, "url", "") or ""
                        if current_url in ("about:blank", ""):
                            logger.warning(f"[Browser] Navigation failed or timed out before reaching {url}: {e}")
                            return "", "", []
                        logger.info("[Browser] Page is navigating (likely bypassing challenge). Waiting...")
                        try:
                            await page.wait_for_load_state('domcontentloaded', timeout=8000)
                        except Exception:
                            pass
                        html_check = await page.content()

                if getattr(page, "url", "") in ("about:blank", "") or len(html_check.strip()) < 60:
                    logger.warning(f"[Browser] Empty or blank document returned for {url}")
                    return "", "", []

                # Check for SiteGround / Cloudflare / generic bot challenges
                html_lower = html_check.lower()
                is_bot_challenge = (
                    "checking the site connection security" in html_lower
                    or "checking your browser" in html_lower
                    or "confirm you are human" in html_lower
                    or "just a moment" in html_lower
                    or "sg-captcha" in html_lower
                    or "cf-browser-verification" in html_lower
                    or "challenge-platform" in html_lower
                )
                if is_bot_challenge:
                    logger.info("[Browser] Detected Bot Challenge. Waiting for Javascript challenge to clear...")
                    try:
                        # Wait up to 15 seconds for the challenge to resolve and page to reload
                        await page.wait_for_function(
                            "() => { const root = document.body || document.documentElement; if (!root) return false; const t = (root.innerText || root.textContent || '').toLowerCase(); const title = (document.title || '').toLowerCase(); return !t.includes('checking the site connection security') && !t.includes('just a moment') && !t.includes('checking your browser') && !t.includes('confirm you are human') && !title.includes('just a moment') && !title.includes('checking your browser'); }",
                            timeout=15000
                        )
                        # Wait an additional 2 seconds for the actual DOM to settle after redirect
                        await asyncio.sleep(2)
                        try:
                            await page.wait_for_load_state('domcontentloaded', timeout=5000)
                        except Exception:
                            pass
                    except Exception as e:
                        logger.warning(f"[Browser] Bot challenge wait timed out or failed: {e}")

                    if not is_aux_resource:
                        await page.mouse.wheel(0, 500)
                        await asyncio.sleep(0.5)

                # Acquire cookies
                cookies_list = await context.cookies()

                if not is_aux_resource:
                    # Auto-dismiss consent banners & wait for splash preloader overlays to clear
                    await dismiss_consent_banners(page)
                    await wait_for_preloaders_to_hide(page)
                    await asyncio.sleep(0.5)

                    # Simulate gentle human interactions (satisfies bot sensors and triggers lazy rendering)
                    try:
                        await page.mouse.move(250, 350)
                        await asyncio.sleep(0.2)
                        await page.mouse.wheel(0, 300)
                        await asyncio.sleep(0.3)
                    except Exception:
                        pass

                    # Detect client-side SPA hydration delay (e.g. React / Salesforce Commerce Cloud)
                    try:
                        text_len = await page.evaluate("() => (document.body ? (document.body.innerText || '').length : 0)")
                        if text_len < 300:
                            logger.info(f"[Browser] Low content detected ({text_len} chars). Waiting up to 2.5s for SPA hydration...")
                            await asyncio.sleep(2.5)
                    except Exception:
                        pass

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

                if not is_aux_resource:
                    try:
                        await page.evaluate(
                            """() => {
                                // 1. Remove 1pxx1px AI/SEO cloaking blocks injected at runtime
                                document.querySelectorAll('[data-seo-content], [style*="clip-path:inset(50%)"], [style*="clip-path: inset(50%)"]').forEach(el => el.remove());

                                // 2. Ensure Elementor lazy containers unlock their CSS background-image rules
                                document.querySelectorAll('.e-con.e-parent').forEach(el => el.classList.add('e-lazyloaded'));

                                // 3. Persist computed CSS background-image URLs onto data-bg-src so the HTML parser can extract them
                                const candidates = document.querySelectorAll('div, section, article, figure');
                                for (const el of candidates) {
                                    if (el.closest('header, footer, nav, [data-elementor-type="header"], [data-elementor-type="footer"], [data-elementor-type="popup"], .elementor-hidden-desktop')) {
                                        continue;
                                    }
                                    if (el.hasAttribute('data-bg-src')) continue;
                                    const bg = window.getComputedStyle(el).backgroundImage;
                                    if (bg && bg !== 'none' && bg.includes('url(') && !bg.includes('gradient(')) {
                                        const m = bg.match(/url\\(["']?(https?:\\/\\/[^"')]+)["']?\\)/i);
                                        if (m && m[1] && !m[1].startsWith('data:')) {
                                            el.setAttribute('data-bg-src', m[1]);
                                        }
                                    }
                                }
                            }"""
                        )
                    except Exception as eval_e:
                        logger.debug(f"[Browser] Pre-serialization DOM enrichment skipped: {eval_e}")

                # Safeguard: Verify that the page did not redirect or navigate to a cookie/privacy policy page
                final_page_url = getattr(page, "url", "") or ""
                if final_page_url and final_page_url != url and any(k in final_page_url.lower() for k in ["cookie-policy", "privacy-policy", "cookie-popup"]):
                    logger.warning(f"[Browser] Page navigated away to legal policy {final_page_url}. Reloading target {url}...")
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                        await asyncio.sleep(1.0)
                    except Exception:
                        pass

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
