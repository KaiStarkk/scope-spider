from __future__ import annotations

import io
import warnings
from contextlib import redirect_stderr, redirect_stdout, suppress
from pathlib import Path
from typing import List, Optional, Pattern, Tuple

from PyPDF2 import PdfReader
from PyPDF2.errors import DependencyError, PdfReadError

try:  # pragma: no cover - optional dependency
    import camelot  # type: ignore
except ImportError:  # pragma: no cover
    camelot = None

CAMEL0T_AVAILABLE = camelot is not None


def camelot_available() -> bool:
    return CAMEL0T_AVAILABLE


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


def extract_scope_tables(
    pdf_path: Path,
    hit_pages: List[int],
    pattern: Optional[Pattern[str]] = None,
) -> Tuple[str, int, List[int]]:
    if camelot is None or not hit_pages:
        return "", 0, []
    page_spec = ",".join(str(page_idx + 1) for page_idx in sorted(set(hit_pages)))
    if not page_spec:
        return "", 0, []
    tables_collected: List[str] = []
    table_counter = 0
    table_page_numbers: List[int] = []
    table_list = None
    for flavor in ("stream", "lattice"):
        with suppress(Exception):
            with warnings.catch_warnings(), redirect_stdout(
                io.StringIO()
            ), redirect_stderr(io.StringIO()):
                warnings.simplefilter("ignore", category=UserWarning)
                table_list = camelot.read_pdf(  # type: ignore[attr-defined]
                    str(pdf_path),
                    pages=page_spec,
                    flavor=flavor,
                )
        if table_list and len(table_list) > 0:
            break
    if not table_list:
        return "", 0, []
    for table in table_list:
        dataframe = table.df
        if dataframe is None or dataframe.empty:
            continue
        csv_buffer = io.StringIO()
        dataframe.to_csv(csv_buffer, index=False, header=False)
        csv_text = csv_buffer.getvalue()
        if not csv_text.strip():
            continue
        if pattern and not pattern.search(csv_text):
            continue
        try:
            page_number = int(str(table.page))
        except (TypeError, ValueError):
            page_number = None
        if page_number is not None:
            table_page_numbers.append(page_number)
        elif hit_pages:
            table_page_numbers.append(hit_pages[0] + 1)
        else:
            table_page_numbers.append(0)
        table_counter += 1
        page_display = table_page_numbers[-1] if table_page_numbers[-1] else table.page
        table_header = f"\n\n=== Table {table_counter} (page {page_display}) ===\n"
        tables_collected.append(table_header + csv_text.strip())
    return "".join(tables_collected).strip(), table_counter, table_page_numbers
