import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
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


def parse_args(argv: List[str]) -> Tuple[Path, Path, bool, bool, bool, int]:
    if len(argv) < 2:
        raise SystemExit(
            "Usage: s3_download.py <companies.json> [--all] [--dir downloads] [--clean] [--debug] [--jobs N]"
        )
    companies_path = Path(argv[1])
    extra_args = argv[2:]
    all_items = "--all" in extra_args
    clean_records = "--clean" in extra_args
    debug = "--debug" in extra_args
    jobs = 1
    if "--jobs" in extra_args:
        idx_jobs = extra_args.index("--jobs")
        try:
            jobs = int(extra_args[idx_jobs + 1])
        except (IndexError, ValueError):
            raise SystemExit("Error: --jobs flag requires an integer value")
        if jobs < 1:
            raise SystemExit("Error: --jobs must be >= 1")
    if "--dir" in extra_args:
        idx = extra_args.index("--dir")
        try:
            download_dir = Path(extra_args[idx + 1])
        except IndexError:
            raise SystemExit("Error: --dir flag requires a path argument")
    else:
        download_dir = DEFAULT_DOWNLOAD_DIR
    return companies_path, download_dir, all_items, clean_records, debug, jobs


def main():
    companies_path, download_dir, all_items, clean_records, debug, jobs = parse_args(
        sys.argv
    )

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

    def handle_failure(
        idx: int,
        ticker: str,
        message: str,
        *,
        is_not_found: bool,
        company_ref: Company,
    ) -> None:
        nonlocal removed_records
        company_ref.download_record = None
        removed_records = True
        if is_not_found:
            if clean_records:
                company_ref.search_record = None
                print(
                    f"ERROR [{idx}/{total}] {ticker}: download failed with 404; cleared search/download (--clean)",
                    flush=True,
                )
            else:
                print(
                    f"ERROR [{idx}/{total}] {ticker}: download failed with 404; rerun with --clean to clear search record",
                    flush=True,
                )
        elif clean_records:
            company_ref.search_record = None
            print(
                f"ERROR [{idx}/{total}] {ticker}: download failed ({message}); cleared search/download (--clean)",
                flush=True,
            )
        else:
            print(
                f"ERROR [{idx}/{total}] {ticker}: download failed ({message}); rerun with --clean to clear search record",
                flush=True,
            )

    if jobs == 1 or total == 1:
        for idx, (company, url, out_path) in enumerate(queued, start=1):
            ticker = company.identity.ticker
            print(f"DOWNLOADING [{idx}/{total}] {ticker}: {url}", flush=True)
            try:
                download_pdf(url, out_path)
            except DownloadError as exc:
                message = str(exc)
                is_not_found = "404" in message or "Not Found" in message
                handle_failure(
                    idx,
                    ticker,
                    message,
                    is_not_found=is_not_found,
                    company_ref=company,
                )
                dump_companies(companies_path, payload, companies)
                continue

            company.download_record = DownloadRecord(pdf_path=str(out_path))
            print(f"DOWNLOADED [{idx}/{total}] {ticker}: {out_path.name}", flush=True)
            dump_companies(companies_path, payload, companies)
    else:
        max_workers = min(jobs, total)
        out_path_map: dict[int, Path] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {}
            for idx, (company, url, out_path) in enumerate(queued, start=1):
                ticker = company.identity.ticker
                print(f"QUEUED [{idx}/{total}] {ticker}: {url}", flush=True)

                def task(u: str, dest: Path):
                    download_pdf(u, dest)

                future = executor.submit(task, url, out_path)
                future_map[future] = (idx, company, url, out_path)
                out_path_map[idx] = out_path

            for future in as_completed(future_map):
                idx, company, url, out_path = future_map[future]
                ticker = company.identity.ticker
                try:
                    future.result()
                except DownloadError as exc:
                    message = str(exc)
                    is_not_found = "404" in message or "Not Found" in message
                    handle_failure(
                        idx,
                        ticker,
                        message,
                        is_not_found=is_not_found,
                        company_ref=company,
                    )
                    dump_companies(companies_path, payload, companies)
                    continue
                except Exception as exc:  # pragma: no cover - safety net
                    message = str(exc)
                    handle_failure(
                        idx,
                        ticker,
                        message,
                        is_not_found=False,
                        company_ref=company,
                    )
                    dump_companies(companies_path, payload, companies)
                    continue

                company.download_record = DownloadRecord(pdf_path=str(out_path))
                print(
                    f"DOWNLOADED [{idx}/{total}] {ticker}: {out_path.name}",
                    flush=True,
                )
                dump_companies(companies_path, payload, companies)

    if sanitised_urls or removed_records:
        dump_companies(companies_path, payload, companies)

    print(f"Updated {companies_path}", flush=True)


if __name__ == "__main__":
    main()
