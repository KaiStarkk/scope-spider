from __future__ import annotations

import argparse
import random
import re
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any, List, Optional, Tuple

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
    parse_data_llama,
    parse_data_local,
    ParsedResult,
    update_company_emissions,
)

VECTOR_STORE_ID_FILE = Path(".vector_store_id")

PAGE_HEADER_RE = re.compile(r"=== Page (\d+)\s*===", re.IGNORECASE)
TABLE_HEADER_RE = re.compile(r"=== Table \d+\s+\(page\s+(\d+)\)\s*===", re.IGNORECASE)


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
        "--mode",
        choices=["auto", "review", "threshold"],
        default="auto",
        help=(
            "Result handling mode: 'auto' accepts results above the confidence threshold, "
            "'review' prompts for every result, and 'threshold' only prompts if confidence is below the threshold."
        ),
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Use only the local Python fuzzy matcher (skip all OpenAI and local-LLM calls).",
    )
    parser.add_argument(
        "--local-llm",
        help="Path to a llama.cpp-compatible model to run locally before OpenAI fallbacks.",
    )
    parser.add_argument(
        "--local-llm-gpu-layers",
        type=int,
        default=0,
        help=(
            "Number of layers to offload to GPU when using --local-llm (requires llama.cpp with CUDA). "
            "Default: 0 (CPU only)."
        ),
    )
    parser.add_argument(
        "--no-upload",
        action="store_true",
        help="Skip the ai-upload/vector-store stage and rely only on snippets.",
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


def extract_snippet_pages(snippet_text: str, label: str) -> List[int]:
    if label == "tables":
        matches = TABLE_HEADER_RE.findall(snippet_text)
    else:
        matches = PAGE_HEADER_RE.findall(snippet_text)
    pages: List[int] = []
    seen: set[int] = set()
    for match in matches:
        with suppress(ValueError):
            page = int(match)
            if page not in seen:
                seen.add(page)
                pages.append(page)
    return pages


def format_page_list(pages: List[int]) -> str:
    return ", ".join(str(p) for p in pages) if pages else "unknown"


def prompt_accept(
    method_label: str,
    parsed: ParsedResult,
    snippet_label: str,
    snippet_pages: List[int],
    confidence: float,
) -> bool:
    page_display = format_page_list(snippet_pages)
    method_text = parsed.scope_2_method or "unknown"
    print(
        f"[REVIEW] {snippet_label} {method_label}: pages={page_display} "
        f"scope1={parsed.scope_1} scope2={parsed.scope_2} scope3={parsed.scope_3} "
        f"method={method_text} conf={confidence:.2f}",
        flush=True,
    )
    while True:
        try:
            response = input("Accept result? [y/N]: ").strip().lower()
        except EOFError:
            return False
        if response in ("y", "yes"):
            return True
        if response in ("", "n", "no", "s", "skip"):
            return False
        print("Please answer 'y' or 'n'.")


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    companies_path = Path(args.companies).expanduser().resolve()

    companies, payload = load_companies(companies_path)

    changed = False
    vector_store_id: Optional[str] = None
    client: Optional[OpenAI] = None
    threshold = max(0.0, min(1.0, args.threshold))
    mode = args.mode

    local_llm_path: Optional[Path] = None
    if args.local_llm:
        candidate = Path(args.local_llm).expanduser().resolve()
        if candidate.exists():
            local_llm_path = candidate
        else:
            print(
                f"WARN: --local-llm path does not exist ({candidate}); skipping local LLM.",
                flush=True,
            )

    if args.local and local_llm_path:
        print(
            "NOTE: --local specified; ignoring --local-llm and OpenAI methods.",
            flush=True,
        )

    local_llm_model: Optional[Any] = None
    local_llm_failed = False

    def ensure_local_llm() -> Optional[Any]:
        nonlocal local_llm_model, local_llm_failed
        if local_llm_failed:
            return None
        if local_llm_model is None:
            if local_llm_path is None:
                local_llm_failed = True
                return None
            try:
                from llama_cpp import Llama as RuntimeLlama  # type: ignore[import]
            except ImportError:
                print(
                    "WARN: llama_cpp is not available; cannot use --local-llm.",
                    flush=True,
                )
                local_llm_failed = True
                return None
            try:
                local_llm_model = RuntimeLlama(
                    model_path=str(local_llm_path),
                    n_ctx=4096,
                    embedding=False,
                    n_gpu_layers=max(0, args.local_llm_gpu_layers),
                )
                print(
                    f"[local-llm] Loaded model from {local_llm_path}",
                    flush=True,
                )
            except (
                OSError,
                RuntimeError,
                ValueError,
            ) as exc:  # pragma: no cover - hardware dependent
                print(
                    f"WARN: failed to load local LLM ({exc}); skipping.",
                    flush=True,
                )
                local_llm_failed = True
                return None
        return local_llm_model

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
        snippet_candidates: List[Tuple[str, Path, int]] = []

        if extraction.table_path and extraction.table_count > 0:
            table_path = Path(extraction.table_path)
            if table_path.exists():
                snippet_candidates.append(
                    ("tables", table_path, extraction.table_token_count)
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
                    ("text", text_path, extraction.text_token_count)
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

        last_success: Optional[Tuple[str, List[int], object, str]] = None

        for snippet_label, snippet_path, token_estimate in snippet_candidates:
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

            snippet_pages = extract_snippet_pages(snippet_text, snippet_label)

            snippet_success = False

            def attempt_method(
                method_label: str,
                parsed_result: Optional[ParsedResult],
                *,
                current_ticker: str,
                current_snippet_label: str,
                current_snippet_pages: List[int],
                current_snippet_path: Path,
            ) -> bool:
                nonlocal snippet_success, changed, last_success
                if (
                    not parsed_result
                    or not parsed_result.scope_1
                    or not parsed_result.scope_2
                    or parsed_result.scope_1 <= 0
                    or parsed_result.scope_2 <= 0
                ):
                    print(
                        f"MISS [{idx_need}/{total_need}] {current_ticker}: {current_snippet_label} {method_label} did not yield usable values",
                        flush=True,
                    )
                    return False
                confidence = parsed_result.confidence or 0.0
                page_display = format_page_list(current_snippet_pages)
                method_text = (
                    f", method={parsed_result.scope_2_method}"
                    if parsed_result.scope_2_method
                    else ""
                )
                interactive = (
                    test_mode
                    or mode == "review"
                    or (mode == "threshold" and confidence < threshold)
                )
                if not interactive:
                    print(
                        f"CANDIDATE [{idx_need}/{total_need}] {current_ticker}: {current_snippet_label} {method_label}{method_text} "
                        f"pages={page_display} scope1={parsed_result.scope_1} scope2={parsed_result.scope_2} "
                        f"scope3={parsed_result.scope_3} conf={confidence:.2f}",
                        flush=True,
                    )
                if not interactive and confidence < threshold:
                    print(
                        f"LOW CONF [{idx_need}/{total_need}] {current_ticker}: {current_snippet_label} {method_label} conf={confidence:.2f} "
                        f"< {threshold:.2f}; continuing",
                        flush=True,
                    )
                    return False
                accept = True
                if interactive:
                    accept = prompt_accept(
                        method_label,
                        parsed_result,
                        current_snippet_label,
                        current_snippet_pages,
                        confidence,
                    )
                if not accept:
                    print(
                        f"SKIP [{idx_need}/{total_need}] {current_ticker}: {current_snippet_label} {method_label} rejected",
                        flush=True,
                    )
                    return False
                if update_company_emissions(
                    company,
                    parsed_result,
                    method=method_label,
                    snippet_label=current_snippet_label,
                    snippet_path=current_snippet_path,
                    snippet_pages=current_snippet_pages,
                ):
                    changed = True
                    last_success = (
                        current_snippet_label,
                        current_snippet_pages,
                        parsed_result,
                        method_label,
                    )
                    print(
                        f"ANALYSED [{idx_need}/{total_need}] {current_ticker}: {current_snippet_label} {method_label}{method_text} "
                        f"(conf={confidence:.2f})",
                        flush=True,
                    )
                    snippet_success = True
                    return True
                print(
                    f"MISS [{idx_need}/{total_need}] {current_ticker}: {current_snippet_label} {method_label} did not change emissions",
                    flush=True,
                )
                return False

            if attempt_method(
                "python",
                parse_data_fuzzy(snippet_text),
                current_ticker=ticker,
                current_snippet_label=snippet_label,
                current_snippet_pages=snippet_pages,
                current_snippet_path=snippet_path,
            ):
                break

            if args.local:
                print(
                    f"SKIP [{idx_need}/{total_need}] {ticker}: {snippet_label} OpenAI methods disabled by --local",
                    flush=True,
                )
                continue

            if local_llm_path:
                llm_instance = ensure_local_llm()
                if llm_instance is not None and attempt_method(
                    "local-llm",
                    parse_data_llama(llm_instance, snippet_text),
                    current_ticker=ticker,
                    current_snippet_label=snippet_label,
                    current_snippet_pages=snippet_pages,
                    current_snippet_path=snippet_path,
                ):
                    break

            parsed_ai_snippet = parse_data_local(ensure_client(), snippet_text)
            if attempt_method(
                "ai-snippet",
                parsed_ai_snippet,
                current_ticker=ticker,
                current_snippet_label=snippet_label,
                current_snippet_pages=snippet_pages,
                current_snippet_path=snippet_path,
            ):
                break

            if not args.no_upload:
                client_fs = ensure_client()
                if vector_store_id is None:
                    vector_store_id = get_or_create_vector_store(
                        client_fs, VECTOR_STORE_ID_FILE
                    )
                attach_file_to_vector_store(client_fs, vector_store_id, snippet_path)
                parsed_ai_upload = parse_data_filesearch(
                    client_fs, vector_store_id, identity.name, ticker
                )
                if attempt_method(
                    "ai-upload",
                    parsed_ai_upload,
                    current_ticker=ticker,
                    current_snippet_label=snippet_label,
                    current_snippet_pages=snippet_pages,
                    current_snippet_path=snippet_path,
                ):
                    break
                if (
                    not parsed_ai_upload
                    or not parsed_ai_upload.scope_1
                    or not parsed_ai_upload.scope_2
                    or parsed_ai_upload.scope_1 <= 0
                    or parsed_ai_upload.scope_2 <= 0
                ):
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

        if test_mode:
            if last_success is not None:
                snippet_label, pages, parsed, method_used = last_success
                page_display = format_page_list(pages)
                print(
                    f"TEST RESULT {ticker}: source={snippet_label}/{method_used}, pages={page_display}, "
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
    if test_mode:
        if changed:
            print("TEST MODE complete: accepted changes saved.", flush=True)
        else:
            print("TEST MODE complete: no changes persisted.", flush=True)
    elif not changed:
        print("No changes.", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
