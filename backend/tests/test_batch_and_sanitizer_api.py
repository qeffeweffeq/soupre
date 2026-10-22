"""
tests/test_batch_and_sanitizer_api.py
Tests for batch raw text/file URL extraction endpoint and target_url sanitization on job creation.
"""

import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlmodel import Session, select, create_engine
from sqlalchemy.pool import StaticPool
from database.models import SQLModel, ScrapeJob
import main
from main import app, get_session

# In-memory SQLite engine with StaticPool so all connections share the memory DB
test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@pytest.fixture(name="client")
def client_fixture():
    SQLModel.metadata.create_all(test_engine)

    def get_test_session():
        with Session(test_engine) as session:
            yield session

    app.dependency_overrides[get_session] = get_test_session
    orig_engine = main.engine
    main.engine = test_engine

    with patch("main._run_job_with_logging"):
        client = TestClient(app)
        yield client

    app.dependency_overrides.clear()
    main.engine = orig_engine
    SQLModel.metadata.drop_all(test_engine)


def test_create_job_sanitizes_target_url(client):
    dirty_url = "https://example.com/articles/intro?id=123&utm_source=twitter&fbclid=abc12345#overview"
    response = client.post("/api/jobs", params={"target_url": dirty_url})
    assert response.status_code == 200
    data = response.json()
    assert "job_id" in data
    job_id = data["job_id"]

    with Session(test_engine) as session:
        job = session.get(ScrapeJob, job_id)
        assert job is not None
        assert job.target_url == "https://example.com/articles/intro?id=123"


def test_batch_upload_json(client):
    payload = {
        "raw_text": "Check https://site-a.com/page?utm_campaign=launch and https://site-b.com/?ref=home, duplicate https://site-a.com/page"
    }
    response = client.post("/api/jobs/batch-upload", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 2
    assert data["urls"] == ["https://site-a.com/page", "https://site-b.com"]


def test_batch_upload_form_data(client):
    response = client.post("/api/jobs/batch-upload", data={"raw_text": "https://service.io/docs?utm_medium=cpc"})
    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 1
    assert data["urls"] == ["https://service.io/docs"]


def test_batch_upload_file(client):
    file_content = b"""
    # URL list
    - https://alpha.com/test?gclid=xyz123
    - https://beta.org/info/
    - https://alpha.com/test
    """
    response = client.post(
        "/api/jobs/batch-upload",
        files={"file": ("urls.md", file_content, "text/markdown")}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 2
    assert data["urls"] == ["https://alpha.com/test", "https://beta.org/info"]


def test_batch_upload_empty(client):
    response = client.post("/api/jobs/batch-upload", json={"raw_text": ""})
    assert response.status_code == 200
    data = response.json()
    assert data == {"count": 0, "urls": []}
