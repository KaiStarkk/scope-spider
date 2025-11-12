import re
from typing import Literal, Optional

from pydantic import BaseModel, field_validator


class SearchRecord(BaseModel):
    url: str
    title: str
    filename: str
    year: Optional[str] = None
    doc_type: Optional[Literal["annual", "sustainability", "other"]] = None

    @field_validator("url", "title", "filename", "year", mode="before")
    @classmethod
    def _strip_fields(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return str(value).strip()

    @field_validator("year")
    @classmethod
    def _validate_year(cls, value: Optional[str]) -> Optional[str]:
        if value is None or value == "":
            return None
        if not re.fullmatch(r"\d{4}", value):
            raise ValueError("year must be four digits (YYYY)")
        return value


__all__ = [
    "SearchRecord",
]
