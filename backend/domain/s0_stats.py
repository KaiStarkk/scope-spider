from __future__ import annotations

import sys
from collections import Counter
import types as types_module
import re
from dataclasses import dataclass
from pathlib import Path
from typing import (
    Any,
    Iterable,
    List,
    Optional,
    Tuple,
    Type,
    Set,
    Union,
    get_args,
    get_origin,
    Literal,
)

from pydantic import BaseModel

from backend.domain.models import (
    Company,
    EmissionsData,
    SearchRecord,
    VerificationRecord,
)
from backend.domain.utils.companies import dump_companies, load_companies
from backend.domain.utils.documents import (
    MAX_REPORT_YEAR,
    classify_document_type,
    infer_year_from_text,
)
from backend.domain.utils.documents import normalise_pdf_url  # type: ignore[attr-defined]
from backend.domain.utils.pdf import extract_pdf_text
from backend.domain.utils.query import derive_filename


SCOPE_KEYWORDS = [
    r"\bscope\s*1\b",
    r"\bscope\s*2\b",
    r"\bscope\s*3\b",
    r"\btco2\b",
    r"\bktco2\b",
    r"\bmtco2\b",
    r"\bkgco2\b",
]
SCOPE_KEYWORDS_RE = re.compile("|".join(SCOPE_KEYWORDS), re.IGNORECASE)
SCOPE_SCAN_MAX_PAGES = 6


@dataclass
class Issue:
    ticker: str
    message: str
    fixed: bool = False
    minor: bool = False


def _resolve_pdf_path(base_file: Path, pdf_path_str: str) -> Path:
    candidate = Path(pdf_path_str)
    if not candidate.is_absolute():
        candidate = base_file.parent / candidate
    return candidate


def _highest_year_from_pages(
    pages: Iterable[str],
) -> Tuple[Optional[str], Optional[int]]:
    pattern = re.compile(r"20\d{2}")
    valid_years: list[int] = []
    future_years: list[int] = []
    for text in pages:
        if not text:
            continue
        for match in pattern.findall(text):
            try:
                year = int(match)
            except ValueError:
                continue
            if 2000 <= year <= MAX_REPORT_YEAR:
                valid_years.append(year)
            elif year > MAX_REPORT_YEAR:
                future_years.append(year)
    best_year = str(max(valid_years)) if valid_years else None
    future_year = max(future_years) if future_years else None
    return best_year, future_year


def validate_search_record(
    record: SearchRecord,
    ticker: str,
    enforce_pdf_only: bool,
    check_pdf_year: bool,
    pdf_path: Optional[Path],
) -> tuple[bool, bool, Iterable[Issue]]:
    changes = False
    issues: list[Issue] = []

    sanitised_url, is_pdf = normalise_pdf_url(record.url)
    if not sanitised_url:
        issues.append(Issue(ticker, "search record removed (empty URL)", True))
        return True, True, issues

    if record.url != sanitised_url:
        record.url = sanitised_url
        issues.append(Issue(ticker, "URL normalised", True))
        changes = True

    if not is_pdf:
        issues.append(
            Issue(
                ticker,
                (
                    "search record removed (non-PDF URL)"
                    if enforce_pdf_only
                    else "search URL is not a direct PDF (pass --pdf to remove)"
                ),
                enforce_pdf_only,
                minor=not enforce_pdf_only,
            )
        )
        if enforce_pdf_only:
            return True, True, issues
        return changes, False, issues

    expected_filename = derive_filename(record.url, record.filename or "")
    if record.filename != expected_filename:
        issues.append(
            Issue(ticker, f"filename normalised to {expected_filename!r}", True)
        )
        record.filename = expected_filename
        changes = True

    if not record.title:
        record.title = record.filename
        issues.append(Issue(ticker, "title was empty; set to filename", True))
        changes = True

    if not record.url.lower().endswith(".pdf"):
        issues.append(Issue(ticker, "search URL does not end with .pdf", False, True))

    derived_type = classify_document_type(record.title, record.filename, record.url)
    if record.doc_type != derived_type:
        issues.append(Issue(ticker, f"doc_type set to {derived_type!r}", True))
        record.doc_type = derived_type
        changes = True

    inferred_year = (
        infer_year_from_text(record.title, record.filename, record.url)
        if check_pdf_year
        else None
    )
    if inferred_year:
        if record.year != inferred_year:
            issues.append(Issue(ticker, f"year set to {inferred_year}", True))
            record.year = inferred_year
            changes = True
    elif not record.year:
        issues.append(Issue(ticker, "year missing", False, True))
    else:
        if len(record.year) != 4 or not record.year.isdigit():
            issues.append(Issue(ticker, "invalid year format; cleared", True))
            record.year = None
            changes = True

    if record.year and record.year.isdigit():
        numeric_year = int(record.year)
        if numeric_year > MAX_REPORT_YEAR:
            issues.append(
                Issue(
                    ticker,
                    f"year {numeric_year} exceeds supported maximum {MAX_REPORT_YEAR}; cleared",
                    True,
                )
            )
            record.year = None
            changes = True

    if check_pdf_year:
        if pdf_path and pdf_path.exists() and pdf_path.suffix.lower() == ".pdf":
            pages = extract_pdf_text(pdf_path, max_pages=1)
            pdf_year, future_year = _highest_year_from_pages(pages)
            if future_year:
                issues.append(
                    Issue(
                        ticker,
                        f"year check: ignored future year {future_year} on first PDF page",
                        False,
                    )
                )
            if pdf_year:
                if record.year != pdf_year:
                    record.year = pdf_year
                    issues.append(
                        Issue(
                            ticker,
                            f"year corrected to {pdf_year} based on first PDF page",
                            True,
                        )
                    )
                    changes = True
            elif not future_year:
                issues.append(
                    Issue(
                        ticker,
                        "year check: no 20XX year found on first PDF page",
                        False,
                    )
                )
        else:
            issues.append(
                Issue(
                    ticker,
                    "year check: PDF not available; skipped",
                    False,
                )
            )

    return changes, False, issues


