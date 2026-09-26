import argparse
import asyncio
import logging
import sys
import os

# Ensure the database models can be imported correctly
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlmodel import create_engine, Session
from database.models import SQLModel, ScrapeJob
from modules.scrapers.engine import run_scrape_job

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

async def run_cli():
    parser = argparse.ArgumentParser(description="Soupre Scraper CLI")
    parser.add_argument("--url", "-u", type=str, required=True, help="The URL to scrape")
    
    args = parser.parse_args()
    target_url = args.url

    # Database setup
    sqlite_url = "sqlite:////Users/admin/webapps/soupre.db"
    engine = create_engine(sqlite_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    # Create a job in the database
    with Session(engine) as session:
        job = ScrapeJob(target_url=target_url)
        session.add(job)
        session.commit()
        session.refresh(job)
        job_id = job.id

    logging.info(f"Starting scrape job #{job_id} for {target_url}")
    
    # Run the scraper
    await run_scrape_job(job_id, target_url, engine)
    
    logging.info(f"Scrape job #{job_id} completed successfully.")

if __name__ == "__main__":
    asyncio.run(run_cli())
