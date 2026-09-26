import asyncio
import random
import logging
import os
from bs4 import BeautifulSoup
from sqlmodel import Session
from database.models import Page
from .fetcher import fetch_url_with_browser
from modules.parsers.markdown_converter import convert_content_to_markdown

MIN_DELAY = 1.5
MAX_DELAY = 4.5

async def scrape_page(url: str, job_id: int, db_engine):
    # Sustainable Scraping: Randomized delay to mimic human browsing
    delay = round(random.uniform(MIN_DELAY, MAX_DELAY), 2)
    logging.info(f"Human-like delay: waiting {delay}s before fetching {url}...")
    await asyncio.sleep(delay)
    
    html_content, screenshot_path = await fetch_url_with_browser(url)
    
    if not html_content:
        logging.warning(f"Playwright failed for {url}. Falling back to standard HTTP request...")
        import requests
        try:
            # Run blocking requests in a thread to avoid blocking asyncio loop
            response = await asyncio.to_thread(
                requests.get, 
                url, 
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36'},
                timeout=15
            )
            response.raise_for_status()
            html_content = response.text
            screenshot_path = "" # No screenshot available for fallback
            logging.info(f"Fallback request successful for {url} ({len(html_content)} bytes)")
        except Exception as e:
            logging.error(f"Fallback fetch also failed for {url}: {e}")
            return
        
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # We pass empty config/driver params to markdown converter for now since we refactored
    # We may need to adapt convert_content_to_markdown if it explicitly requires Selenium
    # But looking at websoup code, driver is optional.
    
    # Extract main content using BeautifulSoup
    main_content = soup.find('main') or soup.find('body')
    
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    media_dir = os.path.join(base_dir, "_downloads", "media")
    content_dir = os.path.join(base_dir, "_downloads", "content")
    
    try:
        markdown_content = convert_content_to_markdown(main_content, soup, url, None, media_dir)
    except Exception as e:
        logging.error(f"Error converting to markdown: {e}")
        markdown_content = str(e)
        
    random_id = random.randint(1000, 9999)
    # We use the absolute path for writing, but relative for DB so frontend works
    abs_markdown_path = os.path.join(content_dir, f"{job_id}_{random_id}.md")
    db_markdown_path = f"../_downloads/content/{job_id}_{random_id}.md"
    
    with open(abs_markdown_path, 'w', encoding='utf-8') as f:
        f.write(markdown_content)
        
    with Session(db_engine) as session:
        page = Page(
            job_id=job_id,
            url=url,
            title=soup.title.string if soup.title else "Untitled",
            markdown_path=db_markdown_path,
            screenshot_path=screenshot_path
        )
        session.add(page)
        session.commit()

async def run_scrape_job(job_id: int, target_url: str, db_engine):
    # Async wrapper
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
