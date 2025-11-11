from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from openai import OpenAI

from src.models import Company

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
    parser.add_argument(
        "--test",
        action="store_true",
        help="Pick a random company with extracted snippets and analyse it without persisting changes.",
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

    test_mode = args.test

    if test_mode:
        analyzable: List[Company] = [
            c
            for c in companies
            if c.download_record
            and c.download_record.pdf_path
            and c.extraction_record
            and (
                (c.extraction_record.table_path and c.extraction_record.table_count > 0)
                or c.extraction_record.text_path
            )
        ]
        if not analyzable:
            print(
                "TEST MODE: no companies with extracted snippets available.", flush=True
            )
            return 1
        company_list = [random.choice(analyzable)]
        total_need = 1
        selection = company_list[0]
        display_name = selection.identity.ticker or selection.identity.name
        print(f"TEST MODE: selected {display_name}", flush=True)
    else:
        company_list = [c for c in companies if needs_verification(c, args.all)]
        total_need = len(company_list)
        if total_need == 0:
            print("No companies require analysis.", flush=True)
            return 0

    idx_need = 0

    for company in company_list:
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
        if not extraction:
            print(
                f"SKIP [{idx_need}/{total_need}] {ticker}: no snippet found, run s4_extract.py",
                flush=True,
            )
            continue
        snippet_candidates: List[Tuple[str, Path, int, List[int]]] = []

        if extraction.table_path and extraction.table_count > 0:
            table_path = Path(extraction.table_path)
            if table_path.exists():
                snippet_candidates.append(
                    (
                        "tables",
                        table_path,
                        extraction.table_token_count,
                        extraction.table_pages or [],
                    )
                )
            else:
                print(
                    f"SKIP [{idx_need}/{total_need}] {ticker}: tables snippet path missing ({table_path})",
                    flush=True,
                )

        if extraction.text_path:
            text_path = Path(extraction.text_path)
            if text_path.exists():
                snippet_candidates.append(
                    (
                        "text",
                        text_path,
                        extraction.text_token_count,
                        extraction.text_pages or [],
                    )
                )
            else:
                print(
                    f"SKIP [{idx_need}/{total_need}] {ticker}: text snippet path missing ({text_path})",
                    flush=True,
                )

        if not snippet_candidates:
            print(
                f"SKIP [{idx_need}/{total_need}] {ticker}: no usable snippets found, run s4_extract.py",
                flush=True,
            )
            continue

        download_path = Path(download.pdf_path)
        company_skip = False

        last_success: Optional[Tuple[str, List[int], object, str]] = None

        for (
            snippet_label,
            snippet_path,
            token_estimate,
            snippet_pages,
        ) in snippet_candidates:
            try:
                snippet_text = snippet_path.read_text(encoding="utf-8")
            except OSError as exc:
                print(
                    f"FAIL [{idx_need}/{total_need}] {ticker}: unable to read {snippet_label} snippet ({exc})",
                    flush=True,
                )
                continue
            print(
                f"TRY [{idx_need}/{total_need}] {ticker}: using {snippet_label} snippet (tokens~{token_estimate})",
                flush=True,
            )
            if len(snippet_text.strip()) < 50:
                print(
                    f"NOTE [{idx_need}/{total_need}] {ticker}: {snippet_label} snippet too small; results may be unreliable",
                    flush=True,
                )

            snippet_success = False

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
                                f"ANALYSED [{idx_need}/{total_need}] {ticker}: {snippet_label} fuzzy{method_suffix} "
                                f"(conf={parsed_fuzzy.confidence:.2f})",
                                flush=True,
                            )
                            if not test_mode:
                                changed = True
                            last_success = (
                                snippet_label,
                                snippet_pages,
                                parsed_fuzzy,
                                "fuzzy",
                            )
                            snippet_success = True
                    else:
                        print(
                            f"LOW CONF [{idx_need}/{total_need}] {ticker}: {snippet_label} fuzzy conf={parsed_fuzzy.confidence:.2f} "
                            f"< {threshold:.2f}; continuing",
                            flush=True,
                        )
                else:
                    print(
                        f"MISS [{idx_need}/{total_need}] {ticker}: {snippet_label} fuzzy search did not yield usable values",
                        flush=True,
                    )

                if snippet_success:
                    break

                if args.local_only:
                    print(
                        f"FAIL [{idx_need}/{total_need}] {ticker}: {snippet_label} fuzzy mode failed; skipping due to --local-only",
                        flush=True,
                    )
                    company_skip = True
                    break

            if snippet_success:
                break

            if args.local_only:
                print(
                    f"FAIL [{idx_need}/{total_need}] {ticker}: skipping {snippet_label} file_search due to --local-only",
                    flush=True,
                )
                company_skip = True
                break

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
                            f"ANALYSED [{idx_need}/{total_need}] {ticker}: {snippet_label} ai-local{method_suffix} "
                            f"(conf={parsed_llm.confidence:.2f})",
                            flush=True,
                        )
                        if not test_mode:
                            changed = True
                        last_success = (
                            snippet_label,
                            snippet_pages,
                            parsed_llm,
                            "ai-local",
                        )
                        snippet_success = True
                else:
                    print(
                        f"LOW CONF [{idx_need}/{total_need}] {ticker}: {snippet_label} ai-local conf={parsed_llm.confidence:.2f} "
                        f"< {threshold:.2f}; trying file_search",
                        flush=True,
                    )
            else:
                print(
                    f"MISS [{idx_need}/{total_need}] {ticker}: {snippet_label} ai-local parse did not yield usable values; trying file_search",
                    flush=True,
                )

            if snippet_success:
                break

            client_fs = ensure_client()
            if vector_store_id is None:
                vector_store_id = get_or_create_vector_store(
                    client_fs, VECTOR_STORE_ID_FILE
                )
            attach_file_to_vector_store(client_fs, vector_store_id, snippet_path)
            parsed_fs = parse_data_filesearch(
                client_fs, vector_store_id, identity.name, ticker
            )
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
                            f"ANALYSED [{idx_need}/{total_need}] {ticker}: {snippet_label} ai-file{method_suffix} "
                            f"(conf={parsed_fs.confidence:.2f})",
                            flush=True,
                        )
                        if not test_mode:
                            changed = True
                        last_success = (
                            snippet_label,
                            snippet_pages,
                            parsed_fs,
                            "ai-file",
                        )
                        snippet_success = True
            else:
                print(
                    f"FAIL [{idx_need}/{total_need}] {ticker}: {snippet_label} ai-file parse failed",
                    flush=True,
                )
                search_year = (
                    company.search_record.year
                    if company.search_record and company.search_record.year
                    else "unknown"
                )
                advice = advise_on_failure(
                    client_fs,
                    snippet_text,
                    download_path,
                    identity.name,
                    search_year,
                )
                if advice:
                    suggestion = (
                        f" | suggestion: {advice.suggestion}"
                        if advice.suggestion
                        else ""
                    )
                    print(
                        f"ADVISE [{idx_need}/{total_need}] {ticker}: {snippet_label} {advice.label} - {advice.reason}{suggestion}",
                        flush=True,
                    )

            if snippet_success:
                break

        if company_skip:
            continue

        if test_mode:
            if last_success:
                snippet_label, pages, parsed, mode = last_success
                page_display = ", ".join(str(p) for p in pages if p) or "unknown"
                print(
                    f"TEST RESULT {ticker}: source={snippet_label}/{mode}, pages={page_display}, "
                    f"scope1={parsed.scope_1}, scope2={parsed.scope_2}, scope3={parsed.scope_3}, "
                    f"method={parsed.scope_2_method}, confidence={parsed.confidence:.2f}",
                    flush=True,
                )
            else:
                print(
                    f"TEST RESULT {ticker}: no emissions extracted.",
                    flush=True,
                )
            continue

    if changed:
        dump_companies(companies_path, payload, companies)
        print(f"Updated {companies_path}", flush=True)
    elif not test_mode:
        print("No changes.", flush=True)
    else:
        print("TEST MODE complete: no changes persisted.", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
