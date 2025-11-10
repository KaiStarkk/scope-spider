import sys
import json
import hashlib
from pathlib import Path
from typing import Dict, Any, List, Tuple
from urllib.parse import urlparse, unquote

import requests


DEFAULT_DOWNLOAD_DIR = Path("downloads")


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
        with requests.get(url, headers=headers, timeout=60, stream=True) as r:
            r.raise_for_status()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with out_path.open("wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
        return True, "ok"
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
    for item in items:
        needed, reason = _needs_download(item, all_items)
        print(reason, flush=True)
        if not needed:
            continue
        report = item.get("report") or {}
        file = report.get("file") or {}
        url = str(file.get("url") or "").strip()
        info = item.get("info") or {}
        ticker = (info.get("ticker") or "").strip()
        if not url:
            continue
        # Skip if already downloaded per companies.json
        dl = (report.get("download") or {})
        dl_path = dl.get("path")
        dl_status = dl.get("status")
        if dl_status == "ok" and dl_path and Path(dl_path).exists():
            print(f"SKIP {ticker}: already downloaded at {dl_path}", flush=True)
            continue
        queued.append(item)

    for item in queued:
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
        ok, msg = _download_pdf(url, out_path)
        if not ok:
            # Reset the report entirely on download failure
            item["report"] = None
            print(f"ERROR {ticker}: download failed ({msg}); report reset to null", flush=True)
        else:
            # Write back into companies.json under report.download
            report.setdefault("download", {})
            report["download"]["path"] = str(out_path)
            report["download"]["status"] = "ok"
            report["download"]["error"] = None
            item["report"] = report
            print(f"DOWNLOADED {ticker}: {out_path.name} ({msg})", flush=True)

    # Persist updates
    companies_path.write_text(json.dumps(items, ensure_ascii=False, indent=2))
    print(f"Updated {companies_path}", flush=True)


if __name__ == "__main__":
    main()
