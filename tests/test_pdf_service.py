from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter

from backend.app.services.pdf_service import PdfExtractionError, chunk_pages, extract_pdf_pages


def make_text_pdf(page_texts: list[str]) -> bytes:
    font_index = 3 + (len(page_texts) * 2)
    page_indexes = [3 + (index * 2) for index in range(len(page_texts))]
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", f"<< /Type /Pages /Kids [{' '.join(f'{index} 0 R' for index in page_indexes)}] /Count {len(page_texts)} >>".encode()]
    for index, page_text in enumerate(page_texts):
        page_index = page_indexes[index]
        content_index = page_index + 1
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 {font_index} 0 R >> >> /Contents {content_index} 0 R >>".encode())
        stream = "BT /F1 12 Tf 72 720 Td (" + page_text.replace("(", "\\(").replace(")", "\\)") + ") Tj ET"
        objects.append(f"<< /Length {len(stream.encode())} >>\nstream\n{stream}\nendstream".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    output = b"%PDF-1.4\n"
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(output)); output += f"{index} 0 obj\n".encode() + (obj if isinstance(obj, bytes) else obj.encode()) + b"\nendobj\n"
    xref = len(output); output += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    output += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:])
    output += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return output


def test_page_preservation_and_chunking() -> None:
    pages = [
        type("Page", (), {"page_number": 1, "text": "Submission deadline: 30 September 2026 at 5:00 PM. " * 2})(),
        type("Page", (), {"page_number": 2, "text": "The bidder must submit a completed BOQ and company profile. " * 2})(),
    ]
    chunks = chunk_pages(pages, chunk_size=70, overlap=10)
    assert {chunk["page"] for chunk in chunks} == {1, 2}
    assert all(chunk["chunk_id"].startswith("p") for chunk in chunks)


def test_empty_pdf_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "empty.pdf"
    PdfWriter().write(path)
    with pytest.raises(PdfExtractionError, match="scanned"):
        extract_pdf_pages(path)


def test_invalid_pdf_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"%PDF-not-valid")
    with pytest.raises(PdfExtractionError, match="could not be read"):
        extract_pdf_pages(path)


def test_real_text_pdf_is_extracted_with_page_text(tmp_path: Path) -> None:
    path = tmp_path / "tender.pdf"
    path.write_bytes(make_text_pdf(["Submission deadline: 30 September 2026."]))
    pages = extract_pdf_pages(path)
    assert pages[0].page_number == 1
    assert "Submission deadline" in pages[0].text


def test_real_multi_page_pdf_preserves_each_page(tmp_path: Path) -> None:
    path = tmp_path / "multi-page-tender.pdf"
    path.write_bytes(make_text_pdf(["Closing date: 30 September 2026.", "Insurance certificate is required."]))
    pages = extract_pdf_pages(path)
    assert len(pages) == 2
    assert pages[0].page_number == 1 and "Closing date" in pages[0].text
    assert pages[1].page_number == 2 and "Insurance" in pages[1].text