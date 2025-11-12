from __future__ import annotations

import argparse
import random
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from openai import OpenAI


from src.models import Company, EmissionsData, VerificationRecord

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


@dataclass
class AnalysisResult:
    changed: bool
    last_success: Optional[Tuple[str, List[int], ParsedResult, str]]
    vector_store_id: Optional[str]
    attempted: bool


VALUE_TOKEN_RE = re.compile(r"(-?\d[\d,]*(?:\.\d+)?)")


def _collect_page_lookup(lines: List[str]) -> List[Optional[int]]:
    page_lookup: List[Optional[int]] = []
    current_page: Optional[int] = None
    for line in lines:
        stripped = line.strip()
        page_match = PAGE_HEADER_RE.match(stripped)
        if page_match:
            current_page = int(page_match.group(1))
        else:
            table_match = TABLE_HEADER_RE.match(stripped)
            if table_match:
                current_page = int(table_match.group(1))
        page_lookup.append(current_page)
    return page_lookup


def _context_keywords(context: str) -> List[str]:
    lowered = context.lower()
    keywords: List[str] = []
    for label in ("scope 1", "scope 2", "scope 3"):
        if label in lowered:
            keywords.append(label)
    return keywords


def _find_line_index_for_value(
    lines: List[str],
    keywords: List[str],
    target: float,
    tolerance: float,
) -> Optional[int]:
    for idx, line in enumerate(lines):
        line_lower = line.lower()
        if keywords and not all(key in line_lower for key in keywords):
            continue
        for match in VALUE_TOKEN_RE.finditer(line):
            token = match.group(1)
            if not token:
                continue
            cleaned = token.replace(",", "")
            try:
                value = float(cleaned)
            except ValueError:
                continue
            if abs(value - target) <= tolerance:
                return idx
    return None


def _scope_keywords_from_hint(hint: Optional[str], scope_label: str) -> List[str]:
    if hint:
        extracted = _context_keywords(hint)
        if extracted:
            return extracted
    return [scope_label]


def _generate_value_targets(value: int) -> List[float]:
    candidates: List[float] = []
    for factor in (1_000_000_000, 1_000_000, 1_000, 1):
        candidate = value / factor
        if candidate <= 0:
            continue
        if candidate < 0.01:
            continue
        candidates.append(candidate)
    return candidates


def _excerpt_from_snippet(
    snippet_text: str,
    scope_label: str,
    value: Optional[int],
    context_hint: Optional[str],
) -> Optional[str]:
    snippet_text = snippet_text or ""
    if not snippet_text.strip():
        return context_hint.strip() if context_hint else None

    if context_hint:
        stripped = context_hint.strip()
        if stripped and stripped in snippet_text:
            return stripped

    if value is None or value <= 0:
        return context_hint.strip() if context_hint and context_hint.strip() else None

    lines = snippet_text.splitlines()
    keywords = _scope_keywords_from_hint(context_hint, scope_label)
    keyword_options = [keywords] if keywords else [[]]
    if keywords:
        keyword_options.append([])

    for keyword_set in keyword_options:
        for candidate in _generate_value_targets(value):
            idx = _find_line_index_for_value(
                lines,
                keyword_set,
                candidate,
                tolerance=max(0.5, abs(candidate) * 0.02),
            )
            if idx is None:
                continue
            start = max(0, idx - 1)
            end = min(len(lines), idx + 2)
            excerpt_lines = [line.rstrip() for line in lines[start:end] if line.strip()]
            if excerpt_lines:
                excerpt = "\n".join(excerpt_lines).strip()
                if excerpt:
                    return excerpt
    return context_hint.strip() if context_hint and context_hint.strip() else None


def _normalise_parsed_result_contexts(
    parsed: ParsedResult,
    snippet_text: str,
) -> ParsedResult:
    parsed.scope_1_context = _excerpt_from_snippet(
        snippet_text,
        "scope 1",
        parsed.scope_1,
        parsed.scope_1_context,
    )
    parsed.scope_2_context = _excerpt_from_snippet(
        snippet_text,
        "scope 2",
        parsed.scope_2,
        parsed.scope_2_context,
    )
    parsed.scope_3_context = _excerpt_from_snippet(
        snippet_text,
        "scope 3",
        parsed.scope_3,
        parsed.scope_3_context,
    )
    return parsed


