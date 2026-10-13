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

async def scrape_page(url: str, job_id: int, db_engine, job_dir_name: str = None):
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
            # Keep screenshot_path from Playwright so it gets moved into the job directory
            
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
    folder_name = job_dir_name if job_dir_name else f"{timestamp}_{domain}"
    
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    job_dir = os.path.join(base_dir, "_downloads", folder_name)
    media_dir = job_dir
    content_dir = job_dir
    
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
    db_markdown_path = f"../_downloads/{folder_name}/{safe_title}.md"
    db_screenshot_path = f"../_downloads/{folder_name}/{os.path.basename(screenshot_path)}" if screenshot_path else ""
    
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
                    
                # Deduplicate images by src while keeping alt
                unique_images = []
                seen_srcs = set()
                for alt, src in img_matches:
                    if src.startswith('data:') or src in seen_srcs:
                        continue
                    seen_srcs.add(src)
                    unique_images.append((alt, src))

                logging.info(f"Downloading {len(unique_images)} unique images concurrently...")

                sem = asyncio.Semaphore(15)
                used_filenames = set()
                download_lock = asyncio.Lock()
                downloaded_count = 0

                media_csv_path = os.path.join(media_dir, "media_index.csv")
                with open(media_csv_path, "w", newline='', encoding='utf-8') as f:
                    writer = csv.writer(f)
                    writer.writerow(["Filename", "Alt Text", "Original URL"])

                    async def download_one(alt, src, idx):
                        nonlocal downloaded_count
                        raw_filename = os.path.basename(urlparse(src).path)
                        clean_name = re.sub(r'[^a-zA-Z0-9_.-]', '_', raw_filename)
                        if not clean_name or '.' not in clean_name:
                            clean_name = f"image_{idx}.jpg"

                        # Avoid collisions
                        async with download_lock:
                            if clean_name in used_filenames:
                                clean_name = f"{idx}_{clean_name}"
                            used_filenames.add(clean_name)

                        local_path = os.path.join(images_dir, clean_name)
                        rel_path = f"images/{clean_name}"

                        try:
                            async with sem:
                                img_resp = await context.request.get(src, timeout=15000)
                                body = await img_resp.body()

                            with open(local_path, 'wb') as img_file:
                                img_file.write(body)

                            async with download_lock:
                                writer.writerow([clean_name, alt, src])
                                downloaded_media.append((src, rel_path))
                                downloaded_count += 1
                                if downloaded_count % 50 == 0 or downloaded_count == len(unique_images):
                                    logging.info(f"Downloaded {downloaded_count}/{len(unique_images)} images...")
                        except Exception as e:
                            logging.warning(f"Failed to download {src}: {e}")

                    tasks = [download_one(alt, src, i) for i, (alt, src) in enumerate(unique_images)]
                    await asyncio.gather(*tasks)

                await browser.close()
                
        await download_images_pw()
        
        # Replace URLs in markdown
        for src, rel_path in downloaded_media:
            final_markdown = final_markdown.replace(f"({src})", f"({rel_path})")


    # Extract videos from markdown
    import subprocess
    video_matches = re.findall(r'\[Video\]\((.*?)\)', final_markdown)
    downloaded_videos = set()
    videos_dir = os.path.join(job_dir, 'videos')
    if video_matches:
        os.makedirs(videos_dir, exist_ok=True)
        logging.info(f"Attempting to download {len(set(video_matches))} videos using yt-dlp...")
        for vid_url in set(video_matches):
            logging.info(f"Downloading video: {vid_url}")
            try:
                # Use yt-dlp to download the video
                yt_dlp_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "venv", "bin", "yt-dlp")
                
                # Download best video to videos_dir, wait, vimeo embeds might require referer, yt-dlp usually handles it.
                # Since Pentagram uses player.vimeo.com, yt-dlp handles it automatically.
                subprocess.run([yt_dlp_path, "--add-header", f"Referer: {url}", "-o", os.path.join(videos_dir, "%(title)s.%(ext)s"), vid_url], check=True, capture_output=True)
                downloaded_videos.add(vid_url)
                logging.info(f"Successfully downloaded video: {vid_url}")
            except Exception as e:
                logging.warning(f"Failed to download video {vid_url}: {e}")
                if isinstance(e, subprocess.CalledProcessError):
                    logging.warning(f"yt-dlp error: {e.stderr.decode('utf-8', errors='ignore')}")

    with open(abs_markdown_path, 'w', encoding='utf-8') as f:
        f.write(final_markdown)
        
    with Session(db_engine) as session:
        from sqlmodel import select
        page = session.exec(select(Page).where(Page.job_id == job_id, Page.url == url)).first()
        if not page:
            page = Page(
                job_id=job_id,
                url=url,
            )
            session.add(page)
            
        page.title = page_title
        page.markdown_path = db_markdown_path
        page.screenshot_path = db_screenshot_path
        page.url_path = urlparse(url).path or "/"
        page.url_depth = len([p for p in urlparse(url).path.split("/") if p])
        page.status = "completed"
        
        session.commit()

