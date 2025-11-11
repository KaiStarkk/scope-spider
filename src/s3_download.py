import sys
from pathlib import Path
from typing import List, Tuple

from src.models import Company, DownloadRecord
from src.utils.companies import dump_companies, load_companies
from src.utils.documents import normalise_pdf_url
from src.utils.downloads import (
    DownloadError,
    download_pdf,
    find_existing_download,
    hash_url,
    safe_filename_from_url,
)
from src.utils.status import needs_download


DEFAULT_DOWNLOAD_DIR = Path("downloads")


def main():
    if len(sys.argv) < 2:
        sys.exit("Usage: s3_download.py <companies.json> [--all] [--dir downloads]")
    companies_path = Path(sys.argv[1])
    extra_args = sys.argv[2:]
    all_items = "--all" in extra_args
    clean_records = "--clean" in extra_args
    debug = "--debug" in extra_args
    if "--dir" in extra_args:
        idx = extra_args.index("--dir")
        try:
            download_dir = Path(extra_args[idx + 1])
        except IndexError:
            sys.exit("Error: --dir flag requires a path argument")
    else:
        download_dir = DEFAULT_DOWNLOAD_DIR

    companies, payload = load_companies(companies_path)

    sanitised_urls = False
    removed_records = False
    queue_reasons: dict[str, int] = {}
    queued: List[Tuple[Company, str, Path]] = []
    for company in companies:
        identity = company.identity
        ticker = identity.ticker
        search_record = company.search_record

        if not search_record or not search_record.url:
            if debug:
                print(f"SKIP {ticker}: no search record URL available", flush=True)
            continue

        url, is_pdf = normalise_pdf_url(search_record.url)
        if not url:
            if debug:
                print(
                    f"SKIP {ticker}: search URL missing after sanitisation", flush=True
                )
            if clean_records:
                company.search_record = None
                removed_records = True
            continue
        if search_record.url != url:
            search_record.url = url
            sanitised_urls = True
        if not url.lower().startswith(("http://", "https://")):
            if debug:
                print(f"SKIP {ticker}: search URL is not HTTP(S)", flush=True)
            continue
        if not is_pdf:
            note = "search URL does not point to a PDF"
            if clean_records:
                company.search_record = None
                removed_records = True
                if debug:
                    print(f"SKIP {ticker}: {note}; removed via --clean", flush=True)
            else:
                if debug:
                    print(f"SKIP {ticker}: {note} (use --clean to drop it)", flush=True)
            continue

        should_queue = all_items or needs_download(company, verify_path=True)
        if not should_queue:
            record_path = (
                company.download_record.pdf_path
                if company.download_record
                else "unknown"
            )
            if debug:
                print(
                    f"SKIP {ticker}: download already available at {record_path}",
                    flush=True,
                )
            continue

        if all_items and not needs_download(company, verify_path=True):
            reason = "forced via --all"
        else:
            reason = "no existing download artefact"
        queue_reasons[reason] = queue_reasons.get(reason, 0) + 1
        if debug:
            print(f"QUEUE {ticker}: {reason}", flush=True)

        url_hash = hash_url(url)
        base_name = safe_filename_from_url(url)
        file_name = f"{ticker}_{url_hash}_{base_name}".replace(" ", "_")
        expected_path = download_dir / file_name

        if expected_path.exists():
            company.download_record = DownloadRecord(pdf_path=str(expected_path))
            dump_companies(companies_path, payload, companies)
            if debug:
                print(
                    f"FOUND {ticker}: existing expected file {expected_path.name}; linked",
                    flush=True,
                )
            continue

        existing = find_existing_download(ticker, download_dir)
        if existing and existing.exists():
            company.download_record = DownloadRecord(pdf_path=str(existing))
            dump_companies(companies_path, payload, companies)
            if debug:
                print(
                    f"FOUND {ticker}: linked existing download {existing.name}",
                    flush=True,
                )
            continue

        queued.append((company, url, expected_path))

    total = len(queued)
    if total == 0:
        print("No downloads required.", flush=True)
        if sanitised_urls or removed_records:
            dump_companies(companies_path, payload, companies)
            print(f"Updated {companies_path}", flush=True)
        return

    reason_summary = ", ".join(
        f"{count}×{label}" for label, count in sorted(queue_reasons.items())
    )
    print(f"Queued {total} download{'s' if total != 1 else ''} ({reason_summary or 'automatic'}).", flush=True)

    for idx, (company, url, out_path) in enumerate(queued, start=1):
        ticker = company.identity.ticker
        print(f"DOWNLOADING [{idx}/{total}] {ticker}: {url}", flush=True)
        try:
            download_pdf(url, out_path)
        except DownloadError as exc:
            message = str(exc)
            is_not_found = "404" in message or "Not Found" in message
            if is_not_found:
                company.search_record = None
                company.download_record = None
                removed_records = True
                print(
                    f"ERROR [{idx}/{total}] {ticker}: download failed with 404; search record cleared",
                    flush=True,
                )
                dump_companies(companies_path, payload, companies)
            elif clean_records:
                company.download_record = None
                company.search_record = None
                removed_records = True
                print(
                    f"ERROR [{idx}/{total}] {ticker}: download failed ({message}); cleared search/download (--clean)",
                    flush=True,
                )
                dump_companies(companies_path, payload, companies)
            else:
                print(
                    f"ERROR [{idx}/{total}] {ticker}: download failed ({message}); rerun with --clean to clear record",
                    flush=True,
                )
            continue

        company.download_record = DownloadRecord(pdf_path=str(out_path))
        print(f"DOWNLOADED [{idx}/{total}] {ticker}: {out_path.name}", flush=True)
        dump_companies(companies_path, payload, companies)

    if sanitised_urls or removed_records:
        dump_companies(companies_path, payload, companies)

    print(f"Updated {companies_path}", flush=True)


if __name__ == "__main__":
    main()
