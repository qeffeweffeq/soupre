from urllib.parse import urlparse
import asyncio
import random
import logging
import os
import csv
import re
from urllib.parse import urljoin
from datetime import datetime
from bs4 import BeautifulSoup
from sqlmodel import Session
from database.models import Page
from .fetcher import fetch_url_with_browser
from modules.parsers.markdown_converter import convert_content_to_markdown
from modules.utils.text_utils import sanitize_filename

MIN_DELAY = 1.5
MAX_DELAY = 4.5

def get_best_image(element):
    """Extract the highest resolution image URL from an element (img or styled div)."""
    best_src = None
    
    # Try srcset or data-srcset
    srcset = element.get('data-srcset') or element.get('srcset')
    if srcset:
        candidates = []
        for part in srcset.split(','):
            part = part.strip()
            if not part: continue
            tokens = part.split(' ')
            url = tokens[0]
            width = 0
            if len(tokens) > 1:
                w_str = tokens[1].replace('w', '').replace('x', '')
                if w_str.isdigit():
                    width = int(w_str)
            candidates.append((width, url))
        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            best_src = candidates[0][1]
            
    if best_src and not best_src.startswith('data:'):
        return best_src
        
    # Try high-res data attributes
    for attr in ['data-large_image', 'data-large', 'data-full-url', 'data-src', 'data-lazy-src', 'src']:
        val = element.get(attr)
        if val and not val.startswith('data:'):
            return val
            
    # Check for background-image in style
    style = element.get('style', '')
    if 'background-image' in style or 'background' in style:
        match = re.search(r'url\([\'"]?(.*?)[\'"]?\)', style)
        if match:
            bg_src = match.group(1)
            if not bg_src.startswith('data:'):
                return bg_src
                
    return None

