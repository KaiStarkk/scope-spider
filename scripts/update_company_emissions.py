from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_COMPANIES_PATH = ROOT_DIR / "companies.json"
DEFAULT_INPUT_PATH = ROOT_DIR / "authoritative_company_emissions.json"


@dataclass
class MatchResult:
    name: str
    ticker: Optional[str]
    index: int
    reason: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Update companies.json with authoritative emissions data."
    )
    parser.add_argument(
        "--companies-path",
        type=Path,
        default=DEFAULT_COMPANIES_PATH,
        help="Path to the companies.json payload (default: %(default)s).",
    )
    parser.add_argument(
        "--input-path",
        type=Path,
        default=DEFAULT_INPUT_PATH,
        help="Path to the authoritative company emissions JSON (default: %(default)s).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist updates to companies.json. Without this flag, the script runs in dry-run mode.",
    )
    parser.add_argument(
        "--cutoff",
        type=float,
        default=0.72,
        help="Similarity cutoff used when fuzzy matching company names (default: %(default)s).",
    )
    return parser.parse_args()


def read_json(path: Path) -> Dict:
    if not path.exists():
        raise FileNotFoundError(f"JSON file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def normalise_name(name: str) -> str:
    if not name:
        return ""
    text = unicodedata.normalize("NFKD", name)
    text = text.lower()
    # strip parenthetical notes (often used for ticker hints)
    text = re.sub(r"\([^)]*\)", " ", text)
    # normalise ampersands and apostrophes
    text = text.replace("&", " and ").replace("'", "")
    # remove non-alphanumeric separators
    tokens = re.split(r"[^a-z0-9]+", text)
    substitutions = {
        "limited": "ltd",
        "ltd": "ltd",
        "pty": "",
        "plc": "",
        "holding": "hldg",
        "holdings": "hldgs",
        "company": "co",
        "companies": "co",
        "corp": "corp",
        "corporation": "corp",
        "inc": "",
        "group": "grp",
        "the": "",
        "sa": "",
        "spa": "",
        "sp": "",
    }
    normalised_tokens: List[str] = []
    for token in tokens:
        if not token:
            continue
        replacement = substitutions.get(token, token)
        if replacement:
            normalised_tokens.append(replacement)
    return " ".join(normalised_tokens)


def extract_ticker_hint(company_name: str) -> Optional[str]:
    match = re.search(r"\(([^()]+)\)\s*$", company_name)
    if not match:
        return None
    candidate = match.group(1)
    letters = "".join(ch for ch in candidate if ch.isalpha() and ch.isupper())
    digits = "".join(ch for ch in candidate if ch.isdigit())
    ticker = letters or None
    if digits:
        ticker = f"{ticker or ''}{digits}"
    return ticker or None


def build_indexes(companies: List[Dict]) -> Tuple[Dict[str, List[int]], Dict[str, List[int]]]:
    by_name: Dict[str, List[int]] = {}
    by_ticker: Dict[str, List[int]] = {}
    for index, company in enumerate(companies):
        identity = company.get("identity") or {}
        name = identity.get("name") or ""
        ticker = identity.get("ticker") or ""
        norm_name = normalise_name(name)
        if norm_name:
            by_name.setdefault(norm_name, []).append(index)
        ticker_prefix = ticker.split("-")[0].upper() if ticker else ""
        if ticker_prefix:
            by_ticker.setdefault(ticker_prefix, []).append(index)
    return by_name, by_ticker


def resolve_company_index(
    company_name: str,
    ticker_hint: Optional[str],
    by_name: Dict[str, List[int]],
    by_ticker: Dict[str, List[int]],
    cutoff: float,
) -> Optional[MatchResult]:
    norm_name = normalise_name(company_name)
    candidates: List[MatchResult] = []

    if ticker_hint:
        ticker_upper = ticker_hint.upper()
        ticker_matches = by_ticker.get(ticker_upper, [])
        if len(ticker_matches) == 1:
            idx = ticker_matches[0]
            return MatchResult(name=company_name, ticker=ticker_upper, index=idx, reason="ticker")
        if len(ticker_matches) > 1:
            candidates.extend(
                MatchResult(name=company_name, ticker=ticker_upper, index=idx, reason="ticker")
                for idx in ticker_matches
            )

    exact_matches = by_name.get(norm_name, [])
    if len(exact_matches) == 1:
        idx = exact_matches[0]
        return MatchResult(name=company_name, ticker=None, index=idx, reason="normalised-name")
    if len(exact_matches) > 1:
        candidates.extend(
            MatchResult(name=company_name, ticker=None, index=idx, reason="normalised-name")
            for idx in exact_matches
        )

    if norm_name:
        all_names = list(by_name.keys())
        close = difflib.get_close_matches(norm_name, all_names, n=3, cutoff=cutoff)
        for candidate_name in close:
            for idx in by_name.get(candidate_name, []):
                candidates.append(
                    MatchResult(name=company_name, ticker=None, index=idx, reason=f"fuzzy({candidate_name})")
                )

    if len(candidates) == 1:
        return candidates[0]

    if candidates:
        matched_indices = {candidate.index for candidate in candidates}
        if len(matched_indices) == 1:
            candidate = candidates[0]
            return MatchResult(
                name=company_name,
                ticker=candidate.ticker,
                index=next(iter(matched_indices)),
                reason=f"ambiguous-{candidate.reason}",
            )

    return None


def update_scope(scope_data: Optional[Dict], new_value: int) -> Dict:
    if scope_data is None:
        return {"value": int(new_value)}
    updated = dict(scope_data)
    updated["value"] = int(new_value)
    return updated


def apply_updates(
    companies_payload: Dict,
    authoritative_data: Dict,
    cutoff: float,
) -> Tuple[List[Dict], List[str]]:
    companies = companies_payload.get("companies")
    if not isinstance(companies, list):
        raise ValueError("companies.json payload must contain a 'companies' list.")

    entries = authoritative_data.get("company_emissions") or []
    if not isinstance(entries, list):
        raise ValueError("authoritative payload must contain a 'company_emissions' list.")

    by_name, by_ticker = build_indexes(companies)
    updates: List[Dict] = []
    warnings: List[str] = []

    for entry in entries:
        company_name = entry.get("company_name")
        if not company_name:
            warnings.append("Skipped entry with missing company_name.")
            continue
        ticker_hint = extract_ticker_hint(company_name)
        match = resolve_company_index(company_name, ticker_hint, by_name, by_ticker, cutoff)
        if match is None:
            warnings.append(f"Could not locate company in source dataset: {company_name}")
            continue

        company = companies[match.index]
        identity = company.get("identity") or {}
        emissions = company.setdefault("emissions", {})
        new_emissions = entry.get("emissions") or {}
        if not isinstance(new_emissions, dict):
            warnings.append(f"Invalid emissions payload for {company_name}; expected an object.")
            continue

        update_record = {
            "company_name": identity.get("name"),
            "ticker": identity.get("ticker"),
            "matched_via": match.reason,
            "changes": {},
        }

        for scope_key in ("scope_1", "scope_2", "scope_3"):
            scope_values = new_emissions.get(scope_key) or {}
            if not isinstance(scope_values, dict):
                warnings.append(
                    f"Invalid {scope_key} structure for {company_name}; expected an object with 'value'."
                )
                continue
            if "value" not in scope_values:
                continue
            previous_scope = emissions.get(scope_key)
            previous_value = previous_scope.get("value") if isinstance(previous_scope, dict) else None
            new_value = int(scope_values["value"])
            if previous_value == new_value:
                continue
            emissions[scope_key] = update_scope(previous_scope if isinstance(previous_scope, dict) else None, new_value)
            update_record["changes"][scope_key] = {
                "from": previous_value,
                "to": new_value,
            }

        if update_record["changes"]:
            updates.append(update_record)

    return updates, warnings


def main() -> int:
    args = parse_args()
    try:
        companies_payload = read_json(args.companies_path)
        authoritative_data = read_json(args.input_path)
    except Exception as exc:  # noqa: BLE001
        print(f"[error] {exc}", file=sys.stderr)
        return 1

    updates, warnings = apply_updates(companies_payload, authoritative_data, args.cutoff)

    if warnings:
        for warning in warnings:
            print(f"[warn] {warning}", file=sys.stderr)

    if updates:
        for update in updates:
            company_name = update["company_name"]
            ticker = update["ticker"] or "N/A"
            matched_via = update["matched_via"]
            print(f"[update] {company_name} ({ticker}) via {matched_via}")
            for scope_key, change in update["changes"].items():
                print(f"  - {scope_key}: {change['from']} -> {change['to']}")
    else:
        print("[info] No changes required (all values already up-to-date).")

    if args.apply and updates:
        args.companies_path.write_text(json.dumps(companies_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[info] Wrote updates to {args.companies_path}")
    elif args.apply:
        print("[info] No updates were applied because no changes were detected.")
    else:
        print("[info] Dry-run mode: no files were modified. Use --apply to persist changes.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
