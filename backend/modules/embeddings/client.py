import hashlib
import logging
import math
import os
import re
import time
from pathlib import Path
from typing import Optional, List

log = logging.getLogger(__name__)

# Fallback vector dimension matching text-embedding-004
DEFAULT_DIM = 768


def _find_credentials_file() -> Optional[str]:
    """Search for GCP credentials.json across common project paths."""
    env_cred = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if env_cred and os.path.exists(env_cred):
        return env_cred

    candidates = [
        Path.cwd() / "credentials.json",
        Path.cwd() / "backend" / "credentials.json",
        Path(__file__).resolve().parent.parent.parent / "credentials.json",
        Path("/Users/admin/webapps/soupre/credentials.json"),
        Path("/Users/admin/webapps/no-db-man/credentials.json"),
    ]
    for cand in candidates:
        if cand.is_file():
            return str(cand)
    return None


def _local_fallback_embedding(text: str, dim: int = DEFAULT_DIM) -> List[float]:
    """
    Deterministic pseudo-semantic embedding fallback using feature hashing & n-grams.
    Ensures tests and offline runs produce consistent, comparable cosine similarities
    without requiring active GCP credentials or internet access.
    """
    if not text:
        return [0.0] * dim

    words = re.findall(r"\b\w+\b", text.lower())
    if not words:
        return [0.0] * dim

    vec = [0.0] * dim
    for idx, word in enumerate(words):
        # Unigram hash
        h = int(hashlib.sha256(word.encode("utf-8")).hexdigest(), 16)
        pos = h % dim
        sign = 1.0 if (h // dim) % 2 == 0 else -1.0
        vec[pos] += sign * (1.0 / math.log2(idx + 3))

        # Bigram hash for phrase context
        if idx > 0:
            bigram = f"{words[idx-1]}_{word}"
            h2 = int(hashlib.sha256(bigram.encode("utf-8")).hexdigest(), 16)
            pos2 = h2 % dim
            sign2 = 1.0 if (h2 // dim) % 2 == 0 else -1.0
            vec[pos2] += sign2 * 0.5

    # L2 normalize
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def get_embedding(
    text: str,
    project: str = "sgd-digital",
    location: str = "europe-west1",
    model: str = "text-embedding-004",
) -> Optional[List[float]]:
    """
    Generate a 768-dimensional vector embedding for the given text using
    Vertex AI text-embedding-004 via the google-genai SDK.
    Falls back gracefully to local deterministic hashing embedding if credentials
    are not found or if the remote call fails.
    """
    if not text or not text.strip():
        log.debug("[EMBED] Skipping embedding generation for empty text.")
        return None

    clean_text = text.strip()

    # Ensure credentials environment variable is set
    cred_file = _find_credentials_file()
    if cred_file and not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = cred_file

    # Attempt Vertex AI API call
    if os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
        try:
            from google import genai

            start_t = time.perf_counter()
            client = genai.Client(
                vertexai=True,
                project=project,
                location=location,
            )
            response = client.models.embed_content(
                model=model,
                contents=clean_text,
            )
            elapsed_ms = (time.perf_counter() - start_t) * 1000
            if response.embeddings and len(response.embeddings) > 0:
                values = response.embeddings[0].values
                log.info(
                    f"[EMBED] Generated {len(values)}-dim vector via Vertex AI in {elapsed_ms:.1f}ms "
                    f"(model={model}, project={project})"
                )
                return values
        except Exception as exc:
            log.warning(f"[EMBED] Vertex AI remote embedding call failed: {exc}. Using local fallback.")

    # Graceful local fallback
    log.debug(f"[EMBED] Using local deterministic fallback embedding for {len(clean_text)} chars.")
    return _local_fallback_embedding(clean_text, dim=DEFAULT_DIM)
