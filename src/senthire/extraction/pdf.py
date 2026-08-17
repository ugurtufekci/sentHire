"""PDF analysis & linearization (docs/02 Stage 1, path selection).

Path A (born-digital): a real text layer exists → extract text locally with
PyMuPDF (sort=True gives a sane reading order for most one/two-column CVs) and
send *text* to the model. Path B (scanned): no usable text layer → the PDF goes
to the model as a document block (vision).
"""

from dataclasses import dataclass

import pymupdf

MIN_CHARS_PER_PAGE = 40  # below this average, the "text layer" is junk/partial
PAGE_MARKER = "=== Page {n} ==="


@dataclass(frozen=True)
class PdfAnalysis:
    page_count: int
    has_text_layer: bool
    text: str  # linearized text with page markers ("" when no text layer)


def analyze_pdf(data: bytes) -> PdfAnalysis:
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        if doc.needs_pass:
            raise EncryptedPdfError("PDF is password-protected")
        pages: list[str] = []
        total_chars = 0
        for page in doc:
            text = page.get_text(sort=True).strip()
            total_chars += len(text)
            pages.append(text)
        page_count = len(pages)

    has_text = page_count > 0 and (total_chars / page_count) >= MIN_CHARS_PER_PAGE
    if not has_text:
        return PdfAnalysis(page_count=page_count, has_text_layer=False, text="")

    parts = [f"{PAGE_MARKER.format(n=i + 1)}\n{content}" for i, content in enumerate(pages)]
    return PdfAnalysis(page_count=page_count, has_text_layer=True, text="\n\n".join(parts))


class EncryptedPdfError(ValueError):
    pass
