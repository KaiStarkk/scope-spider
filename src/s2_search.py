import sys
import time
from pathlib import Path
from typing import Literal, Optional

from openai import OpenAI
from src.models import DownloadRecord, SearchRecord
from src.utils.companies import dump_companies, load_companies
from src.utils.documents import classify_document_type, infer_year_from_text
from src.utils.query import derive_filename
from src.utils.search_workflow import (
    get_unsearched_companies,
    parse_search_args,
    process_company,
    SearchArgs,
)
from src.utils.downloads import find_existing_download

DEFAULT_DOWNLOAD_DIR = Path("downloads")

def main():
    args: SearchArgs = parse_search_args(sys.argv)
    companies_path = Path(args.path)
    base_dir = companies_path.parent
    default_download_dir = (base_dir / DEFAULT_DOWNLOAD_DIR).resolve()
    companies, payload = load_companies(companies_path)
    restored_records = False
    for company in companies:
        record = company.search_record
        download_record = company.download_record
        if record and record.url:
            continue
        pdf_path: Optional[Path] = None
        if download_record and download_record.pdf_path:
            candidate = Path(download_record.pdf_path)
            if not candidate.is_absolute():
                candidate = (base_dir / candidate).resolve()
            if candidate.exists() and candidate.suffix.lower() == ".pdf":
                pdf_path = candidate
        if pdf_path is None:
            ticker = company.identity.ticker or ""
            existing = find_existing_download(ticker, default_download_dir)
            if existing and existing.exists():
                pdf_path = existing.resolve()
                if not company.download_record or company.download_record.pdf_path != str(
                    pdf_path
                ):
                    company.download_record = DownloadRecord(pdf_path=str(pdf_path))
                    restored_records = True

        if pdf_path is None or not pdf_path.exists() or pdf_path.suffix.lower() != ".pdf":
            continue
        local_url = pdf_path.as_uri()
        existing_title = record.title if record and record.title else ""
        existing_filename = record.filename if record and record.filename else ""
        existing_year = record.year if record and record.year else None
        existing_doc_type = record.doc_type if record and record.doc_type else None

        filename = derive_filename(local_url, existing_filename)
        title = existing_title or filename
        inferred_year = existing_year or infer_year_from_text(title, filename, local_url)
        doc_type: Literal["annual", "sustainability", "other"] | None = (
            existing_doc_type
            if existing_doc_type
            else classify_document_type(title, filename, local_url)
        )
        company.search_record = SearchRecord(
            url=local_url,
            title=title,
            filename=filename,
            year=inferred_year,
            doc_type=doc_type,
        )
        restored_records = True
        print(
            f"INFO: {company.identity.ticker} search URL restored from downloaded file",
            flush=True,
        )

    if restored_records:
        dump_companies(companies_path, payload, companies)

    pending = get_unsearched_companies(companies)

    if not pending:
        print("No companies require search.", flush=True)
        return

    client = OpenAI()
    auto_mode = args.mode == "auto"
    review_queue: list[tuple[int, str]] = []

    for idx, company in enumerate(pending, start=1):
        identity = company.identity
        name = identity.name
        ticker = identity.ticker

        print(f"QUERY [{idx}/{len(pending)}]: {name} ({ticker})", flush=True)

        (
            accepted,
            auto_mode,
            quit_requested,
            record,
            review_reason,
        ) = process_company(
            client=client,
            company=company,
            auto_mode=auto_mode,
            debug=args.debug,
        )

        if quit_requested:
            break

        if review_reason:
            review_queue.append((idx, review_reason))

        if not accepted or record is None:
            if auto_mode:
                time.sleep(5.0)
            continue

        company.search_record = record
        dump_companies(companies_path, payload, companies)

        if auto_mode:
            time.sleep(5.0)

    if review_queue:
        print("\nReview queue:", flush=True)
        for entry_idx, item in review_queue:
            print(f"  - [{entry_idx}] {item}", flush=True)

        for entry_idx, item in review_queue:
            print(f"\nREVIEW [{entry_idx}] {item}", flush=True)
            while True:
                manual_url = input("Provide URL or leave blank to skip: ").strip()
                if not manual_url:
                    print("Skipped.", flush=True)
                    break
                try:
                    filename = derive_filename(manual_url, "")
                    inferred_year = infer_year_from_text(filename, manual_url)
                    doc_type: Literal["annual", "sustainability", "other"] = classify_document_type(
                        filename, filename, manual_url
                    )
                    manual_record = SearchRecord(
                        url=manual_url,
                        title=filename,
                        filename=filename,
                        year=inferred_year,
                        doc_type=doc_type,
                    )
                    pending_company = pending[entry_idx - 1]
                    pending_company.search_record = manual_record
                    dump_companies(companies_path, payload, companies)
                    print("Recorded manual URL.", flush=True)
                    break
                except Exception as exc:  # pylint: disable=broad-except
                    print(f"Invalid input ({exc}); try again.", flush=True)


if __name__ == "__main__":
    main()
