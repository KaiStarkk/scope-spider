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
DEFAULT_INPUT_GLOB = "authoritative_company_emissions*.json"


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
        action="append",
        dest="input_paths",
        type=Path,
        help=(
            "Path to an authoritative company emissions JSON payload. "
            "May be provided multiple times."
        ),
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


def discover_default_input_paths() -> List[Path]:
    candidates = [
        path
        for path in sorted(ROOT_DIR.glob(DEFAULT_INPUT_GLOB), key=lambda p: p.name)
        if path.is_file() and path.stat().st_size > 0
    ]
    return candidates


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
        "limited": "",
        "ltd": "",
        "pty": "",
        "plc": "",
        "holding": "",
        "holdings": "",
        "company": "",
        "companies": "",
        "corp": "",
        "corporation": "",
        "inc": "",
        "group": "",
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


def _extract_prefix_ticker(raw_label: str) -> Optional[str]:
    prefix_match = re.match(r"\s*([A-Za-z0-9.]{2,6})(?=[_\s-])", raw_label)
    if prefix_match:
        token = prefix_match.group(1)
        if token.isupper():
            return token.replace(".", "")
    return None


def _deduce_identity_from_label(
    raw_label: str, content: Dict[str, object]
) -> Tuple[str, Optional[str]]:
    ticker_hint = content.get("ticker")
    ticker_hint = ticker_hint.strip().upper() if isinstance(ticker_hint, str) else None
    prefix_token = _extract_prefix_ticker(raw_label)
    if not ticker_hint and prefix_token:
        ticker_hint = prefix_token
    if not ticker_hint:
        match = re.search(r"\(([A-Za-z0-9.]+)\)", raw_label)
        if match:
            ticker_hint = match.group(1).replace(".", "").upper()
    entry_company_name = content.get("company_name")
    if not isinstance(entry_company_name, str) or not entry_company_name.strip():
        entry_company_name = re.split(r"\s+-\s+", raw_label, maxsplit=1)[0].strip()
    entry_company_name = (
        re.sub(r"\([^)]*\)", "", entry_company_name.replace("_", " ")).strip()
        or raw_label.strip()
    )
    if prefix_token and entry_company_name.upper().startswith(prefix_token + " "):
        entry_company_name = entry_company_name[len(prefix_token) :].lstrip()
    return entry_company_name, ticker_hint


