import re
import sys
from pathlib import Path
from typing import List

from src.models import Company, ExtractionRecord
from src.utils.companies import dump_companies, load_companies
from src.utils.pdf import build_snippet, extract_pdf_text, keyword_hit_pages


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
        out_txt = extract_dir / f"{base}.snippet.txt"
        existing_extraction = company.extraction_record
        if (
            out_txt.exists()
            and existing_extraction
            and existing_extraction.json_path
            and Path(existing_extraction.json_path).exists()
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
        snippet, chosen_pages = build_snippet(pages, hits, window=1, max_chars=12000)
        try:
            out_txt.write_text(snippet, encoding="utf-8")
        except OSError as e:
            company.extraction_record = None
            print(
                f"FAIL [{idx_ok}/{total_ok}] extract {ticker}: unable to write snippet ({e}); extraction record cleared",
                flush=True,
            )
            continue

        company.extraction_record = ExtractionRecord(
            json_path=str(out_txt),
            snippet_count=len(chosen_pages),
            table_count=0,
        )

        print(
            f"EXTRACTED [{idx_ok}/{total_ok}] {ticker}: pages={len(pages)} hits={len(hits)} snippet_chars={len(snippet)}",
            flush=True,
        )

    dump_companies(companies_path, payload, companies)
    print(f"Updated {companies_path}", flush=True)


if __name__ == "__main__":
    main()
