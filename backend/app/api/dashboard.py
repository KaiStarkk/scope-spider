from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, Query

from ..dependencies import get_company_repository
from ..services.companies import CompanyRepository
from ..services.dashboard import (
    DashboardFilters,
    build_dashboard_metrics,
    company_stage_summary,
)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/companies")
def list_companies(
    repository: CompanyRepository = Depends(get_company_repository),
) -> Dict[str, Any]:
    """Return the companies payload along with summary statistics."""

    companies, payload = repository.list_companies()
    serialised_companies: List[Dict[str, Any]] = [
        company.model_dump(mode="json") for company in companies
    ]

    stage_counts = company_stage_summary(companies)
    stats = {
        "total": stage_counts["total"],
        "searched": stage_counts["searched"],
        "downloaded": stage_counts["downloaded"],
        "extracted": stage_counts["extracted"],
        "analysed": stage_counts["analysed"],
        "verified": stage_counts["verified"],
        "pending": stage_counts["total"] - stage_counts["verified"],
        "stages": stage_counts,
    }

    extra_metadata = {key: value for key, value in payload.items() if key != "companies"}

    return {
        "companies": serialised_companies,
        "stats": stats,
        "metadata": extra_metadata,
    }


@router.get("/metrics")
def dashboard_metrics(
    industries: Optional[List[str]] = Query(default=None),
    rbics: Optional[List[str]] = Query(default=None),
    states: Optional[List[str]] = Query(default=None),
    methods: Optional[List[str]] = Query(default=None),
    scope1_min: Optional[float] = Query(default=None),
    scope1_max: Optional[float] = Query(default=None),
    net_income_min: Optional[float] = Query(default=None),
    net_income_max: Optional[float] = Query(default=None),
    revenue_min: Optional[float] = Query(default=None),
    revenue_max: Optional[float] = Query(default=None),
    repository: CompanyRepository = Depends(get_company_repository),
) -> Dict[str, Any]:
    companies, _ = repository.list_companies()

    def to_range(min_value: Optional[float], max_value: Optional[float]) -> Optional[Tuple[float, float]]:
        if min_value is None or max_value is None:
            return None
        if min_value > max_value:
            min_value, max_value = max_value, min_value
        return (float(min_value), float(max_value))

    filters = DashboardFilters(
        industries=industries or None,
        rbics=rbics or None,
        states=states or None,
        methods=methods or None,
        scope1_range=to_range(scope1_min, scope1_max),
        net_income_range=to_range(net_income_min, net_income_max),
        revenue_range=to_range(revenue_min, revenue_max),
    )
    return build_dashboard_metrics(companies, filters)
