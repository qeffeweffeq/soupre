from fastapi import FastAPI, Depends, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from database.models import SQLModel, ScrapeJob, Page
from sqlmodel import create_engine, Session, select
from contextlib import asynccontextmanager
import os

# Create downloads directory if it doesn't exist
os.makedirs(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_downloads", "content"), exist_ok=True)
os.makedirs(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_downloads", "media"), exist_ok=True)

sqlite_url = "sqlite:////Users/admin/webapps/soupre.db"
engine = create_engine(sqlite_url, connect_args={"check_same_thread": False})

@asynccontextmanager
async def lifespan(app: FastAPI):
    SQLModel.metadata.create_all(engine)
    yield

app = FastAPI(lifespan=lifespan)

app.mount("/downloads", StaticFiles(directory=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_downloads")), name="downloads")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # In production, restrict to frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_session():
    with Session(engine) as session:
        yield session

@app.post("/api/jobs")
def create_job(target_url: str, background_tasks: BackgroundTasks, session: Session = Depends(get_session)):
    job = ScrapeJob(target_url=target_url)
    session.add(job)
    session.commit()
    session.refresh(job)
    
    # Import the task here to avoid circular imports if any
    from modules.scrapers.engine import run_scrape_job
    
    # Launch scraper in the background
    background_tasks.add_task(run_scrape_job, job.id, target_url, engine)
    return {"job_id": job.id, "status": "started"}

@app.get("/api/jobs")
def get_jobs(session: Session = Depends(get_session)):
    jobs = session.exec(select(ScrapeJob).order_by(ScrapeJob.created_at.desc())).all()
    return jobs

@app.get("/api/jobs/{job_id}/pages")
def get_job_pages(job_id: int, session: Session = Depends(get_session)):
    pages = session.exec(select(Page).where(Page.job_id == job_id)).all()
    return pages

@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: int, session: Session = Depends(get_session)):
    job = session.get(ScrapeJob, job_id)
    if not job:
        return {"status": "not found"}
    
    # Also delete associated pages
    pages = session.exec(select(Page).where(Page.job_id == job_id)).all()
    for page in pages:
        session.delete(page)
        
    session.delete(job)
    session.commit()
    return {"status": "deleted"}
