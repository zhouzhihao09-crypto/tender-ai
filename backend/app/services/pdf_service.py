from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader


class PdfExtractionError(ValueError):
    """A PDF cannot provide enough trustworthy text for analysis."""


@dataclass(frozen=True)
class ExtractedPage:
    page_number: int
    text: str


def extract_pdf_pages(path: Path, minimum_characters: int = 20) -> list[ExtractedPage]:
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            try:
                if not reader.decrypt(""):
                    raise PdfExtractionError("This PDF is password-protected and cannot be read.")
            except PdfExtractionError:
                raise
            except Exception as error:
                raise PdfExtractionError("This PDF is password-protected and cannot be read.") from error
        pages = [ExtractedPage(index, (page.extract_text() or "").strip()) for index, page in enumerate(reader.pages, start=1)]
    except PdfExtractionError:
        raise
    except Exception as error:
        raise PdfExtractionError("The PDF could not be read.") from error

    usable_characters = sum(len(page.text) for page in pages)
    if not pages or usable_characters < minimum_characters:
        raise PdfExtractionError("This document appears to be scanned or image-based. Text extraction was not sufficient for analysis.")
    return pages


def chunk_pages(pages: list[ExtractedPage], chunk_size: int = 900, overlap: int = 120) -> list[dict[str, object]]:
    if chunk_size <= overlap:
        raise ValueError("chunk_size must be greater than overlap")
    chunks: list[dict[str, object]] = []
    chunk_index = 0
    for page in pages:
        text = page.text
        start = 0
        while start < len(text):
            end = min(start + chunk_size, len(text))
            chunk_text = text[start:end].strip()
            if chunk_text:
                chunks.append({"chunk_id": f"p{page.page_number}-c{chunk_index}", "page": page.page_number, "text": chunk_text, "chunk_index": chunk_index})
                chunk_index += 1
            if end == len(text):
                break
            start = end - overlap
    return chunks