from __future__ import annotations

import base64
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from fastapi import HTTPException

from src.models import Company
from src.models import (
    AnalysisRecord,
    DownloadRecord,
    Scope2Emissions,
    Scope3Emissions,
    ScopeValue,
    SearchRecord,
)
from src.s0_stats import STAGE_DEPENDENCIES, reset_company_stages
from src.utils.documents import (
    classify_document_type,
    infer_year_from_text,
    normalise_pdf_url,
)
from src.utils.pdf_preview import previews_as_data_urls
from src.utils.query import derive_filename

__all__ = [
    "company_key",
    "next_pending_key",
    "build_verification_payload",
    "apply_accept",
    "apply_reject",
    "apply_manual_override",
    "list_analysis_methods",
]


def company_key(company: Company) -> str:
    identity = company.identity
    return (identity.ticker or identity.name or "").strip()


def next_pending_key(
    companies: Iterable[Company],
    current_key: Optional[str] = None,
    *,
    skip_current: bool = False,
    allowed_methods: Optional[set[str]] = None,
) -> Optional[str]:
    pending_keys: List[str] = []
    for company in companies:
        if company.verification and company.verification.status == "accepted":
            continue
        method = company.analysis_record.method if company.analysis_record else None
        if allowed_methods and method not in allowed_methods:
            continue
        key = company_key(company)
        if key:
            pending_keys.append(key)
    if not pending_keys:
        return None
    if current_key not in pending_keys:
        return pending_keys[0]
    if skip_current:
        if len(pending_keys) == 1:
            return pending_keys[0]
        current_index = pending_keys.index(current_key)
        return pending_keys[(current_index + 1) % len(pending_keys)]
    return current_key


def _resolve_path(base_dir: Path, candidate: Optional[str]) -> Optional[Path]:
    if not candidate:
        return None
    path = Path(candidate)
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    return path


def _read_text_file(path: Optional[Path]) -> Optional[str]:
    if not path:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def build_verification_payload(
    company: Company,
    *,
    data_root: Path,
    downloads_dir: Path,
) -> Dict[str, Any]:
    identity = company.identity
    annotations = company.annotations
    emissions = company.emissions
    verification = company.verification
    analysis = company.analysis_record

    scope1 = emissions.scope_1.value if emissions.scope_1 else None
    scope2 = emissions.scope_2.value if emissions.scope_2 else None
    scope3 = emissions.scope_3.value if emissions.scope_3 else None
    scope1_conf = emissions.scope_1.confidence if emissions.scope_1 else None
    scope2_conf = emissions.scope_2.confidence if emissions.scope_2 else None

    verified_at = (
        verification.verified_at.isoformat(timespec="seconds")
        if verification and verification.verified_at
        else None
    )

    snippet_path = _resolve_path(data_root, analysis.snippet_path if analysis else None)
    snippet_text = _read_text_file(snippet_path)

    pdf_path = None
    if company.download_record and company.download_record.pdf_path:
        pdf_path = _resolve_path(downloads_dir, company.download_record.pdf_path)
        if pdf_path is None:
            pdf_path = _resolve_path(data_root, company.download_record.pdf_path)

    previews: List[Tuple[int, str]] = []
    if pdf_path and analysis and analysis.snippet_pages:
        previews = previews_as_data_urls(pdf_path, analysis.snippet_pages)

    return {
        "key": company_key(company),
        "identity": {
            "ticker": identity.ticker,
            "name": identity.name,
        },
        "verification": {
            "status": verification.status if verification else "pending",
            "verified_at": verified_at,
            "notes": verification.notes if verification else None,
            "overrides": {
                "scope_1": verification.scope_1_override if verification else None,
                "scope_2": verification.scope_2_override if verification else None,
                "scope_3": verification.scope_3_override if verification else None,
            },
        },
        "annotations": {
            "reporting_group": annotations.reporting_group,
            "location": annotations.company_state
            or annotations.company_region
            or annotations.company_country,
            "rbics_sector": annotations.rbics_sector,
        },
        "emissions": {
            "scope_1": scope1,
            "scope_2": scope2,
            "scope_3": scope3,
            "scope_1_confidence": scope1_conf,
            "scope_2_confidence": scope2_conf,
        },
        "analysis": {
            "method": analysis.method if analysis else None,
            "confidence": analysis.confidence if analysis else None,
            "snippet_label": analysis.snippet_label if analysis else None,
            "snippet_pages": analysis.snippet_pages if analysis else [],
        },
        "snippet": {
            "path": str(snippet_path) if snippet_path else None,
            "text": snippet_text,
        },
        "previews": [
            {"page": page, "data_url": data_url} for page, data_url in previews
        ],
    }


