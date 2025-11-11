from __future__ import annotations

import re
from typing import Literal, Optional
from urllib.parse import urlparse, urlunparse


MIN_REPORT_YEAR = 2000
MAX_REPORT_YEAR = 2025


ANNUAL_KEYWORDS = (
    "annual report",
    "annual-report",
    "annualreport",
    "annualreview",
    "annual review",
)

SUSTAINABILITY_KEYWORDS = (
    "sustainability",
    "esg",
    "climate",
    "tcfd",
    "responsibility",
    "csr",
)


def classify_document_type(
    title: str, filename: str, url: str
) -> Literal["annual", "sustainability", "other"]:
    text = f"{title} {filename} {url}".lower()
    if any(keyword in text for keyword in SUSTAINABILITY_KEYWORDS):
        return "sustainability"
    if any(keyword in text for keyword in ANNUAL_KEYWORDS):
        return "annual"
    return "other"


def infer_year_from_text(*sources: str) -> Optional[str]:
    candidate_years: list[int] = []
    for source in sources:
        if not source:
            continue
        lowered = source.lower()

        for match in re.findall(r"\b(20\d{2})\b", lowered):
            try:
                value = int(match)
            except ValueError:
                continue
            if MIN_REPORT_YEAR <= value <= MAX_REPORT_YEAR:
                candidate_years.append(value)

        for match in re.findall(r"\bfy\s*(?:20)?(\d{2})\b", lowered):
            try:
                value = int(match)
            except ValueError:
                continue
            if value < 0 or value > 99:
                continue
            inferred = 2000 + value
            if MIN_REPORT_YEAR <= inferred <= MAX_REPORT_YEAR:
                candidate_years.append(inferred)

    if not candidate_years:
        return None
    return str(max(candidate_years))


def normalise_pdf_url(raw_url: str | None) -> tuple[str, bool]:
    """Trim whitespace, drop query/fragment, and report if the path ends with '.pdf'."""
    if raw_url is None:
        return "", False

    trimmed = raw_url.strip()
    if not trimmed:
        return "", False

    parsed = urlparse(trimmed)
    path = (parsed.path or "").strip()
    is_pdf = path.lower().endswith(".pdf")
    sanitised = urlunparse(parsed._replace(path=path, query="", fragment=""))

    return sanitised, is_pdf


__all__ = [
    "MAX_REPORT_YEAR",
    "MIN_REPORT_YEAR",
    "classify_document_type",
    "infer_year_from_text",
    "normalise_pdf_url",
]
