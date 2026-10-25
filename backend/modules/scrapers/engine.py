from urllib.parse import urlparse
import asyncio
import random
import logging
import os
import csv
import re
import time
from typing import Optional
from urllib.parse import urljoin
from datetime import datetime
from bs4 import BeautifulSoup
from sqlmodel import Session
from database.models import Page, ScrapeJob, PageTagLink
from modules.enrichment.classifier import classify_page
from modules.config import DOWNLOADS_DIR, BACKEND_DIR, PROJECT_ROOT
from .fetcher import fetch_url_with_browser
from .sitemap_fetcher import determine_content_type
from .parked import check_parked_domain, is_blank_page
from modules.parsers.markdown_converter import convert_content_to_markdown, get_best_image
from modules.extractors.icons_url import extract_best_icon
from modules.extractors.basic_info import (
    extract_about,
    extract_email,
    extract_phone,
    extract_instagram,
    extract_linkedin,
    extract_founded,
    extract_location,
)
from modules.utils.text_utils import sanitize_filename

MIN_DELAY = 1.5
MAX_DELAY = 4.5

def determine_page_hreflang(
    url: str,
    soup: BeautifulSoup,
    sitemap_hreflang: Optional[str] = None
) -> Optional[str]:
    """
    Determine the page language (hreflang) using the fallback cascade:
    1. Sitemap entry hreflang
    2. HTML <head> tags (<html lang>, <link rel="alternate">, <meta og:locale>)
    3. URL path prefix (/it/, /en/, /de/, /fr/)
    """
    # 1. Sitemap entry hreflang
    if sitemap_hreflang:
        clean = sitemap_hreflang.strip().lower()
        if clean and clean != "x-default":
            lang = clean.split("-")[0].split("_")[0]
            if len(lang) == 2:
                return lang

    # 2. HTML head tags
    # 2a. <html lang="...">
    html_tag = soup.find("html")
    if html_tag and html_tag.get("lang"):
        lang = html_tag.get("lang").strip().lower().split("-")[0].split("_")[0]
        if len(lang) == 2:
            return lang

    # 2b. <link rel="alternate" hreflang="..." href="..."> matching url
    for link in soup.find_all("link", rel=lambda r: r and "alternate" in r):
        href = link.get("href", "")
        hreflang = link.get("hreflang", "")
        if hreflang and hreflang.lower() != "x-default":
            clean_hl = hreflang.strip().lower().split("-")[0].split("_")[0]
            if len(clean_hl) == 2:
                if href and urlparse(href).path == urlparse(url).path:
                    return clean_hl

    # 2c. <meta property="og:locale" content="...">
    og_locale = soup.find("meta", property="og:locale") or soup.find("meta", attrs={"name": "og:locale"})
    if og_locale and og_locale.get("content"):
        content = og_locale.get("content").strip().lower()
        lang = content.split("_")[0].split("-")[0]
        if len(lang) == 2:
            return lang

    # 3. URL path prefix (e.g. /it/, /en/, /de/, /fr/)
    parsed_path = urlparse(url).path
    match = re.match(r"^/([a-z]{2})(/|$)", parsed_path.lower())
    if match:
        return match.group(1)

    return None


SHARED_CONTACT_KEYS = (
    "Icon URL",
    "Location",
    "Email",
    "Phone",
    "Instagram",
    "LinkedIn",
    "Founded",
)


def _read_job_contact_csv(job_dir: str) -> dict[str, str]:
    contact_csv_path = os.path.join(job_dir, "contact.csv")
    data: dict[str, str] = {}
    if os.path.exists(contact_csv_path):
        try:
            with open(contact_csv_path, "r", newline="", encoding="utf-8") as f:
                reader = csv.reader(f)
                for idx, row in enumerate(reader):
                    if idx == 0 and row == ["Key", "Value"]:
                        continue
                    if len(row) >= 2 and row[0].strip():
                        data[row[0].strip()] = row[1].strip()
        except Exception:
            pass
    return data


