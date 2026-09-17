import re
from dataclasses import dataclass


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    page: int
    text: str
    score: float
    method: str


def _terms(text: str) -> set[str]:
    return {term for term in re.findall(r"[a-z0-9]{3,}", text.lower()) if term not in {"the", "and", "for", "with", "from", "this", "that"}}


def retrieve(chunks: list[dict[str, object]], query: str, limit: int = 5) -> list[RetrievedChunk]:
    query_terms = _terms(query)
    scored: list[RetrievedChunk] = []
    for chunk in chunks:
        text = str(chunk["text"])
        text_terms = _terms(text)
        keyword_score = len(query_terms & text_terms) / max(len(query_terms), 1)
        semantic_score = len(query_terms & text_terms) / max(len(text_terms), 1) ** 0.5
        score = keyword_score * 0.7 + semantic_score * 0.3
        if score:
            method = "hybrid" if keyword_score and semantic_score else "keyword"
            scored.append(RetrievedChunk(str(chunk["chunk_id"]), int(chunk["page"]), text, score, method))
    return sorted(scored, key=lambda item: item.score, reverse=True)[:limit]