def apply_accept(
    company: Company,
    *,
    notes: Optional[str] = None,
    reviewer: Optional[str] = None,
) -> None:
    verification = company.verification
    verification.status = "accepted"
    verification.notes = notes or None
    verification.reviewer = reviewer or verification.reviewer
    verification.verified_at = datetime.utcnow()


def _ordered_stage_dependencies(stages: Sequence[str]) -> List[str]:
    ordered: List[str] = []
    seen: set[str] = set()
    for stage in stages:
        for dependent in STAGE_DEPENDENCIES.get(stage, ()):
            if dependent not in seen:
                ordered.append(dependent)
                seen.add(dependent)
    return ordered


def _clean_company_token(value: Optional[str]) -> str:
    text = (value or "").strip() or "company"
    return re.sub(r"[^A-Za-z0-9]+", "_", text)


def _normalise_upload_filename(filename: Optional[str]) -> str:
    if not filename:
        return "uploaded.pdf"
    name = Path(filename).name or "uploaded.pdf"
    if not name.lower().endswith(".pdf"):
        name = f"{name}.pdf"
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name)


def _decode_uploaded_pdf(contents: str) -> bytes:
    if not contents:
        raise ValueError("No upload payload provided.")
    try:
        header, encoded = contents.split(",", 1)
    except ValueError as exc:  # pragma: no cover - malformed payload
        raise ValueError("Uploaded file payload is malformed.") from exc
    if "pdf" not in header.lower():
        raise ValueError("Uploaded file does not appear to be a PDF.")
    try:
        data = base64.b64decode(encoded)
    except ValueError as exc:  # pragma: no cover - malformed base64
        raise ValueError("Failed to decode uploaded PDF.") from exc
    if not data.startswith(b"%PDF"):
        raise ValueError("Uploaded file is not a valid PDF document.")
    return data


def _store_uploaded_pdf(
    base_dir: Path,
    downloads_dir: Path,
    company: Company,
    filename: Optional[str],
    data: bytes,
) -> Path:
    safe_ticker = _clean_company_token(company.identity.ticker)
    safe_name = _clean_company_token(company.identity.name)
    identifier = safe_ticker or safe_name or "company"
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    sanitized_filename = _normalise_upload_filename(filename)

    download_dir = downloads_dir.resolve()
    download_dir.mkdir(parents=True, exist_ok=True)

    destination = download_dir / f"{identifier}_{timestamp}_{sanitized_filename}"
    destination.write_bytes(data)

    try:
        relative = destination.relative_to(base_dir)
        return relative
    except ValueError:
        return destination


