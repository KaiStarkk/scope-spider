import argparse
import logging
import re
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable, List, Optional, Tuple

from backend.domain.models import Company, ExtractionRecord
from backend.domain.utils.companies import dump_companies, load_companies, safe_write_text
from backend.domain.utils.pdf import (
    build_text_snippet,
    camelot_available,
    extract_pdf_text,
    extract_scope_tables,
    keyword_hit_pages,
)
from backend.domain.utils.text import count_tokens


DEFAULT_EXTRACT_DIR = Path("extracted")

# Keywords to locate relevant pages; add variants and common units
KEYWORDS = [
    r"\bscope\s*1\b",
    r"\bscope\s*2\b",
    r"\bscope\s*3\b",
    r"\btco2\b",
    r"\bktco2\b",
    r"\bmtco2\b",
    r"\bkgco2\b",
]
KEYWORD_RE = re.compile("|".join(KEYWORDS), re.IGNORECASE)
SCOPE_TABLE_RE = re.compile(r"\bscope\s*\d+\b", re.IGNORECASE)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract relevant text and tables from ESG PDFs."
    )
    parser.add_argument("companies", type=Path, help="Path to companies.json.")
    parser.add_argument(
        "extract_dir",
        nargs="?",
        type=Path,
        default=DEFAULT_EXTRACT_DIR,
        help="Directory to store snippet artefacts (default: extracted).",
    )
    parser.add_argument("--debug", action="store_true", help="Enable verbose logging.")
    parser.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="Number of parallel worker processes to use (default: 1).",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help=(
            "When extraction fails, clear search/download artefacts so the company can be re-searched."
        ),
    )
    args = parser.parse_args(argv)
    if args.jobs < 1:
        parser.error("--jobs must be >= 1")
    args.companies = args.companies.resolve()
    args.extract_dir = args.extract_dir.resolve()
    return args