async def run_scrape_job(
    job_id: int,
    target_url: str,
    db_engine,
    scrape_mode: str = "single",
    max_pages: int = 50,
    delay_min: float = 1.5,
    delay_max: float = 4.5,
    large_threshold: int = 20,
    large_pause_sec: int = 120,
):
    try:
        if scrape_mode == "sitemap":
            from .sitemap_fetcher import discover_sitemap_urls
            from .fetcher import fetch_url_with_browser
            import logging
            from urllib.parse import urlparse
            import os

            from sqlmodel import select
            from database.models import ScrapeJob, Page

            domain = urlparse(target_url).netloc.replace(".", "_")
            job_dir_name = f"job_{job_id}_{domain}"
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))))
            job_dir = os.path.join(base_dir, "_downloads", job_dir_name)

            with Session(db_engine) as s:
                existing_pages = s.exec(select(Page).where(Page.job_id == job_id)).all()
                if existing_pages:
                    logging.info(f"[Job {job_id}] Resuming sitemap job.")
                    pending_urls = [p.url for p in existing_pages if p.status == "pending"]
                    job = s.get(ScrapeJob, job_id)
                    if job:
                        job.total_pages = len(existing_pages)
                        job.status = "running"
                        s.commit()
                else:
                    logging.info(f"[Job {job_id}] Discovering sitemap URLs for {target_url}...")
                    urls = await discover_sitemap_urls(
                        target_url,
                        job_dir=job_dir,
                        max_pages=max_pages
                    )
                    if not urls:
                        job = s.get(ScrapeJob, job_id)
                        if job:
                            job.status = "failed"
                            s.commit()
                        logging.error(f"[Job {job_id}] No sitemap found for {target_url}")
                        return

                    for u in urls:
                        p = Page(
                            job_id=job_id,
                            url=u,
                            title="Pending...",
                            status="pending"
                        )
                        s.add(p)
                    s.commit()

                    job = s.get(ScrapeJob, job_id)
                    if job:
                        job.total_pages = len(urls)
                        job.status = "running"
                        s.commit()
                    
                    pending_urls = urls

            for i, url in enumerate(pending_urls):
                # Large-sitemap pause every large_threshold pages (skip i==0)
                if i > 0 and i % large_threshold == 0 and large_pause_sec > 0:
                    logging.info(
                        f"[Job {job_id}] Large-sitemap pause: {large_pause_sec}s after {i} pages"
                    )
                    await asyncio.sleep(large_pause_sec)
                else:
                    delay = round(random.uniform(delay_min, delay_max), 2)
                    await asyncio.sleep(delay)

                try:
                    await scrape_page(url, job_id, db_engine, job_dir_name=job_dir_name)
                except Exception as e:
                    logging.error(f"[Job {job_id}] Error scraping page {url}: {e}")
                    with Session(db_engine) as s:
                        page = s.exec(select(Page).where(Page.job_id == job_id, Page.url == url)).first()
                        if page:
                            page.status = "failed"
                        else:
                            s.add(Page(job_id=job_id, url=url, status="failed"))
                        s.commit()

                with Session(db_engine) as s:
                    job = s.get(ScrapeJob, job_id)
                    if not job or job.status in ("cancelled", "paused"):
                        logging.info(f"[Job {job_id}] Job {getattr(job, 'status', 'deleted')}. Stopping loop.")
                        break
                    completed_count = len(s.exec(select(Page).where(Page.job_id == job_id, Page.status.in_(["completed", "failed"]))).all())
                    job.status = "running"
                    job.scraped_pages = completed_count
                    s.commit()
        else:
            await scrape_page(target_url, job_id, db_engine)

        from database.models import ScrapeJob, Page
        with Session(db_engine) as session:
            job = session.get(ScrapeJob, job_id)
            if job and job.status not in ("cancelled", "paused", "failed"):
                pending_count = len(session.exec(select(Page).where(Page.job_id == job_id, Page.status == "pending")).all())
                if pending_count == 0:
                    job.status = "completed"
                else:
                    job.status = "paused"
                session.commit()
    except Exception as e:
        logging.error(f"Job {job_id} failed: {e}")
        from database.models import ScrapeJob
        with Session(db_engine) as session:
            job = session.get(ScrapeJob, job_id)
            if job and job.status not in ("cancelled", "paused"):
                job.status = "failed"
                session.commit()
