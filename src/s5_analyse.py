from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from openai import OpenAI

from src.utils.companies import dump_companies, load_companies
from src.utils.verification import (
    advise_on_failure,
    attach_file_to_vector_store,
    get_or_create_vector_store,
    needs_verification,
    parse_data_fuzzy,
    parse_data_filesearch,
    parse_data_local,
    update_company_emissions,
)

VECTOR_STORE_ID_FILE = Path(".vector_store_id")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="s5_analyse",
        description="Analyse extracted snippets to confirm Scope 1/2 emissions.",
    )
    parser.add_argument("companies", help="Path to companies.json")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Analyse all companies regardless of existing emissions.",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Attempt offline fuzzy matching before calling the OpenAI API.",
    )
    parser.add_argument(
        "--local-only",
        action="store_true",
        help=(
            "Skip remote fallbacks (for --local this keeps only fuzzy search; "
            "otherwise only snippet parsing)."
        ),
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.7,
        help="Minimum confidence required to accept a result (default: 0.7).",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    companies_path = Path(args.companies).expanduser().resolve()

    companies, payload = load_companies(companies_path)

    changed = False
    vector_store_id: Optional[str] = None
    client: Optional[OpenAI] = None
    threshold = max(0.0, min(1.0, args.threshold))

    def ensure_client() -> OpenAI:
        nonlocal client
        if client is None:
            client = OpenAI()
        return client

    candidates = [c for c in companies if needs_verification(c, args.all)]
    total_need = len(candidates)
    idx_need = 0

    for company in companies:
        if not needs_verification(company, args.all):
            continue

        idx_need += 1
        identity = company.identity
        ticker = identity.ticker

        download = company.download_record
        if not download or not download.pdf_path:
            print(
                f"SKIP [{idx_need}/{total_need}] {ticker}: no downloaded PDF recorded",
                flush=True,
            )
            continue

        extraction = company.extraction_record
        if not extraction or not extraction.json_path:
            print(
                f"SKIP [{idx_need}/{total_need}] {ticker}: no snippet found, run s4_extract.py",
                flush=True,
            )
            continue

        snippet_path = Path(extraction.json_path)
        if not snippet_path.exists():
            print(
                f"SKIP [{idx_need}/{total_need}] {ticker}: snippet path missing ({snippet_path})",
                flush=True,
            )
            continue

        snippet_text = snippet_path.read_text(encoding="utf-8")
        if len(snippet_text.strip()) < 50:
            print(
                f"NOTE [{idx_need}/{total_need}] {ticker}: snippet too small; results may be unreliable",
                flush=True,
            )

        if args.local:
            parsed_fuzzy = parse_data_fuzzy(snippet_text)
            if (
                parsed_fuzzy
                and parsed_fuzzy.scope_1
                and parsed_fuzzy.scope_2
                and parsed_fuzzy.scope_1 > 0
                and parsed_fuzzy.scope_2 > 0
            ):
                if parsed_fuzzy.confidence >= threshold:
                    if update_company_emissions(company, parsed_fuzzy):
                        method_suffix = (
                            f", method={parsed_fuzzy.scope_2_method}"
                            if parsed_fuzzy.scope_2_method
                            else ""
                        )
                        print(
                            f"ANALYSED [{idx_need}/{total_need}] {ticker}: fuzzy{method_suffix} "
                            f"(conf={parsed_fuzzy.confidence:.2f})",
                            flush=True,
                        )
                        changed = True
                        continue
                else:
                    print(
                        f"LOW CONF [{idx_need}/{total_need}] {ticker}: fuzzy conf={parsed_fuzzy.confidence:.2f} "
                        f"< {threshold:.2f}; continuing",
                        flush=True,
                    )
            else:
                print(
                    f"MISS [{idx_need}/{total_need}] {ticker}: fuzzy search did not yield usable values",
                    flush=True,
                )
            if args.local_only:
                print(
                    f"FAIL [{idx_need}/{total_need}] {ticker}: fuzzy mode failed; skipping due to --local-only",
                    flush=True,
                )
                continue

        parsed_llm = parse_data_local(ensure_client(), snippet_text)
        if (
            parsed_llm
            and parsed_llm.scope_1
            and parsed_llm.scope_2
            and parsed_llm.scope_1 > 0
            and parsed_llm.scope_2 > 0
        ):
            if parsed_llm.confidence >= threshold:
                if update_company_emissions(company, parsed_llm):
                    method_suffix = (
                        f", method={parsed_llm.scope_2_method}"
                        if parsed_llm.scope_2_method
                        else ""
                    )
                    print(
                        f"ANALYSED [{idx_need}/{total_need}] {ticker}: ai-local{method_suffix} "
                        f"(conf={parsed_llm.confidence:.2f})",
                        flush=True,
                    )
                    changed = True
                    continue
            else:
                print(
                    f"LOW CONF [{idx_need}/{total_need}] {ticker}: ai-local conf={parsed_llm.confidence:.2f} "
                    f"< {threshold:.2f}; trying file_search",
                    flush=True,
                )
        else:
            print(
                f"MISS [{idx_need}/{total_need}] {ticker}: ai-local parse did not yield usable values; trying file_search",
                flush=True,
            )

        if args.local_only:
            print(
                f"FAIL [{idx_need}/{total_need}] {ticker}: skipping file_search due to --local-only",
                flush=True,
            )
            continue

        client_fs = ensure_client()
        if vector_store_id is None:
            vector_store_id = get_or_create_vector_store(client_fs, VECTOR_STORE_ID_FILE)
        attach_file_to_vector_store(client_fs, vector_store_id, snippet_path)
        parsed_fs = parse_data_filesearch(client_fs, vector_store_id, identity.name, ticker)
        if (
            parsed_fs
            and parsed_fs.scope_1
            and parsed_fs.scope_2
            and parsed_fs.scope_1 > 0
            and parsed_fs.scope_2 > 0
        ):
            if parsed_fs.confidence >= threshold:
                if update_company_emissions(company, parsed_fs):
                    method_suffix = (
                        f", method={parsed_fs.scope_2_method}"
                        if parsed_fs.scope_2_method
                        else ""
                    )
                    print(
                        f"ANALYSED [{idx_need}/{total_need}] {ticker}: ai-file{method_suffix} "
                        f"(conf={parsed_fs.confidence:.2f})",
                        flush=True,
                    )
                    changed = True
            else:
                print(
                    f"LOW CONF [{idx_need}/{total_need}] {ticker}: ai-file conf={parsed_fs.confidence:.2f} "
                    f"< {threshold:.2f}",
                    flush=True,
                )
        else:
            print(
                f"FAIL [{idx_need}/{total_need}] {ticker}: ai-file parse failed",
                flush=True,
            )
            advice = advise_on_failure(
                client_fs,
                snippet_text,
                Path(download.pdf_path),
                identity.name,
                company.search_record.year if company.search_record else "2025",
            )
            if advice:
                suggestion = (
                    f" | suggestion: {advice.suggestion}" if advice.suggestion else ""
                )
                print(
                    f"ADVISE [{idx_need}/{total_need}] {ticker}: {advice.label} - {advice.reason}{suggestion}",
                    flush=True,
                )

    if changed:
        dump_companies(companies_path, payload, companies)
        print(f"Updated {companies_path}", flush=True)
    else:
        print("No changes.", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
