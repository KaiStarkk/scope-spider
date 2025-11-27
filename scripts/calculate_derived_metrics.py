from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import PyPDF2

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_COMPANIES_PATH = ROOT_DIR / "companies.json"

PROFITABILITY_FIELD = "profitability_ratio"
REPUTATIONAL_FIELD = "reputational_concern_ratio"
PROFITABILITY_EMISSIONS_FIELD = "profitability_emissions_ratio"
EBITDA_EMISSIONS_FIELD = "ebitda_emissions_ratio"
MENTIONS_PER_PAGE_FIELD = "net_zero_mentions_per_page"


@dataclass
class UpdateStats:
    processed: int = 0
    profitability_updates: int = 0
    profitability_cleared: int = 0
    reputational_updates: int = 0
    reputational_cleared: int = 0
    profitability_emissions_updates: int = 0
    profitability_emissions_cleared: int = 0
    ebitda_emissions_updates: int = 0
    ebitda_emissions_cleared: int = 0
    mentions_per_page_updates: int = 0
    mentions_per_page_cleared: int = 0

    def as_lines(self) -> Tuple[str, ...]:
        return (
            f"[info] Processed {self.processed} companies.",
            f"[info] Profitability ratio updated: {self.profitability_updates} (cleared {self.profitability_cleared}).",
            f"[info] Reputational concern ratio updated: {self.reputational_updates} (cleared {self.reputational_cleared}).",
            f"[info] Profitability/Emissions ratio updated: {self.profitability_emissions_updates} (cleared {self.profitability_emissions_cleared}).",
            f"[info] EBITDA/Emissions ratio updated: {self.ebitda_emissions_updates} (cleared {self.ebitda_emissions_cleared}).",
            f"[info] Mentions/Page ratio updated: {self.mentions_per_page_updates} (cleared {self.mentions_per_page_cleared}).",
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Calculate derived profitability and reputational concern metrics in companies.json."
    )
    parser.add_argument(
        "--companies-path",
        type=Path,
        default=DEFAULT_COMPANIES_PATH,
        help="Path to companies.json (default: %(default)s).",
    )
    parser.add_argument(
        "--precision",
        type=int,
        default=6,
        help="Decimal precision used when storing ratios (default: %(default)s).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist calculated metrics back to companies.json. Defaults to dry-run.",
    )
    return parser.parse_args()


