from __future__ import annotations

from contextlib import suppress
from pathlib import Path
from typing import List, Pattern, Tuple

from PyPDF2 import PdfReader
from PyPDF2.errors import PdfReadError


def extract_pdf_text(pdf_path: Path) -> List[str]:
    pages: List[str] = []
    try:
        reader = PdfReader(str(pdf_path))
    except (PdfReadError, OSError):
        return pages
    for page in reader.pages:
        text_content = ""
        with suppress(Exception):
            text_content = page.extract_text() or ""
        pages.append(text_content)
    return pages


def keyword_hit_pages(pages: List[str], keyword_re: Pattern[str]) -> List[int]:
    hits: List[int] = []
    for idx, text in enumerate(pages):
        if text and keyword_re.search(text):
            hits.append(idx)
    return hits


def build_snippet(
    pages: List[str],
    hits: List[int],
    *,
    window: int = 1,
    max_chars: int = 12000,
) -> Tuple[str, List[int]]:
    if not hits:
        chosen = list(range(min(2, len(pages))))
    else:
        chosen: List[int] = []
        for hit in hits:
            for i in range(max(0, hit - window), min(len(pages), hit + window + 1)):
                if i not in chosen:
                    chosen.append(i)
    buffer: List[str] = []
    for index in chosen:
        page_text = (pages[index] or "").strip()
        if not page_text:
            continue
        header = f"\n\n=== Page {index + 1} ===\n"
        buffer.append(header + page_text)
        if sum(len(segment) for segment in buffer) >= max_chars:
            break
    return "".join(buffer).strip(), chosen