async def scrape_page(url: str, job_id: int, db_engine):
    delay = round(random.uniform(MIN_DELAY, MAX_DELAY), 2)
    logging.info(f"Human-like delay: waiting {delay}s before fetching {url}...")
    await asyncio.sleep(delay)
    
    html_content, screenshot_path, pw_cookies = await fetch_url_with_browser(url)
    
    # Check if Playwright failed or hit a Cloudflare/Bot challenge
    pw_cookies = []
    needs_fallback = False
    if not html_content:
        needs_fallback = True
    elif len(html_content) < 50000 or 'robot-suspicion' in html_content or 'cloudflare' in html_content.lower() or 'just a moment' in html_content.lower():
        needs_fallback = True
        
    if needs_fallback:
        logging.warning(f"Playwright failed or hit bot challenge for {url}. Falling back to standard HTTP request...")
        import requests
        try:
            response = await asyncio.to_thread(
                requests.get, 
                url, 
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36'},
                timeout=15
            )
            response.raise_for_status()
            html_content = response.text
            screenshot_path = "" 
            
            if len(html_content) < 10000:
                raise ValueError("Fallback request also returned a suspiciously small page (likely a bot challenge).")
                
            logging.info(f"Fallback request successful for {url} ({len(html_content)} bytes)")
        except Exception as e:
            logging.error(f"Fallback fetch also failed for {url}: {e}. Proceeding with original Playwright content...")
        
    soup = BeautifulSoup(html_content, 'html.parser')
    page_title = soup.title.string.strip() if soup.title and soup.title.string else "Untitled"
    safe_title = sanitize_filename(page_title) or f"Page_{job_id}"
    
    domain = urlparse(url).netloc.replace(".", "_")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    folder_name = f"{timestamp}_{domain}"
    
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    job_dir = os.path.join(base_dir, "_downloads", folder_name)
    media_dir = os.path.join(job_dir, "media")
    content_dir = os.path.join(job_dir, "content")
    
    os.makedirs(media_dir, exist_ok=True)
    os.makedirs(content_dir, exist_ok=True)
    
    if screenshot_path and os.path.exists(screenshot_path):
        new_screenshot_path = os.path.join(media_dir, os.path.basename(screenshot_path))
        os.rename(screenshot_path, new_screenshot_path)
        screenshot_path = new_screenshot_path

    metadata = {
        "URL": url,
        "Title": page_title,
        "Description": "",
        "Keywords": "",
        "Scraped At": datetime.now().isoformat()
    }
    desc_tag = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", property="og:description")
    if desc_tag and desc_tag.get("content"):
        metadata["Description"] = desc_tag["content"].strip()
        
    kw_tag = soup.find("meta", attrs={"name": "keywords"})
    if kw_tag and kw_tag.get("content"):
        metadata["Keywords"] = kw_tag["content"].strip()

    metadata_csv_path = os.path.join(content_dir, "metadata.csv")
    with open(metadata_csv_path, "w", newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(["Key", "Value"])
        for k, v in metadata.items():
            writer.writerow([k, v])

    main_content = soup.find('main') or soup.find('body')
    try:
        raw_markdown = convert_content_to_markdown(main_content, soup, url, pw_cookies, media_dir)
    except Exception as e:
        logging.error(f"Error converting to markdown: {e}")
        raw_markdown = str(e)
        
    md_table = "## Metadata\n\n| Key | Value |\n|---|---|\n"
    for k, v in metadata.items():
        v_safe = str(v).replace("|", "\\|")
        md_table += f"| **{k}** | {v_safe} |\n"
        
    final_markdown = f"# {page_title}\n\n{md_table}\n\n---\n\n{raw_markdown}"
    
    abs_markdown_path = os.path.join(content_dir, f"{safe_title}.md")
    db_markdown_path = f"../_downloads/{folder_name}/content/{safe_title}.md"
    db_screenshot_path = f"../_downloads/{folder_name}/media/{os.path.basename(screenshot_path)}" if screenshot_path else ""
    
    # Extract all image URLs from markdown
    import re
    
    import time
    
    # regex to find markdown images ![alt](url)
    img_matches = re.findall(r'!\[(.*?)\]\((.*?)\)', final_markdown)
    
    downloaded_media = []
    
    # Use playwright to download images to bypass Captcha
    images_dir = os.path.join(media_dir, 'images')
    os.makedirs(images_dir, exist_ok=True)
    
    if img_matches:
        logging.info(f"Attempting to download {len(img_matches)} images using Playwright...")
        from playwright.async_api import async_playwright
        from playwright_stealth import Stealth
        
        async def download_images_pw():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36')
                page = await context.new_page()
                await Stealth().apply_stealth_async(page)
                
                # Navigate to base URL to clear Captcha
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                except:
                    await page.wait_for_load_state('networkidle', timeout=15000)
                    
                # Wait for Captcha bypass
                try:
                    await page.wait_for_function("() => !document.body.innerText.includes('Checking the site connection security') && !document.body.innerText.includes('Just a moment')", timeout=15000)
                    await asyncio.sleep(2)
                except:
                    pass
                    
                media_csv_path = os.path.join(media_dir, "media_index.csv")
                with open(media_csv_path, "w", newline='', encoding='utf-8') as f:
                    writer = csv.writer(f)
                    writer.writerow(["Filename", "Alt Text", "Original URL"])
                    
                    for alt, src in img_matches:
                        if src.startswith('data:'):
                            continue
                            
                        img_filename = os.path.basename(urlparse(src).path)
                        img_filename = re.sub(r'[^a-zA-Z0-9_.-]', '_', img_filename)
                        if not img_filename or '.' not in img_filename:
                            img_filename = f"image_{int(time.time())}.jpg"
                            
                        local_path = os.path.join(images_dir, img_filename)
                        rel_path = f"images/{img_filename}"
                        
                        try:
                            async with page.expect_response(src) as response_info:
                                await page.goto(src, timeout=10000)
                            img_resp = await response_info.value
                            body = await img_resp.body()
                            
                            with open(local_path, 'wb') as img_file:
                                img_file.write(body)
                                
                            writer.writerow([img_filename, alt, src])
                            downloaded_media.append((src, rel_path))
                        except Exception as e:
                            logging.warning(f"Failed to download {src}: {e}")
                            
                await browser.close()
                
        await download_images_pw()
        
        # Replace URLs in markdown
        for src, rel_path in downloaded_media:
            final_markdown = final_markdown.replace(f"({src})", f"({rel_path})")

    with open(abs_markdown_path, 'w', encoding='utf-8') as f:
        f.write(final_markdown)
        
    with Session(db_engine) as session:
        page = Page(
            job_id=job_id,
            url=url,
            title=page_title,
            markdown_path=db_markdown_path,
            screenshot_path=db_screenshot_path
        )
        session.add(page)
        session.commit()

async def run_scrape_job(job_id: int, target_url: str, db_engine):
    try:
        await scrape_page(target_url, job_id, db_engine)
        from database.models import ScrapeJob
        with Session(db_engine) as session:
            job = session.get(ScrapeJob, job_id)
            if job:
                job.status = "completed"
                session.commit()
    except Exception as e:
        logging.error(f"Job failed: {e}")
        from database.models import ScrapeJob
        with Session(db_engine) as session:
            job = session.get(ScrapeJob, job_id)
            if job:
                job.status = "failed"
                session.commit()
