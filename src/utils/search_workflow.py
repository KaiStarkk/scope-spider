from __future__ import annotations

import json
import argparse
from dataclasses import dataclass
from typing import Dict, List, Literal, Sequence, Tuple

from openai import OpenAI
from pydantic import ValidationError

from ..models import Company, SearchRecord
from .documents import classify_document_type, infer_year_from_text
from .query import derive_filename, query
from .status import needs_search


@dataclass
class SearchArgs:
    path: str
    mode: str
    debug: bool
    jobs: int


def parse_search_args(argv: Sequence[str]) -> SearchArgs:
    parser = argparse.ArgumentParser(
        prog="s2_search",
        description="Search for ESG reports using OpenAI tooling.",
    )
    parser.add_argument("path", help="Path to companies.json")
    parser.add_argument(
        "mode",
        nargs="?",
        choices=("auto", "review"),
        default="review",
        help="Automation mode (default: review)",
    )
    parser.add_argument("--debug", "-d", action="store_true", help="Enable verbose logs.")
    parser.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="Number of concurrent search workers (auto mode only).",
    )
    args = parser.parse_args(list(argv[1:]))
    if args.jobs < 1:
        parser.error("--jobs must be >= 1")
    return SearchArgs(path=args.path, mode=args.mode, debug=args.debug, jobs=args.jobs)


def get_unsearched_companies(companies: Sequence[Company]) -> List[Company]:
    return [company for company in companies if needs_search(company)]


def perform_search(client: OpenAI, company: Company, debug: bool):
    if debug:
        # Propagate debug flag for downstream logging
        import os

        os.environ["S0_DEBUG"] = "1"
    identity = company.identity
    return query(client, identity.name, identity.ticker)


def ensure_search_record(parsed) -> SearchRecord | None:
    if parsed is None:
        return None
    if isinstance(parsed, SearchRecord):
        return parsed
    try:
        return SearchRecord.model_validate(parsed)
    except ValidationError:
        return None


def summarize_response(response) -> str:
    try:
        return json.dumps(response.model_dump(), ensure_ascii=False, indent=2)  # type: ignore[attr-defined]
    except (AttributeError, TypeError, ValueError):
        return str(response)


def process_company(
    client: OpenAI,
    company: Company,
    *,
    auto_mode: bool,
    debug: bool,
) -> Tuple[bool, bool, bool, SearchRecord | None, str | None]:
    response, parsed, rejection = perform_search(client, company, debug)
    record = ensure_search_record(parsed)
    if not record:
        reason = rejection or "assistant response could not be parsed"
        if auto_mode:
            details = summarize_response(response) if debug else ""
            identity = company.identity
            message = (
                f"WARNING: {identity.name} ({identity.ticker}) - {reason} — queued for review\n"
            )
            if debug and details:
                message += f"Assistant output:\n{details}\n"
            print(message, flush=True)
            return False, auto_mode, False, None, (
                f"{identity.ticker}: {reason}"
            )
        details = summarize_response(response) if debug else ""
        identity = company.identity
        message = (
            f"\n{identity.name} ({identity.ticker}) - No valid record returned.\n"
            f"Reason: {reason}\n"
        )
        if debug and details:
            message += f"Assistant output:\n{details}\n"
        print(message, flush=True)
        action = None
        manual_url = None
        while True:
            action = input("skip/continue/quit or provide URL [s/c/q/<url>]: ").strip()
            if not action:
                continue
            lower_action = action.lower()
            if lower_action in ("skip", "s", "continue", "c", "quit", "q"):
                break
            if action.startswith("http"):
                manual_url = action
                break
            print("Unrecognized input. Enter s, c, q, or a direct PDF URL.")

        if manual_url:
            filename = derive_filename(manual_url, "")
            inferred_year = infer_year_from_text(filename, manual_url)
            doc_type: Literal["annual", "sustainability", "other"] = classify_document_type(
                filename, filename, manual_url
            )
            manual_record = SearchRecord(
                url=manual_url,
                title=filename,
                filename=filename,
                year=inferred_year,
                doc_type=doc_type,
            )
            return True, auto_mode, False, manual_record, None

        if lower_action in ("quit", "q"):
            return False, auto_mode, True, None, None
        if lower_action in ("continue", "c"):
            print("Switching to automatic mode...", flush=True)
            return False, True, False, None, None
        return False, auto_mode, False, None, None

    if auto_mode:
        return True, auto_mode, False, record, None

    if record.year == "2023":
        identity = company.identity
        print(
            f"WARNING: {identity.name} ({identity.ticker}) proposed search record is for 2023.",
            flush=True,
        )

    preview = json.dumps(
        record.model_dump(exclude_none=True), ensure_ascii=False, indent=2
    )
    identity = company.identity
    print(
        f"\n{identity.name} ({identity.ticker}) - Proposed Search Record:\n{preview}\n",
        flush=True,
    )
    action = input("approve/skip/continue/quit [A/s/c/q]: ").strip().lower()
    if action in ("skip", "s"):
        return False, auto_mode, False, None, None
    if action in ("quit", "q"):
        return False, auto_mode, True, None, None
    if action in ("continue", "c"):
        print("Switching to automatic mode...", flush=True)
        return True, True, False, record, None
    return True, auto_mode, False, record, None
