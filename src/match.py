"""Field-match scoring: local embedding similarity between each job and
the user's resume/profile, purely informational -- it is never used to
filter or order jobs. Computed once per job and cached (`match_scored`).

Uses fastembed (small ONNX model, no torch) instead of sentence-transformers
so the dependency stays light enough for a GitHub Actions runner.
"""

import os
import re

from src import config

EMBEDDING_MODEL_NAME = "BAAI/bge-small-en-v1.5"
WORD_PATTERN = re.compile(r"[a-zA-Z][a-zA-Z0-9+.#-]{1,}")

_model = None


def _get_model():
    global _model
    if _model is None:
        from fastembed import TextEmbedding
        _model = TextEmbedding(
            model_name=EMBEDDING_MODEL_NAME,
            cache_dir=str(config.ROOT_DIR / ".fastembed_cache"),
        )
    return _model


def load_resume_text() -> str:
    env_text = os.environ.get("RESUME_TEXT")
    if env_text:
        return env_text
    if config.RESUME_PATH.exists():
        return config.RESUME_PATH.read_text(encoding="utf-8")
    return ""


def _cosine_similarity(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _word_set(text: str) -> set:
    return set(match.group(0).lower() for match in WORD_PATTERN.finditer(text))


def _keyword_overlap(resume_words: set, text: str) -> float:
    if not resume_words:
        return 0.0
    text_words = _word_set(text)
    if not text_words:
        return 0.0
    return len(resume_words & text_words) / len(resume_words)


def score_batch(jobs: list[dict], resume_text: str | None = None) -> dict:
    resume_text = load_resume_text() if resume_text is None else resume_text
    if not resume_text.strip():
        return {"match_scored": 0}

    candidates = [
        job for job in jobs
        if not job.get("gate_dropped") and job.get("match_score") is None
    ]
    if not candidates:
        return {"match_scored": 0}

    model = _get_model()
    resume_embedding = next(iter(model.embed([resume_text])))
    resume_words = _word_set(resume_text)

    texts = [
        job.get("description") or job.get("title", "")
        for job in candidates
    ]
    job_embeddings = model.embed(texts)

    for job, embedding, text in zip(candidates, job_embeddings, texts):
        similarity = _cosine_similarity(resume_embedding, embedding)
        overlap = _keyword_overlap(resume_words, text)
        combined = 0.7 * similarity + 0.3 * overlap
        job["match_score"] = round(max(0.0, min(1.0, combined)) * 100)
        job["match_scored"] = True

    return {"match_scored": len(candidates)}