def build_indexes(
    companies: List[Dict],
) -> Tuple[Dict[str, List[int]], Dict[str, List[int]]]:
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
            return MatchResult(
                name=company_name, ticker=ticker_upper, index=idx, reason="ticker"
            )
        if len(ticker_matches) > 1:
            candidates.extend(
                MatchResult(
                    name=company_name, ticker=ticker_upper, index=idx, reason="ticker"
                )
                for idx in ticker_matches
            )

    exact_matches = by_name.get(norm_name, [])
    if len(exact_matches) == 1:
        idx = exact_matches[0]
        return MatchResult(
            name=company_name, ticker=None, index=idx, reason="normalised-name"
        )
    if len(exact_matches) > 1:
        candidates.extend(
            MatchResult(
                name=company_name, ticker=None, index=idx, reason="normalised-name"
            )
            for idx in exact_matches
        )

    if norm_name:
        all_names = list(by_name.keys())
        close = difflib.get_close_matches(norm_name, all_names, n=3, cutoff=cutoff)
        for candidate_name in close:
            for idx in by_name.get(candidate_name, []):
                candidates.append(
                    MatchResult(
                        name=company_name,
                        ticker=None,
                        index=idx,
                        reason=f"fuzzy({candidate_name})",
                    )
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


def coerce_emission_value(raw_value: object) -> int:
    if isinstance(raw_value, bool):
        raise ValueError("Boolean values are not valid emission totals.")
    if isinstance(raw_value, (int, float)):
        return int(round(raw_value))
    if isinstance(raw_value, str):
        trimmed = raw_value.strip()
        if not trimmed:
            raise ValueError("Empty string is not a valid emission total.")
        normalised = trimmed.replace(",", "")
        try:
            parsed = float(normalised)
        except ValueError as exc:  # noqa: B904
            raise ValueError(f"Could not parse emission value: {raw_value!r}") from exc
        return int(round(parsed))
    raise TypeError(f"Unsupported emission value type: {type(raw_value).__name__}")


def normalise_entry_from_list(entry: Dict, source: Path) -> Dict:
    normalised = dict(entry)
    normalised.setdefault("_source", str(source))
    if "ticker" not in normalised and "company_name" in normalised:
        candidate = normalised["company_name"]
        if isinstance(candidate, str) and re.fullmatch(
            r"[A-Za-z0-9]+", candidate.strip()
        ):
            normalised["ticker"] = candidate.strip().upper()
    emissions = normalised.get("emissions")
    if isinstance(emissions, Dict):
        entry_context = normalised.get("notes") or normalised.get("note")
        normalised["emissions"] = _normalise_scope_payloads(emissions, entry_context)
    return normalised


def normalise_entries_from_mapping(
    payload: Dict, source: Path
) -> Tuple[List[Dict], List[str]]:
    entries: List[Dict] = []
    warnings: List[str] = []
    for raw_label, content in payload.items():
        if not isinstance(content, Dict):
            warnings.append(f"Skipped malformed entry for {raw_label} in {source}")
            continue
        emissions = content.get("emissions")
        if emissions is None:
            emissions = {
                key: value for key, value in content.items() if key.startswith("scope_")
            }
        if not isinstance(emissions, Dict):
            warnings.append(f"Invalid emissions object for {raw_label} in {source}")
            continue
        entry_level_context = content.get("notes") or content.get("note")
        emissions = _normalise_scope_payloads(emissions, entry_level_context)
        entry_company_name, ticker_hint = _deduce_identity_from_label(raw_label, content)
        entry = {
            "company_name": entry_company_name,
            "ticker": ticker_hint,
            "emissions": emissions,
            "_source": str(source),
            "_label": raw_label,
        }
        entries.append(entry)
    return entries, warnings


def normalise_combined_scope_totals(
    payload: Dict, source: Path
) -> Tuple[List[Dict], List[str]]:
    entries: List[Dict] = []
    warnings: List[str] = []
    for raw_label, content in payload.items():
        if not isinstance(content, Dict):
            warnings.append(f"Skipped malformed entry for {raw_label} in {source}")
            continue
        total_payload = content.get("total_scope_1_and_2")
        if not isinstance(total_payload, Dict):
            warnings.append(
                f"Invalid total_scope_1_and_2 object for {raw_label} in {source}"
            )
            continue
        if "value" not in total_payload:
            warnings.append(
                f"Missing value for combined Scope 1 and 2 in {raw_label} ({source})"
            )
            continue
        entry_company_name, ticker_hint = _deduce_identity_from_label(raw_label, content)
        entry_context = total_payload.get("notes") or content.get("notes") or content.get("note")
        emissions = {"scope_1": dict(total_payload)}
        combined_context = "Combined Scope 1 and 2 total"
        if entry_context:
            combined_context = f"{combined_context}. {entry_context}"
        emissions = _normalise_scope_payloads(
            emissions,
            combined_context,
        )
        entry = {
            "company_name": entry_company_name,
            "ticker": ticker_hint,
            "emissions": emissions,
            "_source": str(source),
            "_label": raw_label,
        }
        entries.append(entry)
    return entries, warnings


def _normalise_scope_payloads(emissions: Dict[str, object], entry_context: Optional[str] = None) -> Dict[str, object]:
    normalised: Dict[str, object] = {}
    for scope_key, payload in emissions.items():
        if not isinstance(payload, Dict):
            normalised[scope_key] = payload
            continue
        scope_payload = dict(payload)
        note = scope_payload.pop("note", None)
        notes = scope_payload.pop("notes", None)
        citation = scope_payload.pop("citation", None)
        value = scope_payload.get("value")
        if isinstance(value, str):
            stripped = value.strip()
            lowered = stripped.lower()
            if lowered in {"n/a", "na", "not applicable", "not available", "none"} or lowered.startswith("not "):
                scope_payload.pop("value", None)
            else:
                scope_payload["value"] = stripped
        contexts = [
            scope_payload.get("context"),
            note,
            notes,
            entry_context,
        ]
        if citation:
            contexts.append(f"Citation(s): {citation}")
        merged_context = " ".join(
            part.strip()
            for part in contexts
            if isinstance(part, str) and part.strip()
        )
        if merged_context:
            scope_payload["context"] = merged_context
        else:
            scope_payload.pop("context", None)
        normalised[scope_key] = scope_payload
    return normalised


def load_authoritative_entries(paths: List[Path]) -> Tuple[List[Dict], List[str]]:
    collected: List[Dict] = []
    warnings: List[str] = []
    for path in paths:
        data = read_json(path)
        if isinstance(data, Dict):
            handled = False
            if "company_emissions" in data:
                company_emissions = data["company_emissions"]
                if not isinstance(company_emissions, list):
                    raise ValueError(
                        f"authoritative payload in {path} must contain a 'company_emissions' list."
                    )
                for entry in company_emissions:
                    if not isinstance(entry, Dict):
                        warnings.append(f"Skipped malformed list entry in {path}")
                        continue
                    collected.append(normalise_entry_from_list(entry, path))
                handled = True
            if "companies_emissions" in data and isinstance(
                data["companies_emissions"], Dict
            ):
                entries, local_warnings = normalise_entries_from_mapping(
                    data["companies_emissions"], path
                )
                collected.extend(entries)
                warnings.extend(local_warnings)
                handled = True
            if "combined_scope_1_and_2_totals_only" in data and isinstance(
                data["combined_scope_1_and_2_totals_only"], Dict
            ):
                entries, local_warnings = normalise_combined_scope_totals(
                    data["combined_scope_1_and_2_totals_only"], path
                )
                collected.extend(entries)
                warnings.extend(local_warnings)
                handled = True
            if not handled:
                entries, local_warnings = normalise_entries_from_mapping(data, path)
                collected.extend(entries)
                warnings.extend(local_warnings)
        else:
            raise ValueError(f"Unsupported authoritative payload format in {path}")
    return collected, warnings


def update_scope(scope_data: Optional[Dict], scope_payload: Dict) -> Dict:
    updated = dict(scope_data) if isinstance(scope_data, Dict) else {}
    for key, value in scope_payload.items():
        if key == "value":
            continue
        updated[key] = value
    updated["value"] = int(scope_payload["value"])
    return updated


def apply_updates(
    companies_payload: Dict,
    entries: List[Dict],
    cutoff: float,
) -> Tuple[List[Dict], List[str]]:
    companies = companies_payload.get("companies")
    if not isinstance(companies, list):
        raise ValueError("companies.json payload must contain a 'companies' list.")

    by_name, by_ticker = build_indexes(companies)
    updates: List[Dict] = []
    warnings: List[str] = []

    for entry in entries:
        company_name = entry.get("company_name")
        if not company_name:
            warnings.append("Skipped entry with missing company_name.")
            continue
        source = entry.get("_source", "<unknown>")
        explicit_ticker = entry.get("ticker")
        ticker_hint = explicit_ticker or extract_ticker_hint(company_name)
        if not ticker_hint:
            stripped_name = company_name.strip()
            if re.fullmatch(r"[A-Z0-9]+", stripped_name):
                ticker_hint = stripped_name
        match = resolve_company_index(
            company_name, ticker_hint, by_name, by_ticker, cutoff
        )
        if match is None:
            warnings.append(
                f"Could not locate company in source dataset: {company_name} (source: {source})"
            )
            continue

        company = companies[match.index]
        identity = company.get("identity") or {}
        emissions = company.setdefault("emissions", {})
        new_emissions = entry.get("emissions") or {}
        if not isinstance(new_emissions, dict):
            warnings.append(
                f"Invalid emissions payload for {company_name}; expected an object. (source: {source})"
            )
            continue

        update_record = {
            "company_name": identity.get("name"),
            "ticker": identity.get("ticker"),
            "matched_via": match.reason,
            "source": source,
            "changes": {},
        }

        for scope_key in ("scope_1", "scope_2", "scope_3"):
            scope_values = new_emissions.get(scope_key) or {}
            if not isinstance(scope_values, dict):
                warnings.append(
                    f"Invalid {scope_key} structure for {company_name}; expected an object with 'value'. (source: {source})"
                )
                continue
            if "value" not in scope_values:
                continue
            previous_scope = emissions.get(scope_key)
            previous_value = (
                previous_scope.get("value")
                if isinstance(previous_scope, dict)
                else None
            )
            try:
                new_value = coerce_emission_value(scope_values["value"])
            except (TypeError, ValueError) as exc:
                warnings.append(
                    f"Invalid {scope_key}.value for {company_name} (source: {source}): {exc}"
                )
                continue
            scope_payload = dict(scope_values)
            scope_payload["value"] = new_value
            if previous_value == new_value:
                continue
            emissions[scope_key] = update_scope(
                previous_scope if isinstance(previous_scope, dict) else None,
                scope_payload,
            )
            update_record["changes"][scope_key] = {
                "from": previous_value,
                "to": new_value,
            }

        if update_record["changes"]:
            updates.append(update_record)

    return updates, warnings


def main() -> int:
    args = parse_args()
    input_paths = args.input_paths
    if input_paths:
        resolved_paths = input_paths
    else:
        resolved_paths = discover_default_input_paths()
        if not resolved_paths:
            print(
                "[error] No authoritative_company_emissions*.json files found. "
                "Provide --input-path explicitly.",
                file=sys.stderr,
            )
            return 1
    try:
        companies_payload = read_json(args.companies_path)
        authoritative_entries, load_warnings = load_authoritative_entries(
            resolved_paths
        )
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1

    updates, apply_warnings = apply_updates(
        companies_payload, authoritative_entries, args.cutoff
    )

    warnings = [*load_warnings, *apply_warnings]

    if warnings:
        for warning in warnings:
            print(f"[warn] {warning}", file=sys.stderr)

    if updates:
        for update in updates:
            company_name = update["company_name"]
            ticker = update["ticker"] or "N/A"
            matched_via = update["matched_via"]
            source = update.get("source", "<unknown>")
            print(
                f"[update] {company_name} ({ticker}) via {matched_via} (source: {source})"
            )
            for scope_key, change in update["changes"].items():
                print(f"  - {scope_key}: {change['from']} -> {change['to']}")
    else:
        print("[info] No changes required (all values already up-to-date).")

    if args.apply and updates:
        args.companies_path.write_text(
            json.dumps(companies_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"[info] Wrote updates to {args.companies_path}")
    elif args.apply:
        print("[info] No updates were applied because no changes were detected.")
    else:
        print(
            "[info] Dry-run mode: no files were modified. Use --apply to persist changes."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
