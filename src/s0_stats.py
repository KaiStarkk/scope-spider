from __future__ import annotations

import sys
from collections import Counter
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Tuple, Type, get_args, get_origin, Literal

from pydantic import BaseModel

from src.models import Company, SearchRecord
from src.utils.companies import dump_companies, load_companies
from src.utils.documents import (
    MAX_REPORT_YEAR,
    classify_document_type,
    infer_year_from_text,
)
from src.utils.documents import normalise_pdf_url  # type: ignore[attr-defined]
from src.utils.pdf import extract_pdf_text
from src.utils.query import derive_filename
from src.utils.status import emissions_complete


@dataclass
class Issue:
    ticker: str
    message: str
    fixed: bool = False


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
        issues.append(Issue(ticker, "search URL does not end with .pdf", False))

    derived_type = classify_document_type(record.title, record.filename, record.url)
    if record.doc_type != derived_type:
        issues.append(Issue(ticker, f"doc_type set to {derived_type!r}", True))
        record.doc_type = derived_type
        changes = True

    inferred_year = infer_year_from_text(record.title, record.filename, record.url)
    if inferred_year:
        if record.year != inferred_year:
            issues.append(Issue(ticker, f"year set to {inferred_year}", True))
            record.year = inferred_year
            changes = True
    elif not record.year:
        issues.append(Issue(ticker, "year missing and could not be inferred", False))
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
            pages = extract_pdf_text(pdf_path)[:1]
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
    types: list[type] = []
    for arg in get_args(annotation):
        types.extend(_expected_scalar_types(arg))
    # Preserve order without duplicates
    seen: dict[type, None] = {}
    for typ in types:
        if typ not in seen:
            seen[typ] = None
    return tuple(seen.keys())


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

        sub_model = _extract_base_model(field.annotation)
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

        expected_types = _expected_scalar_types(field.annotation)
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
    record = company.search_record
    if record and record.url:
        stage_counts["searched"] += 1
    download = company.download_record
    if download and download.pdf_path:
        stage_counts["downloaded"] += 1
    extraction = company.extraction_record
    if extraction and extraction.text_path:
        stage_counts["extracted"] += 1
    if emissions_complete(company.emissions):
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
    mapping = [
        ("searched", "Search records"),
        ("downloaded", "Downloaded"),
        ("extracted", "Extracted"),
        ("verified", "Verified"),
    ]
    for key, label in mapping:
        lines.append(f"  - {label}: {stage_counts.get(key, 0)} / {total}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(
            "Usage: python -m src.s0_stats <companies.json> [--write] [--pdf] [--checkyear]",
            file=sys.stderr,
        )
        return 1

    path = Path(argv[1]).expanduser().resolve()
    write = "--write" in argv[2:]
    enforce_pdf_only = "--pdf" in argv[2:]
    check_pdf_year = "--checkyear" in argv[2:]

    companies, payload = load_companies(path)

    stage_counts: Counter = Counter()
    doc_counter: Counter = Counter()
    issues: list[Issue] = []
    any_changes = False

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
        ticker = company.identity.ticker or f"company[{idx}]"

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

        if write and company_modified:
            dump_companies(path, payload, companies)
            print(
                f"[write] Flushed updates for {ticker} to {path.name}",
                flush=True,
            )

    if any_changes and write:
        dump_companies(path, payload, companies)
        print(f"[stats] Corrections saved to {path}")
    elif any_changes:
        print("[stats] Corrections available (run with --write to persist).")

    print(format_stage_summary(stage_counts, len(companies)))
    print()
    print(format_doc_summary(doc_counter))

    actionable = [issue for issue in issues if not issue.fixed]
    corrected = [issue for issue in issues if issue.fixed]

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
