import os
import json
from pathlib import Path
from typing import Optional, List, Tuple, Dict
import numpy as np
import logging

log = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = Path(__file__).resolve().parent / "cache"
DEFAULT_CACHE_FILE = DEFAULT_CACHE_DIR / "_embeddings.json"


def load_cache(cache_path: Optional[str | Path] = None) -> Dict[str, List[float]]:
    """Loads a vector cache dictionary from disk. Returns a dict mapping key to embedding vector."""
    target_path = Path(cache_path) if cache_path else DEFAULT_CACHE_FILE
    if not target_path.exists():
        return {}

    try:
        with open(target_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        log.error(f"[VEC_STORE] Failed to load vector cache from {target_path}: {exc}")
        return {}


def save_cache(cache: Dict[str, List[float]], cache_path: Optional[str | Path] = None) -> None:
    """Saves a vector cache dictionary to disk in JSON format."""
    target_path = Path(cache_path) if cache_path else DEFAULT_CACHE_FILE
    target_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(cache, f)
        log.info(f"[VEC_STORE] Saved {len(cache)} vectors to {target_path}")
    except Exception as exc:
        log.error(f"[VEC_STORE] Failed to save vector cache to {target_path}: {exc}")


def compute_similarity(vec1: List[float], vec2: List[float]) -> float:
    """
    Compute cosine similarity between two vector embeddings.
    Returns a float between -1.0 and 1.0.
    """
    if not vec1 or not vec2:
        return 0.0

    v1 = np.asarray(vec1, dtype=np.float32)
    v2 = np.asarray(vec2, dtype=np.float32)

    norm1 = np.linalg.norm(v1)
    norm2 = np.linalg.norm(v2)
    denom = norm1 * norm2

    if denom <= 1e-12:
        return 0.0

    cos_sim = float(np.dot(v1, v2) / denom)
    return max(-1.0, min(1.0, cos_sim))


def find_similar(
    target_embedding: List[float],
    cache: Dict[str, List[float]],
    threshold: float = 0.0,
    top_k: Optional[int] = None,
) -> List[Tuple[str, float]]:
    """
    Find items in the vector cache with cosine similarity >= threshold.
    Returns a list of (key, score) tuples sorted descending by score.
    """
    if not target_embedding or not cache:
        return []

    results: List[Tuple[str, float]] = []
    for key, vec in cache.items():
        sim = compute_similarity(target_embedding, vec)
        if sim >= threshold:
            results.append((key, sim))

    results.sort(key=lambda x: x[1], reverse=True)
    if top_k is not None:
        results = results[:top_k]
    return results


def find_similar_tasks(
    target_embedding: List[float],
    cache: Dict[str, List[float]],
    threshold: float = 0.85,
) -> List[Tuple[str, float]]:
    """Backward compatibility helper matching no-db-man task search interface."""
    return find_similar(target_embedding, cache, threshold=threshold)
