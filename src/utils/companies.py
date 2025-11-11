from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Tuple

from ..models import Company


def load_companies(path: Path) -> Tuple[List[Company], Dict[str, object]]:
    raw_text = path.read_text(encoding="utf-8") if path.exists() else "{}"
    payload = json.loads(raw_text or "{}")
    companies_data = payload.get("companies") or []
    if not isinstance(companies_data, list):
        raise ValueError("Input JSON must contain a 'companies' list.")
    companies = [Company.model_validate(item) for item in companies_data]
    return companies, payload


def dump_companies(
    path: Path, payload: Dict[str, object], companies: List[Company]
) -> None:
    payload["companies"] = [
        company.model_dump(exclude_none=True) for company in companies
    ]
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        handle.write(serialized)
        handle.flush()
        os.fsync(handle.fileno())
