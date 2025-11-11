from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..models import Company, EmissionsData


def _path_exists(path: Optional[str]) -> bool:
    if not path:
        return False
    return Path(path).expanduser().resolve().exists()


def emissions_complete(emissions: Optional[EmissionsData]) -> bool:
    if emissions is None:
        return False
    if emissions.scope_1 is None or emissions.scope_1 <= 0:
        return False
    scope_2_value = emissions.scope_2.value if emissions.scope_2 else None
    if scope_2_value is None or scope_2_value <= 0:
        return False
    return True


def needs_search(company: Company) -> bool:
    record = company.search_record
    return not (record and record.url)


def needs_download(company: Company, verify_path: bool = True) -> bool:
    if needs_search(company):
        return False
    record = company.download_record
    pdf_path = record.pdf_path if record else None
    if not pdf_path:
        return True
    if verify_path and not _path_exists(pdf_path):
        return True
    return False


def needs_extraction(company: Company, verify_path: bool = True) -> bool:
    if needs_download(company, verify_path=verify_path):
        return False
    record = company.extraction_record
    json_path = record.json_path if record else None
    if not json_path:
        return True
    if verify_path and not _path_exists(json_path):
        return True
    return False


def needs_verification(company: Company) -> bool:
    return not emissions_complete(company.emissions)
