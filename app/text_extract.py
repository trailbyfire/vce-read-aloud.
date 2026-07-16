"""Extract plain, paragraph-separated text from uploaded essay files.

Paragraphs are joined with a blank line so the rest of the app can split on
"\n\n" consistently regardless of source format.
"""

from __future__ import annotations

from pathlib import Path


class ExtractionError(Exception):
    pass


def extract_text(filename: str, data: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".txt":
        return _extract_txt(data)
    if suffix == ".docx":
        return _extract_docx(data)
    if suffix == ".pdf":
        return _extract_pdf(data)
    raise ExtractionError(
        f"Unsupported file type '{suffix}'. Please use .docx, .pdf, or .txt."
    )


def _extract_txt(data: bytes) -> str:
    for encoding in ("utf-8", "utf-16", "cp1252", "latin-1"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ExtractionError("Could not decode text file (unknown encoding).")
    paragraphs = [p.strip() for p in text.replace("\r\n", "\n").split("\n\n")]
    paragraphs = [p for p in paragraphs if p]
    if not paragraphs:
        # Fall back to single-newline splitting for plain, unwrapped text files.
        paragraphs = [p.strip() for p in text.splitlines() if p.strip()]
    return "\n\n".join(paragraphs)


def _extract_docx(data: bytes) -> str:
    try:
        import io

        from docx import Document
    except ImportError as exc:
        raise ExtractionError(
            "python-docx is not installed. Run: pip install -r requirements.txt"
        ) from exc

    try:
        document = Document(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001 - surface as a friendly extraction error
        raise ExtractionError(f"Could not read .docx file: {exc}") from exc

    paragraphs = [p.text.strip() for p in document.paragraphs]
    paragraphs = [p for p in paragraphs if p]
    if not paragraphs:
        raise ExtractionError("No readable text found in this .docx file.")
    return "\n\n".join(paragraphs)


def _extract_pdf(data: bytes) -> str:
    try:
        import io

        from pypdf import PdfReader
    except ImportError as exc:
        raise ExtractionError(
            "pypdf is not installed. Run: pip install -r requirements.txt"
        ) from exc

    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        raise ExtractionError(f"Could not read .pdf file: {exc}") from exc

    raw_paragraphs: list[str] = []
    for page in reader.pages:
        page_text = page.extract_text() or ""
        for block in page_text.split("\n\n"):
            block = " ".join(line.strip() for line in block.splitlines() if line.strip())
            if block:
                raw_paragraphs.append(block)

    if not raw_paragraphs:
        raise ExtractionError(
            "No extractable text found in this PDF. It may be a scanned image "
            "without a text layer."
        )
    return "\n\n".join(raw_paragraphs)
