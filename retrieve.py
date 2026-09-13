"""
retrieve.py — shared retrieval engine for chat and audit modes.

Embeds a query with the configured embedding model, compares it against
all stored chunk vectors by cosine similarity, and returns the top-k
chunks with their provenance (source file, char offset, chunk index).

Every consumer of retrieved context gets the same provenance structure,
so citations in chat answers and snippet-validation in the auditor trace
back to the same fields.
"""

import json
from pathlib import Path

import numpy as np
import yaml

from ingest import client, embed_texts

CONFIG = yaml.safe_load(Path("config.yaml").read_text())


def load_store(path: str | None = None) -> dict:
    """Read store.json (chunks + datasets). Raises if ingest hasn't run."""
    p = Path(path or CONFIG["paths"]["store"])
    if not p.exists():
        raise FileNotFoundError(
            f"{p} not found — run `uv run ingest.py <paper>` first."
        )
    return json.loads(p.read_text())


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors (0.0 if either is zero-length)."""
    va, vb = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    na, nb = np.linalg.norm(va), np.linalg.norm(vb)
    if na == 0 or nb == 0:
        return 0.0
    return float(va @ vb / (na * nb))


def retrieve(query: str, k: int | None = None,
             min_score: float | None = None) -> list[dict]:
    """Return the top-k chunks matching the query, best first.

    Each result carries the provenance contract shared by chat and audit:
    {source, offset, index, text, score}. The raw embedding vector is
    dropped — consumers never need it.

    min_score: similarity floor; chunks below it are dropped, so callers
    can detect "nothing relevant in the document" instead of feeding the
    LLM noise. None = keep all top-k regardless of score.
    """
    k = k if k is not None else CONFIG["retrieval"]["top_k"]
    if min_score is None:
        min_score = CONFIG["retrieval"].get("min_score")  # may be absent

    store = load_store()
    chunks = store["chunks"]
    if not chunks:
        return []

    qvec = embed_texts([query])[0]
    scored = [
        {"source": c["source"], "offset": c["offset"], "index": c["index"],
         "text": c["text"], "score": _cosine(qvec, c["vector"])}
        for c in chunks
    ]
    scored.sort(key=lambda c: c["score"], reverse=True)
    if min_score is not None:
        scored = [c for c in scored if c["score"] >= min_score]
    return scored[:k]