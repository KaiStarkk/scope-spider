from __future__ import annotations

from typing import Any, Dict, List, Optional, cast

from fastapi import APIRouter, Depends, HTTPException, Query

from ..config import get_settings
from ..dependencies import get_company_repository
from ..models.verification import (
    VerificationDecision,
    VerificationOverride,
    VerificationReject,
)
from ..services.companies import CompanyRepository
from ..services import verification as _verification_module

verification_service = cast(Any, _verification_module)

router = APIRouter(prefix="/api/verification", tags=["verification"])


@router.get("/next")
def get_next_company(
    current_key: Optional[str] = Query(default=None),
    methods: Optional[List[str]] = Query(default=None),
    skip_current: bool = Query(default=False),
    repository: CompanyRepository = Depends(get_company_repository),
) -> Dict[str, Optional[str]]:
    """Return the next pending company key for verification."""

    companies, _ = repository.list_companies()
    allowed = {method for method in methods or [] if method}
    next_key = verification_service.next_pending_key(
        companies,
        current_key=current_key,
        skip_current=skip_current,
        allowed_methods=allowed or None,
    )
    return {"key": next_key}


@router.get("/options")
def get_verification_options(
    repository: CompanyRepository = Depends(get_company_repository),
) -> Dict[str, Any]:
    companies, _ = repository.list_companies()
    methods = verification_service.list_analysis_methods(companies)
    return {"methods": methods}


@router.get("/{target_key}")
def get_company_verification(
    target_key: str,
    repository: CompanyRepository = Depends(get_company_repository),
) -> Dict[str, Any]:
    """Return verification payload for a specific company."""

    settings = get_settings()
    companies, _ = repository.list_companies()
    target = next(
        (
            company
            for company in companies
            if verification_service.company_key(company) == target_key
        ),
        None,
    )
    if target is None:
        raise HTTPException(status_code=404, detail="Company not found.")

    payload = verification_service.build_verification_payload(
        target,
        data_root=settings.data_root,
        downloads_dir=settings.downloads_dir,
    )
    return payload


@router.post("/{target_key}/accept")
def accept_company(
    target_key: str,
    decision: VerificationDecision,
    methods: Optional[List[str]] = Query(default=None),
    repository: CompanyRepository = Depends(get_company_repository),
):
    """Mark a company as accepted and persist the decision."""

    settings = get_settings()
    allowed = {method for method in methods or [] if method}

    def mutator(companies, _payload):
        target = next(
            (
                company
                for company in companies
                if verification_service.company_key(company) == target_key
            ),
            None,
        )
        if target is None:
            raise HTTPException(status_code=404, detail="Company not found.")
        verification_service.apply_accept(
            target,
            notes=decision.notes,
            reviewer=decision.reviewer,
        )
        company_payload = verification_service.build_verification_payload(
            target,
            data_root=settings.data_root,
            downloads_dir=settings.downloads_dir,
        )
        next_key = verification_service.next_pending_key(
            companies,
            target_key,
            skip_current=False,
            allowed_methods=allowed or None,
        )
        return {
            "company": company_payload,
            "message": "Verification accepted.",
            "next_key": next_key,
        }

    return repository.mutate(mutator)


@router.post("/{target_key}/reject")
def reject_company(
    target_key: str,
    request: VerificationReject,
    methods: Optional[List[str]] = Query(default=None),
    repository: CompanyRepository = Depends(get_company_repository),
) -> Dict[str, Any]:
    """Reject a verification result and reset pipeline stages."""

    settings = get_settings()
    allowed = {method for method in methods or [] if method}

    def mutator(companies, _payload):
        target = next(
            (
                company
                for company in companies
                if verification_service.company_key(company) == target_key
            ),
            None,
        )
        if target is None:
            raise HTTPException(status_code=404, detail="Company not found.")
        message = verification_service.apply_reject(
            target,
            base_dir=settings.data_root,
            downloads_dir=settings.downloads_dir,
            new_url=request.replacement_url,
            upload_contents=request.upload_contents,
            upload_filename=request.upload_filename,
            notes=request.notes,
            reviewer=request.reviewer,
        )
        company_payload = verification_service.build_verification_payload(
            target,
            data_root=settings.data_root,
            downloads_dir=settings.downloads_dir,
        )
        next_key = verification_service.next_pending_key(
            companies,
            target_key,
            skip_current=True,
            allowed_methods=allowed or None,
        )
        return {
            "company": company_payload,
            "message": message,
            "next_key": next_key,
        }

    return repository.mutate(mutator)


@router.post("/{target_key}/override")
def override_company(
    target_key: str,
    request: VerificationOverride,
    methods: Optional[List[str]] = Query(default=None),
    repository: CompanyRepository = Depends(get_company_repository),
) -> Dict[str, Any]:
    """Apply manual overrides for scope values and accept the verification."""

    settings = get_settings()
    allowed = {method for method in methods or [] if method}

    def mutator(companies, _payload):
        target = next(
            (
                company
                for company in companies
                if verification_service.company_key(company) == target_key
            ),
            None,
        )
        if target is None:
            raise HTTPException(status_code=404, detail="Company not found.")
        verification_service.apply_manual_override(
            target,
            scope1=request.scope_1,
            scope2=request.scope_2,
            scope3=request.scope_3,
            notes=request.notes,
            reviewer=request.reviewer,
        )
        company_payload = verification_service.build_verification_payload(
            target,
            data_root=settings.data_root,
            downloads_dir=settings.downloads_dir,
        )
        next_key = verification_service.next_pending_key(
            companies,
            target_key,
            skip_current=False,
            allowed_methods=allowed or None,
        )
        return {
            "company": company_payload,
            "message": "Manual corrections saved and accepted.",
            "next_key": next_key,
        }

    return repository.mutate(mutator)
