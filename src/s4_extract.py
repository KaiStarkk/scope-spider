import io
import os
import re
import sys
from pathlib import Path
from typing import List, Tuple

from src.models import Company, ExtractionRecord
from src.utils.companies import dump_companies, load_companies
from src.utils.pdf import build_text_snippet, extract_pdf_text, keyword_hit_pages

try:
    import camelot
except ImportError:  # pragma: no cover - runtime dependency
    camelot = None

try:
    import tiktoken
except ImportError:  # pragma: no cover - runtime dependency
    tiktoken = None


DEFAULT_EXTRACT_DIR = Path("extracted")

# Keywords to locate relevant pages; add variants and common units
KEYWORDS = [
    r"\bscope\s*1\b",
    r"\bscope\s*2\b",
    r"\bscope\s*3\b",
    r"\bghg\b",
    r"\bgreenhouse\s+gas",
    r"\bemissions?\b",
    r"\btco2e\b",
    r"\bktco2e\b",
    r"\bmtco2e\b",
]
KEYWORD_RE = re.compile("|".join(KEYWORDS), re.IGNORECASE)
SCOPE_TABLE_RE = re.compile(r"\bscope\s*\d+\b", re.IGNORECASE)
TOKEN_ENCODING_NAME = "cl100k_base"
_ENCODER = None