def derive_relevant_pages(
    snippet_text: str,
    parsed: ParsedResult,
    default_pages: List[int],
) -> List[int]:
    if not snippet_text:
        return default_pages

    lines = snippet_text.splitlines()
    if not lines:
        return default_pages

    page_lookup = _collect_page_lookup(lines)

    contexts = [
        parsed.scope_1_context,
        parsed.scope_2_context,
        parsed.scope_3_context,
    ]
    discovered_pages: List[int] = []

    for context in contexts:
        if not context:
            continue
        matches = VALUE_TOKEN_RE.findall(context)
        if not matches:
            continue
        keywords = _context_keywords(context)
        best_page: Optional[int] = None
        for match in matches:
            cleaned = match.replace(",", "")
            try:
                number = float(cleaned)
            except ValueError:
                continue
            tolerance = max(1.0, abs(number) * 0.01)
            idx = _find_line_index_for_value(lines, keywords, number, tolerance)
            if idx is None and keywords:
                idx = _find_line_index_for_value(lines, [], number, tolerance)
            if idx is None:
                continue
            page = page_lookup[idx] if idx < len(page_lookup) else None
            if page is not None:
                best_page = page
                break
        if best_page is not None:
            discovered_pages.append(best_page)

    if discovered_pages:
        unique = sorted({page for page in discovered_pages if page is not None})
        if unique:
            return unique
    return default_pages