def _extract_base_model(annotation: object) -> Type[BaseModel] | None:
    origin = get_origin(annotation)
    if origin is None:
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            return annotation
        return None
    for arg in get_args(annotation):
        sub = _extract_base_model(arg)
        if sub is not None:
            return sub
    return None


def _expected_scalar_types(annotation: object) -> Tuple[type, ...]:
    origin = get_origin(annotation)
    if origin is None:
        if isinstance(annotation, type) and not issubclass(annotation, BaseModel):
            return (annotation,)
        return tuple()
    if origin is Literal:
        literal_types = {type(arg) for arg in get_args(annotation)}
        return tuple(literal_types)
    collected: list[type] = []
    for arg in get_args(annotation):
        collected.extend(_expected_scalar_types(arg))
    # Preserve order without duplicates
    seen: dict[type, None] = {}
    for typ in collected:
        if typ not in seen:
            seen[typ] = None
    return tuple(seen.keys())


STAGE_NAMES = ("s2", "s3", "s4", "s5", "s6")
STAGE_DEPENDENCIES = {
    "s2": ("s2", "s3", "s4", "s5"),
    "s3": ("s3", "s4", "s5"),
    "s4": ("s4", "s5"),
    "s5": ("s5",),
    "s6": ("s6",),
}

ANNOTATION_RESET_FIELDS = [
    "anzsic_division",
    "anzsic_confidence",
    "anzsic_context",
    "anzsic_source",
    "anzsic_local_division",
    "anzsic_local_confidence",
    "anzsic_local_context",
    "anzsic_agreement",
    "profitability_year",
    "profitability_revenue_mm_aud",
    "profitability_ebitda_mm_aud",
    "profitability_net_income_mm_aud",
    "profitability_total_assets_mm_aud",
    "size_employee_count",
    "reporting_group",
    "rbics_sector",
    "rbics_sub_sector",
    "rbics_industry_group",
    "rbics_industry",
    "company_country",
    "company_region",
    "company_state",
    "net_zero_claims",
]


def _reset_annotations(company: Company) -> bool:
    changed = False
    annotations = company.annotations
    for field in ANNOTATION_RESET_FIELDS:
        if getattr(annotations, field, None) is not None:
            setattr(annotations, field, None)
            changed = True
    return changed


def _reset_verification(company: Company) -> bool:
    changed = False
    if not isinstance(company.verification, VerificationRecord):
        company.verification = VerificationRecord()
        return True
    verification = company.verification
    reset_fields = [
        "verified_at",
        "reviewer",
        "scope_1_override",
        "scope_2_override",
        "scope_3_override",
        "notes",
    ]
    for field in reset_fields:
        if getattr(verification, field, None) is not None:
            setattr(verification, field, None)
            changed = True
    if verification.status != "pending":
        verification.status = "pending"
        changed = True
    return changed


