import sys
import time
from pathlib import Path
from typing import Literal

from openai import OpenAI
from src.models import SearchRecord
from src.utils.companies import dump_companies, load_companies
from src.utils.documents import classify_document_type, infer_year_from_text
from src.utils.query import derive_filename
from src.utils.search_workflow import (
    get_unsearched_companies,
    parse_search_args,
    process_company,
    SearchArgs,
)

def main():
    args: SearchArgs = parse_search_args(sys.argv)
    companies_path = Path(args.path)
    companies, payload = load_companies(companies_path)
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