def _to_number(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if math.isnan(value):
            return None
        return float(value)
    if isinstance(value, str):
        stripped = value.strip().replace(",", "")
        if not stripped:
            return None
        try:
            parsed = float(stripped)
        except ValueError:
            return None
        if math.isnan(parsed):
            return None
        return parsed
    return None


def _safe_ratio(
    numerator: Optional[float], denominator: Optional[float], precision: int
) -> Optional[float]:
    if numerator is None or denominator is None:
        return None
    if denominator == 0 or not math.isfinite(denominator):
        return None
    ratio = numerator / denominator
    if not math.isfinite(ratio):
        return None
    return round(ratio, precision)


def _assign_value(target: Dict[str, Any], key: str, value: Optional[float], precision: int) -> Tuple[bool, bool]:
    previous = target.get(key)
    if value is None:
        if key in target:
            del target[key]
            return True, True
        return False, False
    needs_update = True
    if isinstance(previous, (int, float)) and not isinstance(previous, bool):
        tolerance = 10 ** -(precision + 2)
        if math.isclose(float(previous), value, rel_tol=0.0, abs_tol=tolerance):
            needs_update = False
    if not needs_update:
        return False, False
    target[key] = value
    return True, False


def get_page_count(pdf_rel_path: str) -> Optional[int]:
    if not pdf_rel_path:
        return None
    path = ROOT_DIR / pdf_rel_path
    if not path.exists():
        return None
    try:
        with path.open("rb") as f:
            reader = PyPDF2.PdfReader(f)
            return len(reader.pages)
    except Exception:
        return None


def update_companies(payload: Dict[str, Any], precision: int) -> UpdateStats:
    companies = payload.get("companies")
    if not isinstance(companies, list):
        raise ValueError("companies.json payload must contain a 'companies' list.")

    stats = UpdateStats(processed=len(companies))
    for company in companies:
        annotations = company.get("annotations")
        if not isinstance(annotations, dict):
            continue

        revenue = _to_number(annotations.get("profitability_revenue_mm_aud"))
        net_income = _to_number(annotations.get("profitability_net_income_mm_aud"))
        ebitda_mm = _to_number(annotations.get("profitability_ebitda_mm_aud"))
        net_zero_mentions = _to_number(annotations.get("net_zero_claims"))

        profitability_ratio = _safe_ratio(net_income, revenue, precision)
        reputational_ratio = _safe_ratio(net_zero_mentions, revenue, precision)

        # Calculate P/E (profitability / scope 1+2)
        profitability_emissions_ratio = None
        ebitda_emissions_ratio = None

        emissions = company.get("emissions") or {}
        total_emissions = None
        if isinstance(emissions, dict):
            s1 = _to_number(emissions.get("scope_1", {}).get("value"))
            s2 = _to_number(emissions.get("scope_2", {}).get("value"))
            if s1 is not None or s2 is not None:
                total_emissions = (s1 or 0.0) + (s2 or 0.0)

        if total_emissions is not None:
            if profitability_ratio is not None:
                profitability_emissions_ratio = _safe_ratio(profitability_ratio, total_emissions, precision + 10)

            if ebitda_mm is not None:
                # EBITDA ($) / Emissions
                ebitda_dollars = ebitda_mm * 1_000_000
                ebitda_emissions_ratio = _safe_ratio(ebitda_dollars, total_emissions, precision + 6)

        # Calculate mentions per page
        net_zero_mentions_per_page = None
        if net_zero_mentions is not None:
            download_record = company.get("download_record")
            if isinstance(download_record, dict):
                pdf_path = download_record.get("pdf_path")
                if pdf_path:
                    page_count = get_page_count(pdf_path)
                    if page_count:
                        net_zero_mentions_per_page = _safe_ratio(net_zero_mentions, page_count, precision)

        # Update fields
        updated, cleared = _assign_value(annotations, PROFITABILITY_FIELD, profitability_ratio, precision)
        if updated:
            if cleared: stats.profitability_cleared += 1
            else: stats.profitability_updates += 1

        updated, cleared = _assign_value(annotations, REPUTATIONAL_FIELD, reputational_ratio, precision)
        if updated:
            if cleared: stats.reputational_cleared += 1
            else: stats.reputational_updates += 1

        updated, cleared = _assign_value(annotations, PROFITABILITY_EMISSIONS_FIELD, profitability_emissions_ratio, precision + 10)
        if updated:
            if cleared: stats.profitability_emissions_cleared += 1
            else: stats.profitability_emissions_updates += 1

        updated, cleared = _assign_value(annotations, EBITDA_EMISSIONS_FIELD, ebitda_emissions_ratio, precision + 6)
        if updated:
            if cleared: stats.ebitda_emissions_cleared += 1
            else: stats.ebitda_emissions_updates += 1

        updated, cleared = _assign_value(annotations, MENTIONS_PER_PAGE_FIELD, net_zero_mentions_per_page, precision)
        if updated:
            if cleared: stats.mentions_per_page_cleared += 1
            else: stats.mentions_per_page_updates += 1

    return stats


def main() -> int:
    args = parse_args()
    try:
        payload = json.loads(args.companies_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"[error] companies payload not found at {args.companies_path}")
        return 1
    except json.JSONDecodeError as exc:
        print(f"[error] Failed to parse JSON: {exc}")
        return 1

    try:
        stats = update_companies(payload, precision=max(0, args.precision))
    except ValueError as exc:
        print(f"[error] {exc}")
        return 1

    for line in stats.as_lines():
        print(line)

    if args.apply:
        args.companies_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"[info] Wrote updates to {args.companies_path}")
    else:
        print("[info] Dry-run mode; no files were modified. Use --apply to persist changes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