def _reset_analysis(company: Company) -> bool:
    changed = False
    if company.analysis_record is not None:
        company.analysis_record = None
        changed = True
    if not isinstance(company.emissions, EmissionsData) or any(
        getattr(company.emissions, attr) is not None
        for attr in ("scope_1", "scope_2", "scope_3")
    ):
        company.emissions = EmissionsData()
        changed = True
    if _reset_verification(company):
        changed = True
    return changed


def _reset_extraction(company: Company) -> bool:
    if company.extraction_record is not None:
        company.extraction_record = None
        return True
    return False


def _reset_download(company: Company) -> bool:
    if company.download_record is not None:
        company.download_record = None
        return True
    return False


def _reset_search(company: Company) -> bool:
    if company.search_record is not None:
        company.search_record = None
        return True
    return False


def _apply_stage_reset(company: Company, stage: str) -> bool:
    if stage == "s2":
        return _reset_search(company)
    if stage == "s3":
        return _reset_download(company)
    if stage == "s4":
        return _reset_extraction(company)
    if stage == "s5":
        return _reset_analysis(company)
    if stage == "s6":
        return _reset_annotations(company)
    raise ValueError(f"Unknown stage: {stage}")


def _expand_stages(stages: Iterable[str]) -> List[str]:
    ordered: List[str] = []
    seen: Set[str] = set()
    for stage in stages:
        key = stage.lower()
        if key not in STAGE_DEPENDENCIES:
            raise ValueError(
                f"Unsupported stage {stage!r}; expected one of {', '.join(STAGE_NAMES)}"
            )
        for dependent in STAGE_DEPENDENCIES[key]:
            if dependent in seen:
                continue
            ordered.append(dependent)
            seen.add(dependent)
    return ordered


def reset_company_stages(company: Company, stages: Iterable[str]) -> bool:
    changed = False
    for stage in _expand_stages(stages):
        if _apply_stage_reset(company, stage):
            changed = True
    return changed


def reset_company_pipeline_state(company: Company) -> bool:
    return reset_company_stages(company, ("s4", "s5", "s6"))


def _unwrap_optional(annotation: object) -> object:
    origin = get_origin(annotation)
    if origin in (Union, types_module.UnionType):
        args = get_args(annotation)
        non_none = [arg for arg in args if arg is not type(None)]
        if len(non_none) == 1 and len(non_none) != len(args):
            return non_none[0]
    return annotation