def apply_reject(
    company: Company,
    *,
    base_dir: Path,
    downloads_dir: Path,
    new_url: Optional[str] = None,
    upload_contents: Optional[str] = None,
    upload_filename: Optional[str] = None,
    notes: Optional[str] = None,
    reviewer: Optional[str] = None,
) -> str:
    stages_requested: List[str] = []
    sanitized_url: Optional[str] = None
    upload_bytes: Optional[bytes] = None

    if new_url:
        candidate_url, is_pdf = normalise_pdf_url(new_url)
        parsed = None
        if candidate_url:
            from urllib.parse import urlparse

            parsed = urlparse(candidate_url)
        if (
            not candidate_url
            or not is_pdf
            or parsed is None
            or parsed.scheme not in ("http", "https")
            or not parsed.netloc
        ):
            raise HTTPException(
                status_code=400,
                detail="Provide a valid PDF URL (http/https ending with .pdf) before rejecting.",
            )
        sanitized_url = candidate_url
        stages_requested.append("s2")

    if upload_contents:
        try:
            upload_bytes = _decode_uploaded_pdf(upload_contents)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if "s2" not in stages_requested:
            stages_requested.append("s3")

    if not stages_requested:
        raise HTTPException(
            status_code=400,
            detail="Provide a replacement PDF URL or upload a new PDF before rejecting.",
        )

    reset_company_stages(company, stages_requested)

    verification = company.verification

    if sanitized_url:
        filename = derive_filename(
            sanitized_url,
            company.search_record.filename if company.search_record else "",
        )
        title = company.identity.name or filename
        inferred_year = infer_year_from_text(title, filename, sanitized_url)
        doc_type = classify_document_type(title, filename, sanitized_url)
        company.search_record = SearchRecord(
            url=sanitized_url,
            title=title,
            filename=filename,
            year=inferred_year,
            doc_type=doc_type,
        )

    if upload_bytes is not None:
        stored_path = _store_uploaded_pdf(
            base_dir,
            downloads_dir,
            company,
            upload_filename,
            upload_bytes,
        )
        company.download_record = DownloadRecord(pdf_path=str(stored_path))

    ordered = _ordered_stage_dependencies(stages_requested)
    stage_display = ", ".join(stage.upper() for stage in ordered)
    verification.status = "rejected"
    verification.verified_at = datetime.utcnow()
    verification.notes = notes or None
    verification.reviewer = reviewer or verification.reviewer
    verification.scope_1_override = None
    verification.scope_2_override = None
    verification.scope_3_override = None

    return (
        f"Verification rejected. Pipeline reset: {stage_display}."
        if stage_display
        else "Verification rejected."
    )


def apply_manual_override(
    company: Company,
    *,
    scope1: int,
    scope2: int,
    scope3: Optional[int],
    notes: Optional[str] = None,
    reviewer: Optional[str] = None,
) -> None:
    verification = company.verification
    emissions = company.emissions

    if emissions.scope_1 is None:
        emissions.scope_1 = ScopeValue(value=scope1, confidence=1.0)
    else:
        emissions.scope_1.value = scope1
        emissions.scope_1.confidence = 1.0

    if emissions.scope_2 is None:
        emissions.scope_2 = Scope2Emissions(value=scope2, confidence=1.0, method="manual")
    else:
        emissions.scope_2.value = scope2
        emissions.scope_2.confidence = 1.0
        emissions.scope_2.method = emissions.scope_2.method or "manual"

    if scope3 is not None:
        if emissions.scope_3 is None:
            emissions.scope_3 = Scope3Emissions(value=scope3, confidence=1.0)
        else:
            emissions.scope_3.value = scope3
            emissions.scope_3.confidence = 1.0
    elif emissions.scope_3 is not None:
        emissions.scope_3.confidence = emissions.scope_3.confidence or 1.0

    previous_record = company.analysis_record
    company.analysis_record = AnalysisRecord(
        method="manual-override",
        snippet_label=previous_record.snippet_label if previous_record else "manual",
        snippet_path=previous_record.snippet_path if previous_record else None,
        snippet_pages=previous_record.snippet_pages if previous_record else [],
        confidence=1.0,
    )

    verification.status = "accepted"
    verification.verified_at = datetime.utcnow()
    verification.scope_1_override = scope1
    verification.scope_2_override = scope2
    verification.scope_3_override = scope3
    verification.notes = notes or None
    verification.reviewer = reviewer or verification.reviewer


def list_analysis_methods(companies: Iterable[Company]) -> List[str]:
    methods = {
        str(company.analysis_record.method).strip()
        for company in companies
        if company.analysis_record and company.analysis_record.method
    }
    return sorted(filter(None, methods))