def count_tokens(text: str) -> int:
    global _ENCODER
    if not text:
        return 0
    if tiktoken is not None:
        try:
            if _ENCODER is None:
                _ENCODER = tiktoken.get_encoding(TOKEN_ENCODING_NAME)
            return len(_ENCODER.encode(text))
        except Exception:
            pass
    # Fallback heuristic: 4 characters per token (rounded)
    return max(0, (len(text) + 3) // 4)


def safe_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def extract_scope_tables(
    pdf_path: Path, hit_pages: List[int]
) -> Tuple[str, int, List[int]]:
    if camelot is None or not hit_pages:
        return "", 0
    page_spec = ",".join(str(page_idx + 1) for page_idx in sorted(set(hit_pages)))
    if not page_spec:
        return "", 0
    tables_collected: List[str] = []
    table_counter = 0
    table_page_numbers: List[int] = []
    table_list = None
    for flavor in ("stream", "lattice"):
        try:
            table_list = camelot.read_pdf(
                str(pdf_path),
                pages=page_spec,
                flavor=flavor,
            )
        except Exception:
            table_list = None
        if table_list and len(table_list) > 0:
            break
    if not table_list:
        return "", 0
    for table in table_list:
        dataframe = table.df
        if dataframe is None or dataframe.empty:
            continue
        csv_buffer = io.StringIO()
        dataframe.to_csv(csv_buffer, index=False, header=False)
        csv_text = csv_buffer.getvalue()
        if not SCOPE_TABLE_RE.search(csv_text):
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


def main():
    # Usage: s4_extract.py <companies.json> [extracted_dir]
    if len(sys.argv) < 2:
        sys.exit("Usage: s4_extract.py <companies.json> [extracted_dir]")
    companies_path = Path(sys.argv[1])
    extract_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_EXTRACT_DIR
    extract_dir.mkdir(parents=True, exist_ok=True)

    companies, payload = load_companies(companies_path)
    # Count candidates with download status ok for progress
    candidates: List[Company] = [
        company
        for company in companies
        if company.download_record and company.download_record.pdf_path
    ]
    total_ok = len(candidates)
    idx_ok = 0
    print(
        f"Starting extraction: eligible={total_ok} total={len(companies)}", flush=True
    )
    if camelot is None:
        print(
            "WARN: camelot not available; table snippets will be skipped. Install camelot-py to enable.",
            flush=True,
        )
    if total_ok == 0:
        print(
            "No items with downloaded PDFs. Run s3_download.py first.",
            flush=True,
        )

    for company in candidates:
        idx_ok += 1
        download_record = company.download_record
        pdf_path = Path(download_record.pdf_path)
        if not pdf_path.exists() or pdf_path.suffix.lower() != ".pdf":
            company.download_record = None
            company.extraction_record = None
            ticker = company.identity.ticker or "UNKNOWN"
            print(
                f"FAIL [{idx_ok}/{total_ok}] extract: missing/invalid PDF for {ticker}; download record cleared",
                flush=True,
            )
            continue
        ticker = company.identity.ticker or "UNKNOWN"
        print(f"EXTRACT [{idx_ok}/{total_ok}] {ticker}: {pdf_path.name}", flush=True)
        base = pdf_path.stem
        out_txt = extract_dir / f"{base}.text.snippet.txt"
        out_tables = extract_dir / f"{base}.tables.snippet.txt"
        existing_extraction = company.extraction_record
        if (
            existing_extraction
            and existing_extraction.text_path == str(out_txt)
            and existing_extraction.text_token_count > 0
            and Path(existing_extraction.text_path).exists()
            and (
                existing_extraction.table_count == 0
                or (
                    existing_extraction.table_path
                    and Path(existing_extraction.table_path).exists()
                )
            )
        ):
            print(
                f"SKIP [{idx_ok}/{total_ok}] extract {ticker}: already exists",
                flush=True,
            )
            continue

        pages = extract_pdf_text(pdf_path)
        if not pages:
            company.extraction_record = None
            print(
                f"FAIL [{idx_ok}/{total_ok}] extract {ticker}: no text extracted; extraction record cleared",
                flush=True,
            )
            continue
        if sum(len(p) for p in pages) < 200:
            print(
                f"NOTE {ticker}: low/empty text; OCR may be required for {pdf_path.name}",
                flush=True,
            )
        hits = keyword_hit_pages(pages, KEYWORD_RE)
        if not hits:
            company.extraction_record = None
            for orphan in (out_txt, out_tables):
                if orphan.exists():
                    orphan.unlink(missing_ok=True)
            print(
                f"MISS [{idx_ok}/{total_ok}] extract {ticker}: pattern not found; skipping extraction",
                flush=True,
            )
            continue
        snippet, chosen_pages = build_text_snippet(pages, hits, max_chars=12000)
        if not snippet:
            company.extraction_record = None
            if out_txt.exists():
                out_txt.unlink(missing_ok=True)
            print(
                f"FAIL [{idx_ok}/{total_ok}] extract {ticker}: matched pages contained no extractable text",
                flush=True,
            )
            continue
        text_pages = [page_index + 1 for page_index in chosen_pages]
        try:
            safe_write_text(out_txt, snippet)
        except OSError as e:
            company.extraction_record = None
            print(
                f"FAIL [{idx_ok}/{total_ok}] extract {ticker}: unable to write snippet ({e}); extraction record cleared",
                flush=True,
            )
            continue

        text_tokens = count_tokens(snippet)
        table_snippet, table_count, table_pages = extract_scope_tables(
            pdf_path, chosen_pages
        )
        table_path_str: str | None = None
        table_token_count = 0
        if table_count > 0 and table_snippet:
            table_token_count = count_tokens(table_snippet)
            try:
                safe_write_text(out_tables, table_snippet)
                table_path_str = str(out_tables)
            except OSError as e:
                print(
                    f"NOTE [{idx_ok}/{total_ok}] {ticker}: unable to write table snippet ({e}); continuing without tables",
                    flush=True,
                )
                table_count = 0
                table_token_count = 0
                table_path_str = None
                table_pages = []
                if out_tables.exists():
                    out_tables.unlink(missing_ok=True)
        elif out_tables.exists():
            out_tables.unlink(missing_ok=True)
        if table_count == 0:
            table_pages = []

        company.extraction_record = ExtractionRecord(
            text_path=str(out_txt),
            text_token_count=text_tokens,
            snippet_page_count=len(chosen_pages),
            table_path=table_path_str,
            table_count=table_count,
            table_token_count=table_token_count,
            text_pages=text_pages,
            table_pages=table_pages,
        )

        print(
            f"EXTRACTED [{idx_ok}/{total_ok}] {ticker}: pages={len(pages)} hits={len(hits)} "
            f"text_tokens={text_tokens} tables={table_count} table_tokens={table_token_count}",
            flush=True,
        )

    dump_companies(companies_path, payload, companies)
    print(f"Updated {companies_path}", flush=True)


if __name__ == "__main__":
    main()