def validate_structure(
    raw_value,
    model: Type[BaseModel],
    ticker: str,
    path: str,
) -> list[Issue]:
    issues: list[Issue] = []
    if not isinstance(raw_value, dict):
        issues.append(
            Issue(
                ticker,
                f"{path or 'company'} expected an object but found {type(raw_value).__name__}",
                False,
            )
        )
        return issues

    fields = model.model_fields
    raw_keys = set(raw_value.keys())
    expected_keys = set(fields.keys())

    for extra in sorted(raw_keys - expected_keys):
        location = f"{path}.{extra}" if path else extra
        issues.append(Issue(ticker, f"unexpected key {location}", False))

    for name, field in fields.items():
        sub_path = f"{path}.{name}" if path else name
        if name not in raw_value:
            if field.is_required():
                issues.append(Issue(ticker, f"missing required key {sub_path}", False))
            continue

        value = raw_value[name]
        if value is None:
            continue

        annotation = _unwrap_optional(field.annotation)
        sub_model = _extract_base_model(annotation)
        if sub_model is not None:
            if not isinstance(value, dict):
                issues.append(
                    Issue(
                        ticker,
                        f"{sub_path} expected object, found {type(value).__name__}",
                        False,
                    )
                )
            else:
                issues.extend(validate_structure(value, sub_model, ticker, sub_path))
            continue

        origin = get_origin(annotation)
        container_type: type | None = None
        if origin in (list, List):
            container_type = list
        elif origin in (tuple, Tuple):
            container_type = tuple
        elif origin in (set, Set):
            container_type = set

        if container_type is not None:
            if not isinstance(value, container_type):
                issues.append(
                    Issue(
                        ticker,
                        f"{sub_path} expected {container_type.__name__}, found {type(value).__name__}",
                        True,
                    )
                )
                continue

            element_annotations = get_args(annotation)
            element_models = [
                _extract_base_model(_unwrap_optional(elem))
                for elem in element_annotations
            ]
            if any(elem_model is not None for elem_model in element_models):
                for idx, item in enumerate(value):
                    elem_model = next(
                        (
                            model_candidate
                            for model_candidate in element_models
                            if model_candidate is not None
                        ),
                        None,
                    )
                    if elem_model is None:
                        break
                    if not isinstance(item, dict):
                        issues.append(
                            Issue(
                                ticker,
                                f"{sub_path}[{idx}] expected object, found {type(item).__name__}",
                                False,
                            )
                        )
                    else:
                        issues.extend(
                            validate_structure(
                                item,
                                elem_model,
                                ticker,
                                f"{sub_path}[{idx}]",
                            )
                        )
                continue

            scalar_types: list[type] = []
            for elem in element_annotations:
                scalar_types.extend(_expected_scalar_types(_unwrap_optional(elem)))
            if scalar_types:
                seen_scalar: dict[type, None] = {}
                for typ in scalar_types:
                    if typ not in seen_scalar:
                        seen_scalar[typ] = None
                allowed_types = tuple(seen_scalar.keys())
                for idx, item in enumerate(value):
                    if item is None:
                        continue
                    if not isinstance(item, allowed_types):
                        expected_names = "/".join(
                            sorted(typ.__name__ for typ in allowed_types)
                        )
                        issues.append(
                            Issue(
                                ticker,
                                f"{sub_path}[{idx}] expected {expected_names}, found {type(item).__name__}",
                                True,
                            )
                        )
            continue

        expected_types = _expected_scalar_types(annotation)
        if expected_types and not isinstance(value, expected_types):
            type_names = "/".join(sorted(t.__name__ for t in expected_types))
            issues.append(
                Issue(
                    ticker,
                    f"{sub_path} expected {type_names}, found {type(value).__name__}",
                    True,
                )
            )

    return issues


def summarise_stages(company: Company, stage_counts: Counter) -> None:
    stage_counts["total"] += 1
    record = company.search_record
    if record and record.url:
        stage_counts["searched"] += 1
    download = company.download_record
    if download and download.pdf_path:
        stage_counts["downloaded"] += 1
    extraction = company.extraction_record
    if extraction and (
        extraction.text_path or (extraction.table_count > 0 and extraction.table_path)
    ):
        stage_counts["extracted"] += 1
    if company.analysis_record is not None:
        stage_counts["analysed"] += 1
    verification = getattr(company, "verification", None)
    if verification and verification.status == "accepted":
        stage_counts["verified"] += 1


def summarise_documents(record: Optional[SearchRecord], doc_counter: Counter) -> None:
    if not record or not record.url:
        return
    doc_type = record.doc_type or classify_document_type(
        record.title, record.filename, record.url
    )
    year = record.year or "unknown"
    doc_counter[(doc_type, year)] += 1


def format_doc_summary(doc_counter: Counter) -> str:
    if not doc_counter:
        return "No search records available."
    lines = ["Document type/year breakdown:"]
    for (doc_type, year), count in sorted(doc_counter.items()):
        lines.append(f"  - {doc_type.title()} {year}: {count}")
    return "\n".join(lines)