def clean_company_artifacts(
    company: Company,
    companies_path: Path,
) -> Tuple[bool, List[str], List[str]]:
    base_dir = companies_path.parent
    changed = False
    removed_files: List[str] = []
    missing_files: List[str] = []

    def resolve_path(path_str: str) -> Path:
        candidate = Path(path_str)
        if not candidate.is_absolute():
            candidate = (base_dir / candidate).resolve()
        return candidate

    def delete_file(path_str: Optional[str]) -> None:
        nonlocal changed
        if not path_str:
            return
        path = resolve_path(path_str)
        try:
            path.unlink()
            removed_files.append(str(path))
            changed = True
        except FileNotFoundError:
            missing_files.append(str(path))
        except OSError as exc:
            missing_files.append(f"{path} (error: {exc})")

    download = company.download_record
    if download and download.pdf_path:
        delete_file(download.pdf_path)
        company.download_record = None
        changed = True
    elif download is not None:
        company.download_record = None
        changed = True

    extraction = company.extraction_record
    if extraction:
        delete_file(extraction.text_path)
        delete_file(extraction.table_path)
        company.extraction_record = None
        changed = True

    if company.search_record is not None:
        company.search_record = None
        changed = True

    if company.analysis_record is not None:
        company.analysis_record = None
        changed = True

    empty_emissions = EmissionsData()
    if company.emissions is None or company.emissions.model_dump(
        mode="json"
    ) != empty_emissions.model_dump(mode="json"):
        company.emissions = empty_emissions
        changed = True

    verification = getattr(company, "verification", None)
    if verification is None:
        company.verification = VerificationRecord()
        changed = True
    else:
        reset_verification = False
        if verification.status != "pending":
            verification.status = "pending"
            reset_verification = True
        if verification.reviewer is not None:
            verification.reviewer = None
            reset_verification = True
        if verification.verified_at is not None:
            verification.verified_at = None
            reset_verification = True
        if verification.scope_1_override is not None:
            verification.scope_1_override = None
            reset_verification = True
        if verification.scope_2_override is not None:
            verification.scope_2_override = None
            reset_verification = True
        if verification.scope_3_override is not None:
            verification.scope_3_override = None
            reset_verification = True
        if verification.notes is not None:
            verification.notes = None
            reset_verification = True
        if reset_verification:
            changed = True

    return changed, removed_files, missing_files


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
        "--clean",
        action="store_true",
        help=(
            "When analysis fails to extract emissions, remove the company's search, download, and extraction "
            "records (and associated files) so it can be re-searched."
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
    parser.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="Number of parallel workers to use in auto mode (default: 1).",
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


def analyse_company(
    company: Company,
    *,
    idx: int,
    total: int,
    threshold: float,
    mode: str,
    local_only: bool,
    local_llm_path: Optional[Path],
    no_upload: bool,
    ensure_client: Callable[[], OpenAI],
    ensure_local_llm: Callable[[], Optional[Any]],
    vector_store_id: Optional[str],
    log: Callable[[str], None],
    prompt_accept_fn: Callable[[str, ParsedResult, str, List[int], float], bool],
    test_mode: bool,
) -> AnalysisResult:
    identity = company.identity
    ticker = identity.ticker or identity.name
    attempted_any = False

    download = company.download_record
    if not download or not download.pdf_path:
        log(f"SKIP [{idx}/{total}] {ticker}: no downloaded PDF recorded")
        return AnalysisResult(False, None, vector_store_id, attempted_any)

    extraction = company.extraction_record
    if not extraction:
        log(f"SKIP [{idx}/{total}] {ticker}: no snippet found, run s4_extract.py")
        return AnalysisResult(False, None, vector_store_id, attempted_any)

    snippet_candidates: List[Tuple[str, Path, int]] = []

    if extraction.table_path and extraction.table_count > 0:
        table_path = Path(extraction.table_path)
        if table_path.exists():
            snippet_candidates.append(
                ("tables", table_path, extraction.table_token_count)
            )
        else:
            log(
                f"SKIP [{idx}/{total}] {ticker}: tables snippet path missing ({table_path})"
            )

    if extraction.text_path:
        text_path = Path(extraction.text_path)
        if text_path.exists():
            snippet_candidates.append(("text", text_path, extraction.text_token_count))
        else:
            log(
                f"SKIP [{idx}/{total}] {ticker}: text snippet path missing ({text_path})"
            )

    if not snippet_candidates:
        log(
            f"SKIP [{idx}/{total}] {ticker}: no usable snippets found, run s4_extract.py"
        )
        return AnalysisResult(False, None, vector_store_id, attempted_any)

    download_path = Path(download.pdf_path)
    changed = False
    last_success: Optional[Tuple[str, List[int], ParsedResult, str]] = None

    for snippet_label, snippet_path, token_estimate in snippet_candidates:
        try:
            snippet_text = snippet_path.read_text(encoding="utf-8")
        except OSError as exc:
            log(
                f"FAIL [{idx}/{total}] {ticker}: unable to read {snippet_label} snippet ({exc})"
            )
            continue

        log(
            f"TRY [{idx}/{total}] {ticker}: using {snippet_label} snippet (tokens~{token_estimate})"
        )

        if len(snippet_text.strip()) < 50:
            log(
                f"NOTE [{idx}/{total}] {ticker}: {snippet_label} snippet too small; results may be unreliable"
            )

        snippet_pages = extract_snippet_pages(snippet_text, snippet_label)

        snippet_success = False

        def attempt_method(
            method_label: str,
            parsed_result: Optional[ParsedResult],
            snippet_source: str = snippet_text,
            *,
            current_snippet_label: str,
            current_snippet_pages: List[int],
            current_snippet_path: Path,
        ) -> bool:
            nonlocal snippet_success, changed, last_success, vector_store_id, attempted_any
            attempted_any = True
            if (
                not parsed_result
                or not parsed_result.scope_1
                or not parsed_result.scope_2
                or parsed_result.scope_1 <= 0
                or parsed_result.scope_2 <= 0
            ):
                log(
                    f"MISS [{idx}/{total}] {ticker}: {current_snippet_label} {method_label} did not yield usable values"
                )
                return False

            parsed_result = _normalise_parsed_result_contexts(
                parsed_result, snippet_source
            )

            refined_pages = derive_relevant_pages(
                snippet_source, parsed_result, current_snippet_pages
            )
            confidence = parsed_result.confidence or 0.0
            page_display = format_page_list(refined_pages)
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
                log(
                    f"CANDIDATE [{idx}/{total}] {ticker}: {current_snippet_label} {method_label}{method_text} "
                    f"pages={page_display} scope1={parsed_result.scope_1} scope2={parsed_result.scope_2} "
                    f"scope3={parsed_result.scope_3} conf={confidence:.2f}"
                )
            if not interactive and confidence < threshold:
                log(
                    f"LOW CONF [{idx}/{total}] {ticker}: {current_snippet_label} {method_label} conf={confidence:.2f} "
                    f"< {threshold:.2f}; continuing"
                )
                return False

            accept = True
            if interactive:
                accept = prompt_accept_fn(
                    method_label,
                    parsed_result,
                    current_snippet_label,
                    refined_pages,
                    confidence,
                )
            if not accept:
                log(
                    f"SKIP [{idx}/{total}] {ticker}: {current_snippet_label} {method_label} rejected"
                )
                return False

            if update_company_emissions(
                company,
                parsed_result,
                method=method_label,
                snippet_label=current_snippet_label,
                snippet_path=current_snippet_path,
                snippet_pages=refined_pages,
            ):
                changed = True
                last_success = (
                    current_snippet_label,
                    refined_pages,
                    parsed_result,
                    method_label,
                )
                log(
                    f"ANALYSED [{idx}/{total}] {ticker}: {current_snippet_label} {method_label}{method_text} "
                    f"(conf={confidence:.2f})"
                )
                snippet_success = True
                return True

            log(
                f"MISS [{idx}/{total}] {ticker}: {current_snippet_label} {method_label} did not change emissions"
            )
            return False

        if attempt_method(
            "python",
            parse_data_fuzzy(snippet_text),
            current_snippet_label=snippet_label,
            current_snippet_pages=snippet_pages,
            current_snippet_path=snippet_path,
        ):
            break

        if local_only:
            log(
                f"SKIP [{idx}/{total}] {ticker}: {snippet_label} OpenAI methods disabled by --local"
            )
            continue

        if local_llm_path is not None:
            llm_instance = ensure_local_llm()
            if llm_instance is not None and attempt_method(
                "local-llm",
                parse_data_llama(llm_instance, snippet_text),
                current_snippet_label=snippet_label,
                current_snippet_pages=snippet_pages,
                current_snippet_path=snippet_path,
            ):
                break

        parsed_ai_snippet = parse_data_local(ensure_client(), snippet_text)
        if attempt_method(
            "ai-snippet",
            parsed_ai_snippet,
            current_snippet_label=snippet_label,
            current_snippet_pages=snippet_pages,
            current_snippet_path=snippet_path,
        ):
            break

        if no_upload:
            continue

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
                    f" | suggestion: {advice.suggestion}" if advice.suggestion else ""
                )
                log(
                    f"ADVISE [{idx}/{total}] {ticker}: {snippet_label} {advice.label} - {advice.reason}{suggestion}"
                )

        if snippet_success:
            break

    return AnalysisResult(
        changed=changed,
        last_success=last_success,
        vector_store_id=vector_store_id,
        attempted=attempted_any,
    )


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    companies_path = Path(args.companies).expanduser().resolve()

    companies, payload = load_companies(companies_path)

    test_mode = args.test
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

    dirty = False
    persisted = False

    def mark_dirty() -> None:
        nonlocal changed, dirty
        changed = True
        dirty = True

    def persist_if_needed() -> None:
        nonlocal dirty, persisted
        if test_mode or not dirty:
            return
        dump_companies(companies_path, payload, companies)
        dirty = False
        persisted = True
    jobs = max(1, args.jobs)
    if jobs > 1:
        if test_mode:
            print(
                "WARN: --jobs ignored for test mode; running sequentially.",
                flush=True,
            )
            jobs = 1
        elif mode != "auto":
            print(
                "WARN: --jobs > 1 currently requires --mode auto; running sequentially.",
                flush=True,
            )
            jobs = 1
        elif local_llm_path is not None:
            print(
                "WARN: --jobs > 1 is currently incompatible with --local-llm; running sequentially.",
                flush=True,
            )
            jobs = 1
        elif not args.no_upload:
            print(
                "WARN: --jobs > 1 currently requires --no-upload; running sequentially.",
                flush=True,
            )
            jobs = 1

    indexed_companies = list(enumerate(companies))

    if test_mode:
        analyzable: List[Tuple[int, Company]] = [
            (idx, company)
            for idx, company in indexed_companies
            if company.download_record
            and company.download_record.pdf_path
            and company.extraction_record
            and (
                (
                    company.extraction_record.table_path
                    and company.extraction_record.table_count > 0
                )
                or company.extraction_record.text_path
            )
        ]
        if not analyzable:
            print(
                "TEST MODE: no companies with extracted snippets available.",
                flush=True,
            )
            return 1
        company_index, selection = random.choice(analyzable)
        company_pairs = [(company_index, selection)]
        total_need = 1
        display_name = selection.identity.ticker or selection.identity.name
        print(f"TEST MODE: selected {display_name}", flush=True)
    else:
        company_pairs = [
            (idx, company)
            for idx, company in indexed_companies
            if needs_verification(company, args.all)
        ]
        total_need = len(company_pairs)
        if total_need == 0:
            print("No companies require analysis.", flush=True)
            return 0

    if jobs == 1:
        for position, (company_index, company) in enumerate(company_pairs, start=1):

            def log(message: str) -> None:
                print(message, flush=True)

            def prompt_accept_wrapper(
                method_label: str,
                parsed: ParsedResult,
                snippet_label: str,
                snippet_pages: List[int],
                confidence: float,
            ) -> bool:
                return prompt_accept(
                    method_label, parsed, snippet_label, snippet_pages, confidence
                )

            company_changed = False
            result = analyse_company(
                company,
                idx=position,
                total=total_need,
                threshold=threshold,
                mode=mode,
                local_only=args.local,
                local_llm_path=None if args.local else local_llm_path,
                no_upload=args.no_upload,
                ensure_client=ensure_client,
                ensure_local_llm=ensure_local_llm,
                vector_store_id=vector_store_id,
                log=log,
                prompt_accept_fn=prompt_accept_wrapper,
                test_mode=test_mode,
            )
            vector_store_id = result.vector_store_id
            if result.changed:
                mark_dirty()
                company_changed = True
            ticker = company.identity.ticker or company.identity.name
            if args.clean and not test_mode and result.attempted and not result.changed:
                cleaned, removed_files, missing_files = clean_company_artifacts(
                    company, companies_path
                )
                if cleaned:
                    mark_dirty()
                    company_changed = True
                    detail_parts: List[str] = []
                    if removed_files:
                        detail_parts.append(f"removed {len(removed_files)} file(s)")
                    if missing_files:
                        detail_parts.append(f"{len(missing_files)} file(s) missing")
                    detail = (
                        "; ".join(detail_parts) if detail_parts else "records cleared"
                    )
                    log(
                        f"CLEAN [{position}/{total_need}] {ticker}: no usable emissions; {detail}; ready for re-search"
                    )
            companies[company_index] = company
            if company_changed:
                persist_if_needed()
            if test_mode:
                name_display = company.identity.ticker or company.identity.name
                if result.last_success is not None:
                    snippet_label, pages, parsed, method_used = result.last_success
                    page_display = format_page_list(pages)
                    print(
                        f"TEST RESULT {name_display}: source={snippet_label}/{method_used}, pages={page_display}, "
                        f"scope1={parsed.scope_1}, scope2={parsed.scope_2}, scope3={parsed.scope_3}, "
                        f"method={parsed.scope_2_method}, confidence={parsed.confidence:.2f}",
                        flush=True,
                    )
                else:
                    print(
                        f"TEST RESULT {name_display}: no emissions extracted.",
                        flush=True,
                    )
        if test_mode:
            changed = False
    else:
        max_workers = min(jobs, total_need)
        print(
            f"Running analysis with {max_workers} parallel workers (auto, snippets only).",
            flush=True,
        )

        def worker(
            company_index: int,
            company_payload: Dict[str, Any],
            position: int,
        ) -> Tuple[int, Dict[str, Any], List[str], bool]:
            local_client: Optional[OpenAI] = None

            def ensure_client_worker() -> OpenAI:
                nonlocal local_client
                if local_client is None:
                    local_client = OpenAI()
                return local_client

            logs: List[str] = []
            company_obj = Company.model_validate(company_payload)
            worker_result = analyse_company(
                company_obj,
                idx=position,
                total=total_need,
                threshold=threshold,
                mode="auto",
                local_only=args.local,
                local_llm_path=None,
                no_upload=True,
                ensure_client=ensure_client_worker,
                ensure_local_llm=lambda: None,
                vector_store_id=None,
                log=logs.append,
                prompt_accept_fn=lambda *_: False,
                test_mode=False,
            )
            worker_changed = worker_result.changed
            ticker = company_obj.identity.ticker or company_obj.identity.name
            if args.clean and worker_result.attempted and not worker_result.changed:
                cleaned, removed_files, missing_files = clean_company_artifacts(
                    company_obj, companies_path
                )
                if cleaned:
                    worker_changed = True
                    detail_parts: List[str] = []
                    if removed_files:
                        detail_parts.append(f"removed {len(removed_files)} file(s)")
                    if missing_files:
                        detail_parts.append(f"{len(missing_files)} file(s) missing")
                    detail = (
                        "; ".join(detail_parts) if detail_parts else "records cleared"
                    )
                    logs.append(
                        f"CLEAN [{position}/{total_need}] {ticker}: no usable emissions; {detail}; ready for re-search"
                    )
            return (
                company_index,
                company_obj.model_dump(mode="json"),
                logs,
                worker_changed,
            )

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    worker,
                    company_index,
                    company.model_dump(mode="json"),
                    position,
                ): (position, company_index)
                for position, (company_index, company) in enumerate(
                    company_pairs, start=1
                )
            }
            for future in as_completed(futures):
                position, company_index = futures[future]
                exc = future.exception()
                if exc is not None:  # pragma: no cover - safety net
                    identity = companies[company_index].identity
                    name_display = identity.ticker or identity.name
                    print(
                        f"ERROR [{position}/{total_need}] {name_display}: analysis failed ({exc})",
                        flush=True,
                    )
                    continue
                company_idx, company_data, logs, company_changed = future.result()
                for line in logs:
                    print(line, flush=True)
                companies[company_idx] = Company.model_validate(company_data)
                if company_changed:
                    mark_dirty()
                    persist_if_needed()

    if test_mode:
        print("TEST MODE complete: no changes persisted.", flush=True)
    else:
        if dirty:
            persist_if_needed()
        if persisted:
            print(f"Updated {companies_path}", flush=True)
        else:
            print("No changes.", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
