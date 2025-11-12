from __future__ import annotations

from contextlib import suppress
from pathlib import Path
from typing import List, Optional, Pattern, Tuple

from PyPDF2 import PdfReader
from PyPDF2.errors import DependencyError, PdfReadError


def extract_pdf_text(pdf_path: Path, *, max_pages: Optional[int] = None) -> List[str]:
    pages: List[str] = []
    try:
        reader = PdfReader(str(pdf_path))
    except DependencyError as exc:
        print(
            f"[pdf] WARN: unable to read {pdf_path} (missing dependency: {exc})",
            flush=True,
        )
        return pages
    except (PdfReadError, OSError):
        return pages
    for page_index, page in enumerate(reader.pages):
        text_content = ""
        with suppress(Exception):
            text_content = page.extract_text() or ""
        pages.append(text_content)
        if max_pages is not None and page_index + 1 >= max_pages:
            break
    return pages


def keyword_hit_pages(pages: List[str], keyword_re: Pattern[str]) -> List[int]:
    hits: List[int] = []
    for idx, text in enumerate(pages):
        if text and keyword_re.search(text):
            hits.append(idx)
    return hits


def build_text_snippet(
    pages: List[str],
    selected_pages: List[int],
    *,
    max_chars: int = 12000,
) -> Tuple[str, List[int]]:
    if not selected_pages:
        return "", []
    chosen: List[int] = []
    seen: set[int] = set()
    for index in selected_pages:
        if index in seen:
            continue
        if 0 <= index < len(pages):
            chosen.append(index)
            seen.add(index)
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
