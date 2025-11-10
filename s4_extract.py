import sys
import json
import re
from pathlib import Path
from typing import List, Dict, Any, Tuple

from PyPDF2 import PdfReader


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


def _extract_pdf_text(pdf_path: Path) -> List[str]:
    pages: List[str] = []
    try:
        reader = PdfReader(str(pdf_path))
        for page in reader.pages:
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            pages.append(text)
    except Exception:
        pages = []
    return pages


def _keyword_hit_pages(pages: List[str]) -> List[int]:
    hits: List[int] = []
    for idx, text in enumerate(pages):
        if text and KEYWORD_RE.search(text):
            hits.append(idx)
    return hits


def _build_snippet(pages: List[str], hits: List[int], window: int = 1, max_chars: int = 12000) -> Tuple[str, List[int]]:
    if not hits:
        # fallback: take first two pages to give some context
        chosen = list(range(min(2, len(pages))))
    else:
        chosen: List[int] = []
        for h in hits:
            for i in range(max(0, h - window), min(len(pages), h + window + 1)):
                if i not in chosen:
                    chosen.append(i)
    buffer: List[str] = []
    for i in chosen:
        page_text = (pages[i] or "").strip()
        if not page_text:
            continue
        header = f"\n\n=== Page {i + 1} ===\n"
        buffer.append(header + page_text)
        if sum(len(b) for b in buffer) >= max_chars:
            break
    return "".join(buffer).strip(), chosen


def main():
    # Usage: s4_extract.py <companies.json> [extracted_dir]
    if len(sys.argv) < 2:
        sys.exit("Usage: s4_extract.py <companies.json> [extracted_dir]")
    companies_path = Path(sys.argv[1])
    extract_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_EXTRACT_DIR
    extract_dir.mkdir(parents=True, exist_ok=True)

    items: List[Dict[str, Any]] = json.loads(companies_path.read_text() or "[]")
    # Count candidates with download status ok for progress
    total_ok = 0
    for it in items:
        rep = it.get("report") or {}
        dl0 = rep.get("download") or {}
        if (dl0.get("status") or "") == "ok":
            total_ok += 1
    idx_ok = 0
    print(f"Starting extraction: eligible={total_ok} total={len(items)}", flush=True)
    if total_ok == 0:
        print("No items with downloaded PDFs (report.download.status=='ok'). Run s3_download.py first.", flush=True)

    for item in items:
        report = item.get("report") or {}
        dl = report.get("download") or {}
        if (dl.get("status") or "") != "ok":
            continue
        idx_ok += 1
        pdf_path = Path(dl.get("path") or "")
        if not pdf_path.exists() or pdf_path.suffix.lower() != ".pdf":
            # If the file is missing or not a PDF, reset the report
            item["report"] = None
            info = item.get("info") or {}
            tkr = info.get("ticker") or "UNKNOWN"
            print(f"FAIL [{idx_ok}/{total_ok}] extract: missing/invalid PDF for {tkr}; report reset to null", flush=True)
            continue
        info = item.get("info") or {}
        ticker = info.get("ticker") or "UNKNOWN"
        print(f"EXTRACT [{idx_ok}/{total_ok}] {ticker}: {pdf_path.name}", flush=True)
        base = pdf_path.stem
        out_txt = extract_dir / f"{base}.snippet.txt"
        if out_txt.exists() and (report.get("extraction") or {}).get("snippet_path"):
            print(f"SKIP [{idx_ok}/{total_ok}] extract {ticker}: already exists", flush=True)
            continue

        pages = _extract_pdf_text(pdf_path)
        if not pages:
            # Hard failure to extract; reset report
            item["report"] = None
            print(f"FAIL [{idx_ok}/{total_ok}] extract {ticker}: no text extracted; report reset to null", flush=True)
            continue
        if sum(len(p) for p in pages) < 200:
            print(f"NOTE {ticker}: low/empty text; OCR may be required for {pdf_path.name}", flush=True)
        hits = _keyword_hit_pages(pages)
        snippet, chosen_pages = _build_snippet(pages, hits)
        try:
            out_txt.write_text(snippet)
        except Exception as e:
            item["report"] = None
            print(f"FAIL [{idx_ok}/{total_ok}] extract {ticker}: unable to write snippet ({e}); report reset to null", flush=True)
            continue

        extraction = {
            "pdf": str(pdf_path),
            "pages": len(pages),
            "hits": hits,
            "chosen_pages": chosen_pages,
            "snippet_path": str(out_txt),
            "chars_total": sum(len(p or "") for p in pages),
            "chars_snippet": len(snippet),
        }
        report["extraction"] = extraction
        item["report"] = report
        print(f"EXTRACTED [{idx_ok}/{total_ok}] {ticker}: pages={extraction['pages']} hits={len(hits)} snippet={extraction['chars_snippet']} chars", flush=True)

    companies_path.write_text(json.dumps(items, ensure_ascii=False, indent=2))
    print(f"Updated {companies_path}", flush=True)


if __name__ == "__main__":
    main()
