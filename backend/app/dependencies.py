from __future__ import annotations

from functools import lru_cache

from .config import get_settings
from .services.companies import CompanyRepository


@lru_cache
def get_company_repository() -> CompanyRepository:
    settings = get_settings()
    return CompanyRepository(settings.companies_file)