def _update_job_contact_csv(job_dir: str, soup: BeautifulSoup, icon_url: str | None = None) -> dict[str, str]:
    os.makedirs(job_dir, exist_ok=True)
    contact_data = _read_job_contact_csv(job_dir)

    candidates = {
        "Icon URL": icon_url,
        "Location": extract_location(soup),
        "Email": extract_email(soup),
        "Phone": extract_phone(soup),
        "Instagram": extract_instagram(soup),
        "LinkedIn": extract_linkedin(soup),
        "Founded": extract_founded(soup),
    }

    updated = False
    for k in SHARED_CONTACT_KEYS:
        val = candidates.get(k)
        if val is None or not str(val).strip():
            continue
        val_str = str(val).strip()
        existing_val = contact_data.get(k, "")
        # Allow upgrading Google Favicon fallback to a real site icon
        if not existing_val or (k == "Icon URL" and "google.com/s2/favicons" in existing_val and "google.com/s2/favicons" not in val_str):
            contact_data[k] = val_str
            updated = True

    contact_csv_path = os.path.join(job_dir, "contact.csv")
    if updated or not os.path.exists(contact_csv_path):
        with open(contact_csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Key", "Value"])
            for k in SHARED_CONTACT_KEYS:
                if k in contact_data and contact_data[k]:
                    writer.writerow([k, contact_data[k]])

    return contact_data


async def scrape_page(
    url: str,
    job_id: int,
    db_engine,
    job_dir_name: str = None,
    sitemap_hreflang: str = None,
    sitemap_lastmod: str = None,
    sitemap_content_type: str = None,
    max_pages: int = 0,
):
    delay = round(random.uniform(MIN_DELAY, MAX_DELAY), 2)
    logging.info(f"Human-like delay: waiting {delay}s before fetching {url}...")
    await asyncio.sleep(delay)

    domain = urlparse(url).netloc.replace(".", "_")
    folder_name = job_dir_name if job_dir_name else f"{int(time.time())}_{domain}"
    job_dir = os.path.join(DOWNLOADS_DIR, folder_name)
    os.makedirs(job_dir, exist_ok=True)

    # Save initial screenshot directly inside job_dir so it never touches _downloads/ root
    temp_screenshot = os.path.join(job_dir, f"_temp_shot_{int(time.time() * 1000)}.png")
    try:
        html_content, screenshot_path, pw_cookies = await asyncio.wait_for(
            fetch_url_with_browser(
                url,
                take_screenshot=True,
                target_screenshot_path=temp_screenshot
            ),
            timeout=60.0
        )
    except asyncio.TimeoutError:
        logging.warning(f"Playwright timed out after 60s for {url}. Falling back to standard HTTP request...")
        html_content = ""
        screenshot_path = ""
        pw_cookies = []

    # Check if browser failed or hit an unresolved Bot challenge
    needs_fallback = False
    html_lower = (html_content or "").lower()
    if not html_content or len(html_content.strip()) < 500:
        needs_fallback = True
    elif (
        "robot-suspicion" in html_lower
        or "checking the site connection security" in html_lower
        or "checking your browser" in html_lower
        or "<title>just a moment" in html_lower
        or "confirm you are human" in html_lower
    ):
        needs_fallback = True

    if needs_fallback:
        logging.warning(f"Browser failed or hit bot challenge for {url}. Falling back to standard HTTP request...")
        import requests
        try:
            response = await asyncio.to_thread(
                requests.get,
                url,
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'},
                timeout=(6, 12)
            )
            response.raise_for_status()
            if len(response.text.strip()) < 500:
                raise ValueError("Fallback request also returned an empty/challenge page.")
            html_content = response.text
            logging.info(f"Fallback request successful for {url} ({len(html_content)} bytes)")
        except Exception as e:
            logging.error(f"Fallback fetch also failed for {url}: {e}. Proceeding with original browser content...")
            if not html_content or len(html_content.strip()) < 100:
                raise RuntimeError(f"Failed to fetch content for {url}: {e}")

    soup = BeautifulSoup(html_content, 'html.parser')

    # Strip hidden 1pxx1px AI-SEO cloaking blocks immediately so they never pollute metadata or markdown
    for hidden_seo in soup.select('[data-seo-content], [style*="clip-path:inset(50%)"], [style*="clip-path: inset(50%)"]'):
        hidden_seo.decompose()

    # Collect alternate language URLs (e.g. TranslatePress / WPML <link rel="alternate" hreflang="..." href="...">)
    alternate_lang_links: list[tuple[str, str]] = []
    try:
        base_netloc = urlparse(url).netloc.lower().removeprefix("www.")
        seen_alt_urls: set[str] = set()
        for link_tag in soup.find_all("link", rel=lambda r: r and "alternate" in (r if isinstance(r, list) else str(r).lower().split())):
            hl = (link_tag.get("hreflang") or "").strip().lower()
            href = (link_tag.get("href") or "").strip()
            if not hl or hl == "x-default" or not href:
                continue
            alt_full = urljoin(url, href).split("#")[0]
            pu = urlparse(alt_full)
            if pu.scheme in ("http", "https") and pu.netloc.lower().removeprefix("www.") == base_netloc:
                if alt_full not in seen_alt_urls and alt_full != url:
                    seen_alt_urls.add(alt_full)
                    lang_short = re.split(r"[-_]", hl)[0]
                    alternate_lang_links.append((alt_full, lang_short))
    except Exception:
        pass

    page_title = " ".join(soup.title.string.split()) if soup.title and soup.title.string else "Untitled"
    safe_title = sanitize_filename(page_title) or f"Page_{job_id}"

    # Health checks: blank page & parked domain detection
    if is_blank_page(soup):
        logging.warning(f"Blank or nearly empty page detected for {url}")
    full_page_text = soup.get_text(separator=" ", strip=True)
    if check_parked_domain(full_page_text, url):
        logging.warning(f"Parked or spam domain content detected for {url}")

    # Extract best high-resolution icon URL once per job (or upgrade if previous was a Google Favicon fallback)
    existing_contact = _read_job_contact_csv(job_dir)
    cached_icon = existing_contact.get("Icon URL")
    if cached_icon and "google.com/s2/favicons" not in cached_icon:
        icon_url = cached_icon
    else:
        icon_url = await asyncio.to_thread(extract_best_icon, soup, url)

    # Persist shared site/contact fields into job-level contact.csv (instead of every single page)
    _update_job_contact_csv(job_dir, soup, icon_url)

    # Determine page hreflang and content_type
    hreflang = determine_page_hreflang(url, soup, sitemap_hreflang)
    content_type = sitemap_content_type or determine_content_type(url)
    if not content_type or "sitemap" in content_type.lower() or content_type.lower().endswith(".xml"):
        content_type = determine_content_type(url)
    if not content_type or "sitemap" in content_type.lower() or content_type.lower().endswith(".xml"):
        content_type = "pages"

    if job_dir_name:
        images_dir = os.path.join(job_dir, "_images")
        lang_code = hreflang or "default"
        lang_dir = os.path.join(job_dir, lang_code)
        type_dir = os.path.join(lang_dir, content_type)
        os.makedirs(type_dir, exist_ok=True)

        page_dir = os.path.join(type_dir, safe_title)
        if os.path.exists(page_dir):
            existing_meta = os.path.join(page_dir, "metadata.csv")
            if os.path.exists(existing_meta):
                try:
                    with open(existing_meta, "r", encoding="utf-8") as f:
                        if url not in f.read():
                            url_suffix = sanitize_filename(urlparse(url).path.strip("/").replace("/", "_")) or str(int(time.time()))
                            page_dir = os.path.join(type_dir, f"{safe_title}_{url_suffix}")
                except Exception:
                    pass

        folder_page_name = os.path.basename(page_dir)
        img_rel_prefix = "../../../_images"
        db_markdown_path = f"../_downloads/{folder_name}/{lang_code}/{content_type}/{folder_page_name}/{safe_title}.md"
        db_screenshot_path = f"../_downloads/{folder_name}/{lang_code}/{content_type}/{folder_page_name}/{os.path.basename(screenshot_path)}" if screenshot_path else ""
    else:
        images_dir = os.path.join(job_dir, "images")
        page_dir = job_dir
        folder_page_name = ""
        img_rel_prefix = "images"
        db_markdown_path = f"../_downloads/{folder_name}/{safe_title}.md"
        db_screenshot_path = f"../_downloads/{folder_name}/{os.path.basename(screenshot_path)}" if screenshot_path else ""

    os.makedirs(images_dir, exist_ok=True)
    os.makedirs(page_dir, exist_ok=True)

    if screenshot_path and os.path.exists(screenshot_path):
        final_screenshot_name = f"screenshot_{domain}_{int(time.time())}.png"
        new_screenshot_path = os.path.join(page_dir, final_screenshot_name)
        os.rename(screenshot_path, new_screenshot_path)
        screenshot_path = new_screenshot_path
        if job_dir_name:
            db_screenshot_path = f"../_downloads/{folder_name}/{lang_code}/{content_type}/{folder_page_name}/{final_screenshot_name}"
        else:
            db_screenshot_path = f"../_downloads/{folder_name}/{final_screenshot_name}"

    metadata = {
        "URL": url,
        "Title": page_title,
        "Description": extract_about(soup) or "",
        "Keywords": "",
        "Sitemap Last Modified": sitemap_lastmod or "",
        "Scraped At": datetime.now().isoformat()
    }
    kw_tag = soup.find("meta", attrs={"name": "keywords"})
    if kw_tag and kw_tag.get("content"):
        metadata["Keywords"] = kw_tag["content"].strip()

    metadata_csv_path = os.path.join(page_dir, "metadata.csv")
    with open(metadata_csv_path, "w", newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(["Key", "Value"])
        for k, v in metadata.items():
            writer.writerow([k, v])

    main_content = soup.find('main') or soup.find('body')
    try:
        raw_markdown = convert_content_to_markdown(main_content, soup, url, pw_cookies, page_dir)
    except Exception as e:
        logging.error(f"Error converting to markdown: {e}")
        raw_markdown = str(e)
        
    md_table = "## Metadata\n\n| Key | Value |\n|---|---|\n"
    for k, v in metadata.items():
        v_clean = " ".join(str(v).replace("\r\n", " ").replace("\n", " ").replace("\r", " ").split())
        v_safe = v_clean.replace("|", "\\|")
        md_table += f"| **{k}** | {v_safe} |\n"
        
    final_markdown = f"# {page_title}\n\n{md_table}\n\n---\n\n{raw_markdown}"
    
    abs_markdown_path = os.path.join(page_dir, f"{safe_title}.md")
    
    # Extract all image URLs from markdown
    # regex to find markdown images ![alt](url)
    img_matches = re.findall(r'!\[(.*?)\]\((.*?)\)', final_markdown)
    
    downloaded_media = []
    
    if img_matches:
        import requests
        unique_images = []
        seen_srcs = set()
        for alt, src in img_matches:
            if src.startswith('data:') or src in seen_srcs:
                continue
            seen_srcs.add(src)
            unique_images.append((alt, src))

        if unique_images:
            logging.info(f"Downloading {len(unique_images)} unique images concurrently...")
            img_session = requests.Session()
            img_session.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
                "Referer": url,
            })
            if pw_cookies:
                for c in pw_cookies:
                    if isinstance(c, dict) and c.get("name") and c.get("value") is not None:
                        img_session.cookies.set(c["name"], c["value"], domain=c.get("domain", ""))

            sem = asyncio.Semaphore(15)
            used_filenames = set()
            download_lock = asyncio.Lock()
            downloaded_count = 0

            media_csv_path = os.path.join(page_dir, "media_index.csv")
            with open(media_csv_path, "w", newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(["Filename", "Alt Text", "Original URL"])

                def _fetch_img_bytes(target_src: str) -> bytes:
                    resp = img_session.get(target_src, timeout=(5, 12))
                    resp.raise_for_status()
                    return resp.content

                async def download_one(alt, src, idx):
                    nonlocal downloaded_count
                    raw_filename = os.path.basename(urlparse(src).path)
                    clean_name = re.sub(r'[^a-zA-Z0-9_.-]', '_', raw_filename)
                    if not clean_name or '.' not in clean_name:
                        clean_name = f"image_{idx}.jpg"

                    async with download_lock:
                        if clean_name in used_filenames:
                            clean_name = f"{idx}_{clean_name}"
                        used_filenames.add(clean_name)

                    local_path = os.path.join(images_dir, clean_name)
                    rel_path = f"{img_rel_prefix}/{clean_name}"

                    try:
                        async with sem:
                            body = await asyncio.to_thread(_fetch_img_bytes, src)

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

        # Replace URLs in markdown
        for src, rel_path in downloaded_media:
            final_markdown = final_markdown.replace(f"({src})", f"({rel_path})")


    # Extract videos from markdown
    import subprocess
    video_matches = re.findall(r'\[Video\]\((.*?)\)', final_markdown)
    downloaded_videos = set()
    videos_dir = os.path.join(page_dir, 'videos') if job_dir_name else os.path.join(job_dir, 'videos')
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
        page.description = metadata.get("Description") or ""
        page.clean_url = url.split("?")[0].split("#")[0]
        page.icon_url = icon_url
        page.markdown_path = db_markdown_path
        page.screenshot_path = db_screenshot_path
        page.url_path = urlparse(url).path or "/"
        page.url_depth = len([p for p in urlparse(url).path.split("/") if p])
        page.status = "completed"
        page.content_type = content_type
        if hreflang:
            page.hreflang = hreflang
        if sitemap_lastmod:
            page.lastmod = sitemap_lastmod

        # Automatic dual classification (JEV & Embeddings)
        try:
            classification = classify_page(
                title=page_title,
                description=metadata.get("Description", ""),
                body_text=raw_markdown,
                url=url,
                db_session=session,
            )
            page.category_id = classification.category_id
            page.types_id = classification.types_id
            page.genre_id = classification.genre_id
            page.categorization_source = classification.source
            page.categorization_confidence = classification.confidence

            # Flush to ensure page has an ID before creating PageTagLink records
            session.flush()

            if page.id is not None and classification.tag_ids:
                existing_links = session.exec(
                    select(PageTagLink).where(PageTagLink.page_id == page.id)
                ).all()
                existing_tag_ids = {link.tag_id for link in existing_links}
                for tid in classification.tag_ids:
                    if tid not in existing_tag_ids:
                        session.add(PageTagLink(page_id=page.id, tag_id=tid))

            cat_log_msg = (
                f"[Categorization] Category: {classification.category} | "
                f"Type: {classification.type} | "
                f"Source: {classification.source} (confidence: {classification.confidence:.2f})"
            )
            logging.info(cat_log_msg)
            try:
                from modules.core.job_logger import job_log_manager
                job_log_manager.log(job_id, cat_log_msg)
            except Exception:
                pass
        except Exception as exc:
            logging.warning(f"[Categorization] Failed to classify page {url}: {exc}")

        job = session.get(ScrapeJob, job_id)
        if job:
            is_target_match = url.rstrip("/") == (job.target_url or "").rstrip("/")
            if page_title and page_title != "Untitled" and (not job.title or job.title == "Untitled" or is_target_match):
                job.title = page_title
            if icon_url and (not job.icon_url or is_target_match):
                job.icon_url = icon_url

            # Enqueue newly discovered hreflang alternate pages (e.g. TranslatePress / WPML) in sitemap mode
            if job.scrape_mode == "sitemap" and alternate_lang_links:
                existing_urls = set(session.exec(select(Page.url).where(Page.job_id == job_id)).all())
                added_alt = 0
                for alt_url, alt_lang in alternate_lang_links:
                    if alt_url in existing_urls:
                        continue
                    if max_pages and max_pages > 0 and len(existing_urls) >= max_pages:
                        break
                    alt_path = urlparse(alt_url).path or "/"
                    alt_depth = len([part for part in alt_path.split("/") if part])
                    alt_ct = determine_content_type(alt_url)
                    session.add(
                        Page(
                            job_id=job_id,
                            url=alt_url,
                            title=alt_path,
                            url_path=alt_path,
                            url_depth=alt_depth,
                            status="pending",
                            hreflang=alt_lang,
                            content_type=alt_ct,
                            category_id=job.category_id if job else None,
                            types_id=job.types_id if job else None,
                        )
                    )
                    existing_urls.add(alt_url)
                    added_alt += 1
                if added_alt > 0:
                    job.total_pages = len(existing_urls)
                    logging.info(f"[Job {job_id}] Discovered {added_alt} alternate language URL(s) from {url}")

            session.add(job)
        
        session.commit()

    # Clean up any stray .png files in DOWNLOADS_DIR root
    if os.path.exists(DOWNLOADS_DIR):
        for fname in os.listdir(DOWNLOADS_DIR):
            if fname.endswith(".png") and os.path.isfile(os.path.join(DOWNLOADS_DIR, fname)):
                try:
                    os.remove(os.path.join(DOWNLOADS_DIR, fname))
                except Exception:
                    pass

async def run_scrape_job(
    job_id: int,
    target_url: str,
    db_engine,
    scrape_mode: str = "single",
    max_pages: int = 0,
    delay_min: float = 60.0,       # 1 min default
    delay_max: float = 120.0,      # 2 min default
    session_min: int = 30,         # 30 pages min per session
    session_max: int = 50,         # 50 pages max per session
    cooldown_min: float = 180.0,   # 3 min default (180s)
    cooldown_max: float = 300.0,   # 5 min default (300s)
    large_threshold: int = 20,
    large_pause_sec: int = 120,
):
    try:
        from modules.core.job_logger import job_log_manager
        from database.models import ScrapeJob, Page
        from sqlmodel import select

        domain = urlparse(target_url).netloc.replace(".", "_")
        job_dir_name = f"job_{job_id}_{domain}"
        job_dir = os.path.join(DOWNLOADS_DIR, job_dir_name)
        os.makedirs(job_dir, exist_ok=True)
        log_file = os.path.join(job_dir, "job.log")
        job_log_manager.register_job_log_file(job_id, log_file)

        with Session(db_engine) as session:
            job = session.get(ScrapeJob, job_id)
            if job:
                job.status = "running"
                job.log_path = f"_downloads/{job_dir_name}/job.log"
                session.commit()

        # Clean up legacy backend/_downloads if it exists
        legacy_backend_downloads = os.path.join(BACKEND_DIR, "_downloads")
        if os.path.exists(legacy_backend_downloads):
            try:
                import shutil
                shutil.rmtree(legacy_backend_downloads)
            except Exception:
                pass

        # Clean up any stray .png files in DOWNLOADS_DIR root
        if os.path.exists(DOWNLOADS_DIR):
            for fname in os.listdir(DOWNLOADS_DIR):
                if fname.endswith(".png") and os.path.isfile(os.path.join(DOWNLOADS_DIR, fname)):
                    try:
                        os.remove(os.path.join(DOWNLOADS_DIR, fname))
                    except Exception:
                        pass

        if scrape_mode == "sitemap":
            from .sitemap_fetcher import discover_sitemap_urls, SitemapEntry, _strip_www
            from .fetcher import fetch_url_with_browser

            # Clearance cookie acquisition (take_screenshot=False) + immediate root title/icon extraction
            clearance_cookies = None
            root_html = ""
            try:
                root_html, _, clearance_cookies = await fetch_url_with_browser(target_url, take_screenshot=False)
                if root_html:
                    root_soup = BeautifulSoup(root_html, "html.parser")
                    root_title = root_soup.title.string.strip() if root_soup.title and root_soup.title.string else None
                    root_icon = await asyncio.to_thread(extract_best_icon, root_soup, target_url)
                    _update_job_contact_csv(job_dir, root_soup, root_icon)
                    with Session(db_engine) as s:
                        job = s.get(ScrapeJob, job_id)
                        if job:
                            if root_title and root_title != "Untitled":
                                job.title = root_title
                            if root_icon:
                                job.icon_url = root_icon
                            s.add(job)
                            s.commit()
            except Exception as e:
                logging.warning(f"[Job {job_id}] Clearance cookie acquisition error: {e}")

            logging.info(f"[Job {job_id}] Discovering sitemap URLs for {target_url}...")
            entries: list[SitemapEntry] = await discover_sitemap_urls(
                target_url,
                job_dir=job_dir,
                max_pages=max_pages,
                cookies=clearance_cookies
            )

            # Fallback: if no XML sitemap was found but root_html was fetched, extract internal links from homepage
            if not entries and root_html:
                try:
                    root_soup = BeautifulSoup(root_html, "html.parser")
                    base_netloc = _strip_www(urlparse(target_url).netloc)
                    seen_fallback: set[str] = {target_url.split("#")[0]}
                    entries.append(SitemapEntry(url=target_url.split("#")[0], content_type="pages"))
                    for a_tag in root_soup.find_all("a", href=True):
                        href = (a_tag.get("href") or "").strip()
                        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                            continue
                        full_u = urljoin(target_url, href).split("#")[0]
                        pu = urlparse(full_u)
                        if pu.scheme in ("http", "https") and _strip_www(pu.netloc) == base_netloc:
                            ext = pu.path.lower()
                            if not ext.endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.mp4', '.avi', '.pdf', '.zip', '.xml', '.xsl', '.css', '.js')):
                                if full_u not in seen_fallback:
                                    seen_fallback.add(full_u)
                                    entries.append(SitemapEntry(
                                        url=full_u,
                                        content_type=determine_content_type(full_u),
                                    ))
                    logging.info(f"[Job {job_id}] No XML sitemap found; discovered {len(entries)} internal page URLs from homepage.")
                except Exception as fb_e:
                    logging.warning(f"[Job {job_id}] Homepage link fallback failed: {fb_e}")

            with Session(db_engine) as s:
                existing_pages = s.exec(select(Page).where(Page.job_id == job_id)).all()
                pages_by_url = {p.url: p for p in existing_pages}

                for p in existing_pages:
                    if p.status == "failed":
                        p.status = "pending"
                        s.add(p)

                if not entries and not existing_pages:
                    job = s.get(ScrapeJob, job_id)
                    if job:
                        job.status = "failed"
                        s.commit()
                    logging.error(f"[Job {job_id}] No sitemap found for {target_url}")
                    return

                # Synchronize entries into Page table
                for entry in entries:
                    u = entry.url
                    parsed_path = urlparse(u).path
                    depth = len([part for part in (parsed_path or "").split("/") if part])

                    ct = entry.content_type
                    if not ct or "sitemap" in ct.lower() or ct.lower().endswith(".xml"):
                        ct = determine_content_type(u)

                    if u not in pages_by_url:
                        p = Page(
                            job_id=job_id,
                            url=u,
                            title=(parsed_path or u),
                            url_path=(parsed_path or "/"),
                            url_depth=depth,
                            status="pending",
                            hreflang=entry.hreflang,
                            lastmod=entry.lastmod,
                            content_type=ct,
                            category_id=job.category_id if job else None,
                            types_id=job.types_id if job else None,
                        )
                        s.add(p)
                        pages_by_url[u] = p
                    else:
                        p = pages_by_url[u]
                        if entry.hreflang and not p.hreflang:
                            p.hreflang = entry.hreflang
                        if ct:
                            p.content_type = ct

                        # Incremental scrape logic
                        if p.status == "completed":
                            is_newer = False
                            if entry.lastmod and p.lastmod:
                                if entry.lastmod > p.lastmod:
                                    is_newer = True
                            elif entry.lastmod and not p.lastmod:
                                p.lastmod = entry.lastmod

                            # Check disk file
                            file_missing = True
                            if p.markdown_path:
                                rel = p.markdown_path.lstrip("./").lstrip("/")
                                if rel.startswith("_downloads/"):
                                    disk_path = os.path.join(DOWNLOADS_DIR, rel.replace("_downloads/", "", 1))
                                elif rel.startswith("../_downloads/"):
                                    disk_path = os.path.join(DOWNLOADS_DIR, rel.replace("../_downloads/", "", 1))
                                else:
                                    disk_path = os.path.join(job_dir, rel)
                                if os.path.exists(disk_path):
                                    file_missing = False

                            if is_newer or file_missing:
                                p.status = "pending"
                                if entry.lastmod:
                                    p.lastmod = entry.lastmod
                        elif p.status == "failed":
                            p.status = "pending"

                        s.add(p)

                s.commit()

                all_pages = s.exec(select(Page).where(Page.job_id == job_id)).all()
                completed_pages = [p for p in all_pages if p.status == "completed"]

                job = s.get(ScrapeJob, job_id)
                if job:
                    job.total_pages = len(all_pages)
                    job.scraped_pages = len(completed_pages)
                    job.status = "running"
                    s.commit()

                logging.info(
                    f"[Job {job_id}] Total pages: {len(all_pages)}, "
                    f"Already completed: {len(completed_pages)}"
                )

            while True:
                with Session(db_engine) as s:
                    job = s.get(ScrapeJob, job_id)
                    if not job or job.status in ("cancelled", "paused"):
                        logging.info(f"[Job {job_id}] Job {getattr(job, 'status', 'stopped')}. Stopping loop.")
                        break
                    all_pending = s.exec(
                        select(Page)
                        .where(Page.job_id == job_id, Page.status == "pending")
                        .order_by(Page.id.asc())
                    ).all()
                    pending_items = [
                        {
                            "id": p.id,
                            "url": p.url,
                            "hreflang": p.hreflang,
                            "lastmod": p.lastmod,
                            "content_type": p.content_type,
                        }
                        for p in all_pending
                    ]

                if not pending_items:
                    with Session(db_engine) as s:
                        job = s.get(ScrapeJob, job_id)
                        if job and job.status not in ("cancelled", "paused"):
                            job.status = "completed"
                            s.commit()
                    break

                # Pick randomized session size between session_min and session_max (30 to 50 pages, never exceeding 50)
                actual_max = min(50, session_max)
                actual_min = min(session_min, actual_max)
                session_size = min(len(pending_items), random.randint(actual_min, actual_max))
                current_session = pending_items[:session_size]

                logging.info(f"[Job {job_id}] Starting session of {session_size} pages ({len(pending_items)} total pending)...")

                for idx, item in enumerate(current_session):
                    with Session(db_engine) as s:
                        job = s.get(ScrapeJob, job_id)
                        if not job or job.status in ("cancelled", "paused"):
                            break

                    if idx > 0:
                        page_delay = round(random.uniform(delay_min, delay_max), 1)
                        logging.info(f"[Job {job_id}] Human-like delay: {page_delay}s ({page_delay/60:.2f}m) before scraping page {idx + 1}/{session_size}: {item['url']}...")
                        await asyncio.sleep(page_delay)
                    else:
                        logging.info(f"[Job {job_id}] Scraping page {idx + 1}/{session_size}: {item['url']}...")

                    try:
                        await scrape_page(
                            item["url"],
                            job_id,
                            db_engine,
                            job_dir_name=job_dir_name,
                            sitemap_hreflang=item["hreflang"],
                            sitemap_lastmod=item["lastmod"],
                            sitemap_content_type=item["content_type"],
                            max_pages=max_pages,
                        )
                    except Exception as e:
                        logging.error(f"[Job {job_id}] Error scraping page {item['url']}: {e}")
                        with Session(db_engine) as s:
                            p = s.get(Page, item["id"])
                            if p:
                                p.status = "failed"
                                s.commit()

                    with Session(db_engine) as s:
                        job = s.get(ScrapeJob, job_id)
                        if not job or job.status in ("cancelled", "paused"):
                            break
                        completed_count = len(s.exec(select(Page).where(Page.job_id == job_id, Page.status.in_(["completed", "failed"]))).all())
                        job.scraped_pages = completed_count
                        s.commit()

                # After session finishes, check remaining pending pages
                with Session(db_engine) as s:
                    job = s.get(ScrapeJob, job_id)
                    if not job or job.status in ("cancelled", "paused"):
                        break
                    rem_count = len(s.exec(select(Page).where(Page.job_id == job_id, Page.status == "pending")).all())

                if rem_count > 0:
                    # Random cooldown between 3 and 5 minutes (180s to 300s)
                    cooldown_sec = round(random.uniform(cooldown_min, cooldown_max), 1)
                    cooldown_min_disp = cooldown_sec / 60.0
                    logging.info(f"[Job {job_id}] Session completed ({session_size} pages). Cooling down for {cooldown_sec}s ({cooldown_min_disp:.1f} minutes). Will auto-resume next session...")
                    slept = 0.0
                    while slept < cooldown_sec:
                        await asyncio.sleep(2.0)
                        slept += 2.0
                        with Session(db_engine) as s:
                            job = s.get(ScrapeJob, job_id)
                            if not job or job.status in ("cancelled", "paused"):
                                break
        else:
            await scrape_page(target_url, job_id, db_engine, job_dir_name=job_dir_name)

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