def process_company_task(
    company_index: int,
    company_data: dict[str, Any],
    progress_idx: int,
    total_ok: int,
    extract_dir: str,
    debug: bool,
    clean: bool,
) -> Tuple[int, dict[str, Any], List[str], int, bool]:
    company = Company.model_validate(company_data)
    extract_dir_path = Path(extract_dir)
    extract_dir_path.mkdir(parents=True, exist_ok=True)

    logs: List[str] = []
    deleted_files = 0

    def log(message: str) -> None:
        logs.append(message)

    def finalize() -> Tuple[int, dict[str, Any], List[str], int, bool]:
        updated_payload = company.model_dump(mode="json")
        changed = updated_payload != company_data
        return company_index, updated_payload, logs, deleted_files, changed

    def delete_path(path: Path) -> bool:
        nonlocal deleted_files
        try:
            path.unlink()
            deleted_files += 1
            return True
        except FileNotFoundError:
            return False
        except OSError:
            return False

    def force_research_cleanup(
        reason: str, cleanup_paths: Iterable[Optional[Path]]
    ) -> None:
        if not clean:
            return
        seen: set[Path] = set()
        for candidate in cleanup_paths:
            if candidate is None:
                continue
            if candidate in seen:
                continue
            seen.add(candidate)
            delete_path(candidate)
        company.search_record = None
        company.download_record = None
        company.extraction_record = None
        log(
            f"CLEAR {progress_prefix} extract {ticker}: {reason}; forced re-search (--clean)"
        )

    progress_prefix = f"[{progress_idx}/{total_ok}]"
    download_record = company.download_record
    ticker = company.identity.ticker or "UNKNOWN"

    if download_record is None or not download_record.pdf_path:
        company.download_record = None
        company.extraction_record = None
        log(
            f"FAIL {progress_prefix} extract: missing PDF path for {ticker}; download record cleared"
        )
        force_research_cleanup("missing PDF path", [])
        return finalize()

    pdf_path = Path(download_record.pdf_path)
    if not pdf_path.exists() or pdf_path.suffix.lower() != ".pdf":
        company.download_record = None
        company.extraction_record = None
        log(
            f"FAIL {progress_prefix} extract: missing/invalid PDF for {ticker}; download record cleared"
        )
        force_research_cleanup("missing or invalid PDF", [pdf_path])
        return finalize()

    base = pdf_path.stem
    out_txt = extract_dir_path / f"{base}.text.snippet.txt"
    out_tables = extract_dir_path / f"{base}.tables.snippet.txt"

    existing_extraction = company.extraction_record
    if existing_extraction:
        text_ready = (
            existing_extraction.text_path
            and existing_extraction.text_path == str(out_txt)
            and Path(existing_extraction.text_path).exists()
            and existing_extraction.text_token_count > 0
        )
        tables_expected = existing_extraction.table_count > 0
        tables_ready = (
            tables_expected
            and existing_extraction.table_path
            and existing_extraction.table_path == str(out_tables)
            and Path(existing_extraction.table_path).exists()
        )

        if tables_expected and not tables_ready:
            existing_extraction = None
        elif not tables_expected and not text_ready:
            existing_extraction = None
        elif tables_ready or text_ready:
            if debug:
                log(f"SKIP {progress_prefix} extract {ticker}: already exists")
            return finalize()

    pages = extract_pdf_text(pdf_path)
    if not pages:
        company.extraction_record = None
        log(
            f"FAIL {progress_prefix} extract {ticker}: no text extracted; extraction record cleared"
        )
        delete_path(out_txt)
        delete_path(out_tables)
        force_research_cleanup(
            "no extractable text from PDF", [out_txt, out_tables, pdf_path]
        )
        return finalize()

    if sum(len(p) for p in pages) < 200:
        log(f"NOTE {ticker}: low/empty text; OCR may be required for {pdf_path.name}")

    hits = keyword_hit_pages(pages, KEYWORD_RE)
    if not hits:
        company.search_record = None
        company.download_record = None
        company.extraction_record = None
        deleted_in_case = 0
        for orphan in (out_txt, out_tables):
            if delete_path(orphan):
                deleted_in_case += 1
        if delete_path(pdf_path):
            deleted_in_case += 1
        removed_detail = (
            f"removed {deleted_in_case} file{'s' if deleted_in_case != 1 else ''}"
            if deleted_in_case
            else "no files removed"
        )
        log(
            f"CLEAR {progress_prefix} extract {ticker}: no keywords found; cleared search/download/extraction and {removed_detail}"
        )
        return finalize()

    log(f"EXTRACT {progress_prefix} {ticker}: {pdf_path.name}")

    selected_pages = sorted(set(hits))
    table_snippet, table_count, _ = extract_scope_tables(
        pdf_path, selected_pages, pattern=SCOPE_TABLE_RE
    )

    text_path_str: Optional[str] = None
    text_tokens = 0
    chosen_pages: List[int] = []
    table_path_str: Optional[str] = None
    table_token_count = 0

    if table_count > 0 and table_snippet:
        try:
            safe_write_text(out_tables, table_snippet)
            table_path_str = str(out_tables)
            table_token_count = count_tokens(table_snippet)
        except OSError as exc:
            log(
                f"NOTE {progress_prefix} {ticker}: unable to write table snippet ({exc}); continuing without tables"
            )
            table_count = 0
            table_token_count = 0
            table_path_str = None
            delete_path(out_tables)
    else:
        delete_path(out_tables)

    snippet, chosen_pages = build_text_snippet(pages, hits, max_chars=12000)
    if snippet:
        try:
            safe_write_text(out_txt, snippet)
            text_path_str = str(out_txt)
            text_tokens = count_tokens(snippet)
        except OSError as exc:
            company.extraction_record = None
            delete_path(out_txt)
            log(
                f"FAIL {progress_prefix} extract {ticker}: unable to write snippet ({exc}); extraction record cleared"
            )
            if table_count == 0:
                delete_path(out_tables)
            force_research_cleanup(
                "failed to write text snippet", [out_txt, out_tables, pdf_path]
            )
            return finalize()
    else:
        delete_path(out_txt)
        if table_count == 0:
            company.extraction_record = None
            log(
                f"FAIL {progress_prefix} extract {ticker}: matched pages contained no extractable text"
            )
            force_research_cleanup(
                "no extractable text in matched pages", [out_tables, pdf_path]
            )
            return finalize()
        log(
            f"NOTE {progress_prefix} extract {ticker}: no text snippet generated; tables retained"
        )

    company.extraction_record = ExtractionRecord(
        json_path=text_path_str,
        text_token_count=text_tokens,
        snippet_count=len(chosen_pages),
        table_path=table_path_str,
        table_count=table_count,
        table_token_count=table_token_count,
    )

    log(
        f"EXTRACTED {progress_prefix} {ticker}: pages={len(pages)} hits={len(hits)} "
        f"text_tokens={text_tokens} tables={table_count} table_tokens={table_token_count}"
    )

    return finalize()


