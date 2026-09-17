import hashlib
import json
import math
from pathlib import Path

from ..config import settings


def _fallback_embedding(text: str, dimensions: int = 256) -> list[float]:
    vector = [0.0] * dimensions
    for token in text.lower().split():
        index = int(hashlib.sha256(token.encode()).hexdigest(), 16) % dimensions
        vector[index] += 1.0
    length = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / length for value in vector]


def embed(texts: list[str]) -> tuple[list[list[float]], str]:
    try:
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(settings.embedding_model)
        return model.encode(texts, normalize_embeddings=True).tolist(), "sentence-transformers"
    except Exception:
        return [_fallback_embedding(text) for text in texts], "deterministic-fallback"


def build_tender_index(tender_id: int, chunks: list[dict[str, object]]) -> str:
    vectors, provider = embed([str(chunk["text"]) for chunk in chunks])
    index_dir = settings.index_dir
    index_dir.mkdir(parents=True, exist_ok=True)
    index_path = index_dir / f"tender-{tender_id}.json"
    index_path.write_text(json.dumps({"provider": provider, "model": settings.embedding_model, "chunks": chunks, "vectors": vectors}), encoding="utf-8")
    try:
        import faiss

        import numpy as np

        index = faiss.IndexFlatIP(len(vectors[0]))
        index.add(np.array(vectors, dtype="float32"))
        faiss.write_index(index, str(index_dir / f"tender-{tender_id}.faiss"))
    except Exception:
        pass
    return provider