def format_stage_summary(stage_counts: Counter, total: int) -> str:
    lines = ["Pipeline stage coverage:"]
    lines.append(f"  - Total companies: {total}")
    mapping = [
        ("searched", "Search records"),
        ("downloaded", "Downloaded"),
        ("extracted", "Extracted"),
        ("analysed", "Analysed"),
        ("verified", "Verified (accepted)"),
    ]
    for key, label in mapping:
        lines.append(f"  - {label}: {stage_counts.get(key, 0)} / {total}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(
            "Usage: python -m backend.domain.s0_stats <companies.json> [--write] [--pdf] [--checkyear] "
            "[--checkscope] [--delete] [--all] [--failed-analysis] [--reset[=STAGE[,STAGE...]]]",
            file=sys.stderr,
        )
        return 1

    path = Path(argv[1]).expanduser().resolve()
    args_list = argv[2:]
    write = False
    enforce_pdf_only = False
    check_pdf_year = False
    check_scope = False
    delete_scope = False
    show_all = False
    reset_only = False
    list_failed_analysis = False
    reset_requested: List[str] = []

    i = 0
    while i < len(args_list):
        arg = args_list[i]
        if arg == "--write":
            write = True
        elif arg == "--pdf":
            enforce_pdf_only = True
        elif arg == "--checkyear":
            check_pdf_year = True
        elif arg == "--checkscope":
            check_scope = True
        elif arg == "--delete":
            delete_scope = True
        elif arg == "--all":
            show_all = True
        elif arg == "--failed-analysis":
            list_failed_analysis = True
        elif arg.startswith("--reset"):
            reset_only = True
            value: Optional[str] = None
            if arg == "--reset":
                if i + 1 < len(args_list) and not args_list[i + 1].startswith("--"):
                    value = args_list[i + 1]
                    i += 1
            else:
                _, value = arg.split("=", 1)
            if value:
                for token in value.split(","):
                    stage_name = token.strip().lower()
                    if stage_name:
                        reset_requested.append(stage_name)
        else:
            print(f"[warn] ignored unknown argument {arg}", flush=True)
        i += 1

    if reset_requested:
        normalised: List[str] = []
        seen_reset: Set[str] = set()
        for stage in reset_requested:
            if stage not in seen_reset:
                normalised.append(stage)
                seen_reset.add(stage)
        reset_requested = normalised
    if delete_scope and not check_scope:
        print(
            "[warn] --delete has no effect without --checkscope; ignoring.",
            flush=True,
        )
        delete_scope = False

    companies, payload = load_companies(path)

    if reset_only:
        invalid_stages = [
            stage for stage in reset_requested if stage not in STAGE_DEPENDENCIES
        ]
        if invalid_stages:
            expected = ", ".join(name.upper() for name in STAGE_NAMES)
            print(
                "[error] Unknown reset stage(s): "
                + ", ".join(stage.upper() for stage in invalid_stages)
                + f". Expected one of: {expected}",
                flush=True,
            )
            return 1
        if not write:
            if reset_requested:
                suffix = "=" + ",".join(stage.upper() for stage in reset_requested)
                print(
                    f"[reset] No changes made. Re-run with --write --reset{suffix} to persist the cleanup.",
                    flush=True,
                )
            else:
                print(
                    "[reset] No changes made. Re-run with --write --reset to persist the cleanup.",
                    flush=True,
                )
            return 0

        stages_to_process = reset_requested or ["s4", "s5", "s6"]
        expanded_stages = _expand_stages(stages_to_process)
        reset_count = 0
        for company in companies:
            if reset_company_stages(company, stages_to_process):
                reset_count += 1
        if reset_count:
            dump_companies(path, payload, companies)
            stages_display = " -> ".join(stage.upper() for stage in expanded_stages)
            print(
                f"[reset] Cleared data for {stages_display} on {reset_count} company(ies).",
                flush=True,
            )
        else:
            if reset_requested:
                stages_display = " -> ".join(stage.upper() for stage in expanded_stages)
                print(
                    f"[reset] No {stages_display} data required clearing.",
                    flush=True,
                )
            else:
                print("[reset] No pipeline artefacts required clearing.", flush=True)
        return 0

    stage_counts: Counter = Counter()
    doc_counter: Counter = Counter()
    failed_analysis_companies: List[str] = []
    issues: list[Issue] = []
    any_changes = False
    scope_checked = 0
    scope_hit = 0
    scope_missing: List[str] = []
    scope_skipped = 0
    scope_deleted = 0

    raw_companies_raw = payload.get("companies", [])
    if isinstance(raw_companies_raw, list):
        raw_companies = raw_companies_raw
    else:
        issues.append(Issue("GLOBAL", "top-level 'companies' is not a list", False))
        raw_companies = []

    check_total = 0
    if check_pdf_year:
        for company in companies:
            if (
                company.search_record
                and company.download_record
                and company.download_record.pdf_path
            ):
                check_total += 1
        print(f"[checkyear] Eligible companies: {check_total}", flush=True)
    check_progress = 0

    for idx, company in enumerate(companies):
        ticker = company.identity.ticker or company.identity.name or f"company[{idx}]"

        raw_entry = raw_companies[idx] if idx < len(raw_companies) else None
        structure_issues: list[Issue] = []
        if isinstance(raw_entry, dict):
            structure_issues = validate_structure(raw_entry, Company, ticker, "")
        elif raw_entry is None:
            structure_issues = [
                Issue(ticker, "missing raw entry in companies list", False)
            ]
        else:
            structure_issues = [
                Issue(
                    ticker,
                    f"expected object for raw company entry but found {type(raw_entry).__name__}",
                    False,
                )
            ]
        issues.extend(structure_issues)
        if any(issue.fixed for issue in structure_issues):
            any_changes = True

        record_issues: Iterable[Issue] = []
        original_year = company.search_record.year if company.search_record else None

        pdf_name = "unknown"
        company_modified = False

        if company.search_record:
            pdf_path: Optional[Path] = None
            per_company_check = check_pdf_year and company.download_record is not None
            if per_company_check and company.download_record:
                candidate = _resolve_pdf_path(path, company.download_record.pdf_path)
                if candidate.exists():
                    pdf_path = candidate
                check_progress += 1
                pdf_name = (
                    candidate.name
                    if company.download_record and company.download_record.pdf_path
                    else "unknown"
                )
                print(
                    f"[checkyear] [{check_progress}/{check_total}] {ticker}: checking {pdf_name}",
                    flush=True,
                )
            changed, remove_record, record_issues = validate_search_record(
                company.search_record,
                ticker,
                enforce_pdf_only,
                per_company_check,
                pdf_path,
            )
            if per_company_check:
                new_year = company.search_record.year if company.search_record else None
                summary: str
                if remove_record:
                    reason = "; ".join(issue.message for issue in record_issues) or (
                        "search record removed"
                    )
                    summary = f"search record removed ({reason})"
                elif not pdf_path:
                    summary = "skipped year check (PDF not available)"
                else:
                    no_year_detected = any(
                        "no 20XX year found" in issue.message for issue in record_issues
                    )
                    future_issue = next(
                        (
                            issue
                            for issue in record_issues
                            if "ignored future year" in issue.message
                        ),
                        None,
                    )
                    future_year_display: Optional[str] = None
                    if future_issue:
                        match_future = re.search(r"20\d{2}", future_issue.message)
                        if match_future:
                            future_year_display = match_future.group(0)

                    future_suffix = (
                        f"; ignored future year {future_year_display} on first page"
                        if future_year_display
                        else ""
                    )

                    if new_year == original_year and not no_year_detected:
                        if new_year:
                            summary = (
                                f"existing year {new_year} confirmed from {pdf_name}"
                                f"{future_suffix}"
                            )
                        else:
                            summary = (
                                f"year remains unset for {pdf_name}{future_suffix}"
                            )
                    elif new_year != original_year:
                        summary = (
                            f"year changed {original_year or 'unknown'} -> {new_year or 'unknown'} "
                            f"from {pdf_name}"
                            f"{future_suffix}"
                        )
                    elif future_year_display:
                        summary = (
                            f"ignored future year {future_year_display} on first page of {pdf_name}; "
                            f"current year {new_year or 'unknown'}"
                        )
                    elif no_year_detected:
                        summary = f"no year detected on first page of {pdf_name}; current year {new_year or 'unknown'}"
                    else:
                        summary = f"year status unchanged for {pdf_name}{future_suffix}"
                print(
                    f"[checkyear] [{check_progress}/{check_total}] {ticker}: {summary}",
                    flush=True,
                )
            if remove_record:
                company.search_record = None
                company_modified = True
            if changed or remove_record:
                any_changes = True
                company_modified = True
        issues.extend(record_issues)
        if any(issue.fixed for issue in record_issues):
            any_changes = True

        summarise_stages(company, stage_counts)
        summarise_documents(company.search_record, doc_counter)
        if list_failed_analysis:
            has_download = company.download_record is not None and bool(
                company.download_record.pdf_path
            )
            extraction_record = company.extraction_record
            has_extraction = bool(
                extraction_record
                and (
                    extraction_record.text_path
                    or (
                        extraction_record.table_count > 0
                        and extraction_record.table_path
                    )
                )
            )
            if has_download and has_extraction and company.analysis_record is None:
                failed_analysis_companies.append(ticker)

        if check_scope:
            if not company.download_record or not company.download_record.pdf_path:
                scope_skipped += 1
                print(
                    f"[checkscope] {ticker}: skipped (no download record)",
                    flush=True,
                )
            else:
                scope_checked += 1
                scope_present = False
                scope_source = "unknown"
                scope_notes: List[str] = []

                snippet_candidates: List[Tuple[str, Path]] = []
                if company.extraction_record:
                    if company.extraction_record.text_path:
                        snippet_candidates.append(
                            ("text snippet", Path(company.extraction_record.text_path))
                        )
                    if company.extraction_record.table_path:
                        snippet_candidates.append(
                            (
                                "table snippet",
                                Path(company.extraction_record.table_path),
                            )
                        )

                for label, candidate_snippet in snippet_candidates:
                    snippet_path = candidate_snippet.expanduser()
                    if not snippet_path.is_absolute():
                        snippet_path = (path.parent / snippet_path).resolve()
                    if snippet_path.exists():
                        try:
                            snippet_text = snippet_path.read_text(encoding="utf-8")
                            if SCOPE_KEYWORDS_RE.search(snippet_text):
                                scope_present = True
                                scope_source = label
                                break
                        except OSError as exc:
                            scope_notes.append(f"{label} read error ({exc})")
                    else:
                        scope_notes.append(f"{label} missing")

                if not scope_present:
                    pdf_candidate = _resolve_pdf_path(
                        path, company.download_record.pdf_path
                    )
                    if pdf_candidate.exists():
                        pdf_pages = extract_pdf_text(
                            pdf_candidate, max_pages=SCOPE_SCAN_MAX_PAGES
                        )
                        if not pdf_pages:
                            scope_notes.append("no text extracted from PDF")
                        else:
                            for idx, page_text in enumerate(pdf_pages):
                                if page_text and SCOPE_KEYWORDS_RE.search(page_text):
                                    scope_present = True
                                    scope_source = f"pdf page {idx + 1}"
                                    break
                    else:
                        scope_notes.append("pdf missing on disk")

                if scope_present:
                    scope_hit += 1
                    print(
                        f"[checkscope] {ticker}: scope keywords found ({scope_source})",
                        flush=True,
                    )
                else:
                    scope_missing.append(ticker)
                    note_suffix = f" ({'; '.join(scope_notes)})" if scope_notes else ""
                    print(
                        f"[checkscope] {ticker}: scope keywords missing{note_suffix}",
                        flush=True,
                    )
                    if delete_scope:
                        deleted_records = False
                        if company.search_record is not None:
                            company.search_record = None
                            deleted_records = True
                        if company.download_record is not None:
                            company.download_record = None
                            deleted_records = True
                        if company.extraction_record is not None:
                            company.extraction_record = None
                            deleted_records = True
                        if deleted_records:
                            company_modified = True
                            any_changes = True
                            scope_deleted += 1
                            print(
                                f"[checkscope] {ticker}: cleared records due to missing scope keywords",
                                flush=True,
                            )

        if write and company_modified:
            dump_companies(path, payload, companies)
            print(
                f"[write] Flushed updates for {ticker} to {path.name}",
                flush=True,
            )

    if check_scope:
        print("\nScope keyword coverage:")
        print(f"  - Checked: {scope_checked}")
        print(f"  - With scope keywords: {scope_hit}")
        print(f"  - Missing scope keywords: {len(scope_missing)}")
        if scope_skipped:
            print(f"  - Skipped (no download): {scope_skipped}")
        if delete_scope:
            print(f"  - Records cleared: {scope_deleted}")
        if scope_missing:
            print(
                "  - Missing tickers: "
                + ", ".join(sorted(scope_missing))  # alphabetical for readability
            )

    if any_changes and write:
        dump_companies(path, payload, companies)
        print(f"[stats] Corrections saved to {path}")
    elif any_changes:
        print("[stats] Corrections available (run with --write to persist).")

    print(format_stage_summary(stage_counts, len(companies)))
    print()
    print(format_doc_summary(doc_counter))

    if list_failed_analysis:
        print("\nCompanies with downloads and extraction but no analysis:")
        if failed_analysis_companies:
            for name in sorted(set(failed_analysis_companies)):
                print(f"  - {name}")
        else:
            print("  - None")

    actionable = [issue for issue in issues if not issue.fixed]
    corrected = [issue for issue in issues if issue.fixed]

    if not show_all:
        actionable = [issue for issue in actionable if not issue.minor]
        corrected = [issue for issue in corrected if not issue.minor]

    if corrected:
        print("\nCorrections made:")
        for item in corrected:
            print(f"  - {item.ticker}: {item.message}")
    if actionable:
        print("\nOutstanding issues:")
        for item in actionable:
            print(f"  - {item.ticker}: {item.message}")

    if not any([corrected, actionable]):
        print("\nNo data issues detected.")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