def main(argv: Optional[List[str]] = None) -> None:
    args = parse_args(argv)
    companies_path = args.companies
    extract_dir = args.extract_dir
    debug = args.debug
    jobs = args.jobs

    extract_dir.mkdir(parents=True, exist_ok=True)

    logging.getLogger("PyPDF2").setLevel(logging.ERROR)
    companies, payload = load_companies(companies_path)

    indexed_candidates: List[tuple[int, Company]] = [
        (idx, company)
        for idx, company in enumerate(companies)
        if company.download_record and company.download_record.pdf_path
    ]
    total_ok = len(indexed_candidates)

    print(
        f"Starting extraction: eligible={total_ok} total={len(companies)}",
        flush=True,
    )
    if not camelot_available():
        print(
            "WARN: camelot not available; table snippets will be skipped. Install camelot-py to enable.",
            flush=True,
        )
    if total_ok == 0:
        print(
            "No items with downloaded PDFs. Run s3_download.py first.",
            flush=True,
        )

    total_deleted = 0

    if jobs == 1 or total_ok <= 1:
        for progress_idx, (company_index, company) in enumerate(
            indexed_candidates, start=1
        ):
            result = process_company_task(
                company_index,
                company.model_dump(mode="json"),
                progress_idx,
                total_ok,
                str(extract_dir),
                debug,
                args.clean,
            )
            (
                _,
                updated_data,
                logs,
                deleted_count,
                changed_flag,
            ) = result
            companies[company_index] = Company.model_validate(updated_data)
            total_deleted += deleted_count
            if changed_flag:
                dump_companies(companies_path, payload, companies)
            for message in logs:
                print(message, flush=True)
    else:
        max_workers = min(jobs, total_ok)
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(
                    process_company_task,
                    company_index,
                    company.model_dump(mode="json"),
                    progress_idx,
                    total_ok,
                    str(extract_dir),
                    debug,
                    args.clean,
                )
                for progress_idx, (company_index, company) in enumerate(
                    indexed_candidates, start=1
                )
            ]

            for future in as_completed(futures):
                exc = future.exception()
                if exc is not None:  # pragma: no cover - runtime guard
                    print(f"ERROR extract worker failed: {exc}", flush=True)
                    continue
                (
                    company_index,
                    updated_data,
                    logs,
                    deleted_count,
                    changed_flag,
                ) = future.result()
                companies[company_index] = Company.model_validate(updated_data)
                total_deleted += deleted_count
                if changed_flag:
                    dump_companies(companies_path, payload, companies)
                for message in logs:
                    print(message, flush=True)

    dump_companies(companies_path, payload, companies)
    print(
        f"Deleted files during extraction: {total_deleted}",
        flush=True,
    )
    print(f"Updated {companies_path}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
