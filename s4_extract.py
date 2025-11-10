import sys
import json
import re
from pathlib import Path
from typing import List, Dict, Any, Tuple

from PyPDF2 import PdfReader


DEFAULT_DOWNLOAD_DIR = Path("downloads")
DEFAULT_EXTRACT_DIR = Path("extracted")
MANIFEST_FILE = DEFAULT_DOWNLOAD_DIR / "manifest.json"

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


def _read_manifest() -> List[Dict[str, Any]]:
    if MANIFEST_FILE.exists():
        try:
            return json.loads(MANIFEST_FILE.read_text() or "[]")
        except Exception:
            return []
    return []


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
    # Usage: s4_extract.py [downloads_dir] [extracted_dir]
    downloads_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DOWNLOAD_DIR
    extract_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_EXTRACT_DIR
    extract_dir.mkdir(parents=True, exist_ok=True)

    manifest = _read_manifest()
    if not manifest:
        print(f"Warning: No manifest at {MANIFEST_FILE}; scanning PDF files in {downloads_dir}", flush=True)
        pdfs = list(downloads_dir.glob("*.pdf"))
        manifest = [{"ticker": pdf.stem.split("_")[0], "url": "", "path": str(pdf), "status": "ok"} for pdf in pdfs]

    for row in manifest:
        if row.get("status") != "ok":
            continue
        pdf_path = Path(row.get("path", ""))
        if not pdf_path.exists() or pdf_path.suffix.lower() != ".pdf":
            continue
        ticker = row.get("ticker") or "UNKNOWN"
        base = pdf_path.stem
        out_json = extract_dir / f"{base}.json"
        out_txt = extract_dir / f"{base}.snippet.txt"
        if out_txt.exists() and out_json.exists():
            print(f"SKIP extract {ticker}: already exists", flush=True)
            continue

        pages = _extract_pdf_text(pdf_path)
        if not pages or sum(len(p) for p in pages) < 200:
            print(f"NOTE {ticker}: low/empty text; OCR may be required for {pdf_path.name}", flush=True)
        hits = _keyword_hit_pages(pages)
        snippet, chosen_pages = _build_snippet(pages, hits)
        out_txt.write_text(snippet)

        meta = {
            "ticker": ticker,
            "pdf": str(pdf_path),
            "pages": len(pages),
            "hits": hits,
            "chosen_pages": chosen_pages,
            "snippet_path": str(out_txt),
            "chars_total": sum(len(p or "") for p in pages),
            "chars_snippet": len(snippet),
        }
        out_json.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
        print(f"EXTRACTED {ticker}: pages={meta['pages']} hits={len(hits)} snippet={meta['chars_snippet']} chars", flush=True)


if __name__ == "__main__":
    main()
