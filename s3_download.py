import sys
import json
import hashlib
from pathlib import Path
from typing import Dict, Any, List, Tuple
from urllib.parse import urlparse, unquote

import requests


DEFAULT_DOWNLOAD_DIR = Path("downloads")

def _persist_json(path: Path, items: List[Dict[str, Any]]) -> None:
    """Persist the current items list to disk immediately."""
    try:
        path.write_text(json.dumps(items, ensure_ascii=False, indent=2))
    except Exception:
        # Best-effort; caller continues
        pass

def _find_existing_download(ticker: str, download_dir: Path) -> Path | None:
    """Attempt to find an already-downloaded PDF for this ticker.
    Strategy: look for files named like '<TICKER>_*.pdf' and take the most recent."""
    if not ticker:
        return None
    try:
        candidates = sorted(
            download_dir.glob(f"{ticker}_*.pdf"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return candidates[0] if candidates else None
    except Exception:
        return None


def _link_existing_download(item: Dict[str, Any], download_dir: Path) -> bool:
    """If a PDF exists on disk for this ticker, link it into companies.json.
    Returns True if JSON was updated."""
    info = item.get("info") or {}
    ticker = (info.get("ticker") or "").strip()
    if not ticker:
        return False
    existing = _find_existing_download(ticker, download_dir)
    if existing and existing.exists():
        report = item.get("report") or {}
        report.setdefault("download", {})
        report["download"]["path"] = str(existing)
        report["download"]["status"] = "ok"
        report["download"]["error"] = None
        item["report"] = report
        print(f"FOUND {ticker}: linked existing download at {existing.name}", flush=True)
        return True
    return False


def _safe_filename_from_url(url: str) -> str:
    parsed = urlparse(url)
    name = unquote(Path(parsed.path).name or "report.pdf")
    # Guard against querystring-only names
    if not name.lower().endswith(".pdf"):
        if ".pdf" in name.lower():
            # Trim to after last occurrence of .pdf
            idx = name.lower().rfind(".pdf")
            name = name[: idx + 4]
        else:
            name = f"{name}.pdf"
    return name


def _hash_url(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]


def _needs_download(item: Dict[str, Any], all_items: bool) -> Tuple[bool, str]:
    info = item.get("info") or {}
    name = (info.get("name") or "").strip()
    ticker = (info.get("ticker") or "").strip()
    report = item.get("report") or {}
    file = report.get("file") or {}
    url = str(file.get("url") or "").strip()
    filetype = (file.get("filetype") or "").lower()
    data = report.get("data") or {}
    scope_1 = data.get("scope_1")
    scope_2 = data.get("scope_2")

    if not url.startswith("http"):
        return False, f"SKIP {ticker}: no valid URL"
    if filetype != "pdf":
        return False, f"SKIP {ticker}: non-PDF filetype '{filetype or 'unknown'}'"
    if all_items:
        return True, f"QUEUE {ticker}: forced by --all"
    # Minimal heuristic: download if either scope is missing or non-positive
    if scope_1 is None or scope_2 is None or scope_1 <= 0 or scope_2 <= 0:
        return (
            True,
            f"QUEUE {ticker}: missing/placeholder data (s1={scope_1}, s2={scope_2})",
        )
    return False, f"SKIP {ticker}: has data (s1={scope_1}, s2={scope_2})"


def _download_pdf(url: str, out_path: Path) -> Tuple[bool, str]:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; ScopeSpider/1.0; +https://example.org/bot)",
        "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
    }
    try:
        with requests.get(url, headers=headers, timeout=60, stream=True, allow_redirects=True) as r:
            r.raise_for_status()
            content_type = (r.headers.get("Content-Type") or "").lower()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = out_path.with_suffix(out_path.suffix + ".part")
            first_chunk = None
            f = None
            try:
                for chunk in r.iter_content(chunk_size=8192):
                    if not chunk:
                        continue
                    if first_chunk is None:
                        first_chunk = chunk
                        is_pdf_magic = first_chunk[:4] == b"%PDF"
                        # Accept if Content-Type hints PDF OR magic bytes confirm it
                        if ("pdf" not in content_type) and (not is_pdf_magic):
                            # Do not write anything; treat as non-PDF and abort
                            return False, f"not a PDF (Content-Type='{content_type or 'unknown'}', magic={'ok' if is_pdf_magic else 'missing'})"
                        f = tmp_path.open("wb")
                        f.write(first_chunk)
                    else:
                        if f is None:
                            f = tmp_path.open("wb")
                        f.write(chunk)
                if f:
                    f.close()
                if first_chunk is None:
                    # Empty body
                    if tmp_path.exists():
                        tmp_path.unlink(missing_ok=True)
                    return False, "empty response body"
                # Promote temp file
                tmp_path.rename(out_path)
                return True, "ok"
            except Exception as inner_e:
                # Cleanup temp file on any failure
                try:
                    if f:
                        f.close()
                    if tmp_path.exists():
                        tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass
                return False, str(inner_e)
    except Exception as e:
        return False, str(e)


def main():
    if len(sys.argv) < 2:
        sys.exit("Usage: s3_download.py <companies.json> [--all] [--dir downloads]")
    companies_path = Path(sys.argv[1])
    all_items = "--all" in sys.argv[2:]
    if "--dir" in sys.argv:
        idx = sys.argv.index("--dir")
        download_dir = Path(sys.argv[idx + 1])
    else:
        download_dir = DEFAULT_DOWNLOAD_DIR

    items: List[Dict[str, Any]] = json.loads(companies_path.read_text() or "[]")

    queued = []
    changed_any = False
    for item in items:
        needed, reason = _needs_download(item, all_items)
        print(reason, flush=True)
        # Always attempt to link an existing file before deciding to queue
        linked = _link_existing_download(item, download_dir)
        if linked:
            changed_any = True
            _persist_json(companies_path, items)
        if not needed:
            # If we aren't downloading, we may have linked or nothing to do
            continue
        report = item.get("report") or {}
        file = report.get("file") or {}
        url = str(file.get("url") or "").strip()
        info = item.get("info") or {}
        ticker = (info.get("ticker") or "").strip()
        if not url:
            # If no URL, but we just linked an existing file, skip queue
            if linked:
                continue
            continue
        # Skip if already downloaded per companies.json
        dl = (report.get("download") or {})
        dl_path = dl.get("path")
        dl_status = dl.get("status")
        if dl_status == "ok" and dl_path and Path(dl_path).exists():
            print(f"SKIP {ticker}: already downloaded at {dl_path}", flush=True)
            continue
        # If we can determine the target path and it already exists, link and skip queue
        url_hash = _hash_url(url)
        base_name = _safe_filename_from_url(url)
        file_name = f"{ticker}_{url_hash}_{base_name}".replace(" ", "_")
        out_path = download_dir / file_name
        if out_path.exists():
            report.setdefault("download", {})
            report["download"]["path"] = str(out_path)
            report["download"]["status"] = "ok"
            report["download"]["error"] = None
            item["report"] = report
            print(f"FOUND {ticker}: existing expected file {out_path.name}; linked", flush=True)
            changed_any = True
            _persist_json(companies_path, items)
            continue
        queued.append(item)

    total = len(queued)
    for idx, item in enumerate(queued, start=1):
        info = item.get("info") or {}
        name = (info.get("name") or "").strip()
        ticker = (info.get("ticker") or "").strip()
        report = item.get("report") or {}
        file = report.get("file") or {}
        url = str(file.get("url") or "").strip()
        if not url:
            continue
        url_hash = _hash_url(url)
        base_name = _safe_filename_from_url(url)
        # Prefix with ticker and short hash to reduce collisions
        file_name = f"{ticker}_{url_hash}_{base_name}".replace(" ", "_")
        out_path = download_dir / file_name
        if out_path.exists():
            # Avoid re-downloading if already present (race or previous run)
            report.setdefault("download", {})
            report["download"]["path"] = str(out_path)
            report["download"]["status"] = "ok"
            report["download"]["error"] = None
            item["report"] = report
            print(f"FOUND [{idx}/{total}] {ticker}: file already exists, linked {out_path.name}", flush=True)
            continue
        ok, msg = _download_pdf(url, out_path)
        if not ok:
            # Reset the report entirely on download failure
            item["report"] = None
            print(f"ERROR [{idx}/{total}] {ticker}: download failed ({msg}); report reset to null", flush=True)
            changed_any = True
            _persist_json(companies_path, items)
        else:
            # Write back into companies.json under report.download
            report.setdefault("download", {})
            report["download"]["path"] = str(out_path)
            report["download"]["status"] = "ok"
            report["download"]["error"] = None
            item["report"] = report
            print(f"DOWNLOADED [{idx}/{total}] {ticker}: {out_path.name} ({msg})", flush=True)
            changed_any = True
            _persist_json(companies_path, items)

    # Persist updates
    if changed_any:
        companies_path.write_text(json.dumps(items, ensure_ascii=False, indent=2))
    print(f"Updated {companies_path}", flush=True)


if __name__ == "__main__":
    main()
