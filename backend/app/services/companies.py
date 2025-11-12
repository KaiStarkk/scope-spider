from __future__ import annotations

from pathlib import Path
from threading import Lock
from typing import Callable, Dict, List, Tuple, TypeVar

from fastapi import HTTPException

from src.models import Company
from src.utils.companies import dump_companies, load_companies


T = TypeVar("T")


class CompanyRepository:
    """Thin repository around the companies.json payload."""

    def __init__(self, companies_path: Path) -> None:
        self._companies_path = companies_path
        self._lock = Lock()

    @property
    def path(self) -> Path:
        return self._companies_path

    def _load(self) -> Tuple[List[Company], Dict[str, object]]:
        try:
            companies, payload = load_companies(self._companies_path)
        except ValueError as exc:
            raise HTTPException(status_code=500, detail=f"Failed to parse companies file: {exc}") from exc
        return companies, payload

    def list_companies(self) -> Tuple[List[Company], Dict[str, object]]:
        return self._load()

    def save_companies(self, companies: List[Company], payload: Dict[str, object]) -> None:
        with self._lock:
            dump_companies(self._companies_path, payload, companies)

    def mutate(
        self,
        mutator: Callable[[List[Company], Dict[str, object]], T],
    ) -> T:
        with self._lock:
            companies, payload = self._load()
            result = mutator(companies, payload)
            dump_companies(self._companies_path, payload, companies)
            return result
