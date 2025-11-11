from __future__ import annotations

import os
import re
from dataclasses import dataclass
from textwrap import dedent
from typing import Callable, Literal, cast
from urllib.parse import urlparse

from src.utils.documents import (
    classify_document_type,
    infer_year_from_text,
    normalise_pdf_url,
)
from ..models import SearchRecord


@dataclass(frozen=True)
class QueryConfig:
    model: str
    debug: bool


def load_query_config() -> QueryConfig:
    model = os.getenv("S0_MODEL", "gpt-4o-mini")
    debug = os.getenv("S0_DEBUG", "0").lower() in ("1", "true", "yes")
    return QueryConfig(model=model, debug=debug)


def build_debug_logger(config: QueryConfig) -> Callable[[str], None]:
    def _dbg(message: str) -> None:
        if config.debug:
            print(message, flush=True)

    return _dbg


def build_web_search_prompt(company: str) -> str:
    return (
        f"{company} (2025 OR FY25 OR 25) "
        f'("annual report" OR "sustainability report" OR "esg report" OR "climate report") "scope 1"'
        "filetype:pdf"
    )


def query(client, company: str, ticker: str):
    """Create an OpenAI response for the given company and ticker.

    Returns (raw_response, search_record, rejection_reason). If the record
    is None, rejection_reason will contain a brief description.
    """
    config = load_query_config()
    dbg = build_debug_logger(config)

    dbg(f"[s0] Starting web_search-backed lookup for {company} ({ticker})")
    try:
        response = execute_llm_search(client, company, ticker, config, dbg)
    except Exception as exc:  # pylint: disable=broad-except
        dbg(f"[s0] execute_llm_search raised {exc}")
        return None, None, f"search invocation failed: {exc}"
    parsed, rejection = map_llm_response_to_record(response, dbg)
    return response, parsed, rejection


def execute_llm_search(
    client, company: str, ticker: str, config: QueryConfig, dbg: Callable[[str], None]
):
    input_text = f'{{"name": "{company}", "ticker": "{ticker}"}}'
    search_prompt = build_web_search_prompt(company)
    dbg(f"[s0] search prompt: {search_prompt}")
    instructions = dedent(
        f"""
        ## Objective
        Return the direct .pdf URL for the company's official 2025 (or FY25) sustainability/ESG/climate/annual report.
        Output only the minimal fields listed below.

        ## One-shot web search
        - You have ONE web_search call. Form a precise query:
          "{search_prompt}"
        - Selection priorities:
          1) Prefer a 2025 sustainability/ESG/climate/TCFD PDF.
          2) If no 2025 (or FY25) sustainability/ESG/climate/TCFD PDF exists, return a 2025 (or FY25) annual report PDF.
          3) Only fall back to a 2024 sustainability/ESG/climate/TCFD PDF if no 2025 PDFs exist.
        - Never return 2023 (or earlier) documents unless explicitly instructed.
        - Ensure the link is a direct PDF (ends with .pdf) on an official domain/CDN.

        ## Output (structured, ONLY these fields)
        - url: direct .pdf URL (empty if none qualify)
        - title: report title
        - filename: file name
        - year: "2025" or "2024"

        The URL you provide MUST end in ".pdf".
        """
    ).strip()

    dbg(
        f"[s0] Invoking responses.parse with web_search tool, instructions: {instructions}"
    )
    return client.responses.parse(
        instructions=instructions,
        input=input_text,
        text_format=SearchRecord,
        max_tool_calls=1,
        tools=[
            {
                "type": "web_search",
                "user_location": {"type": "approximate", "country": "AU"},
                "search_context_size": "low",
            }
        ],
        include=["web_search_call.results"],
        tool_choice="required",
        temperature=0,
        model=config.model,
        store=True,
    )


def map_llm_response_to_record(
    response, dbg: Callable[[str], None]
) -> tuple[SearchRecord | None, str | None]:
    record = getattr(response, "output_parsed", None)
    try:
        if not record or not (record.url or "").strip():
            dbg("[s0] web_search parse returned empty URL; skipping")
            return None, "assistant returned an empty URL"
        url, is_pdf = normalise_pdf_url(record.url)
        if not url or not is_pdf:
            dbg(f"[s0] web_search parse URL not a PDF: {record.url}; skipping")
            return None, f"assistant returned a non-PDF link ({record.url})"
        filename = derive_filename(url, record.filename or "")
        title = (record.title or "").strip() or filename

        text = f"{title} {filename} {url}".lower()
        has_2025 = ("2025" in text) or ("fy25" in text)
        has_2024 = ("2024" in text) or ("fy24" in text)
        has_2023 = ("2023" in text) or ("fy23" in text)

        selected_year = infer_year_from_text(title, filename, url)
        if not selected_year:
            if has_2025:
                selected_year = "2025"
            elif has_2024:
                selected_year = "2024"
            elif has_2023:
                selected_year = "2023"
            else:
                dbg(
                    f"[s0] Candidate rejected: no FY23/FY24/FY25 indicator found ({url})"
                )
                return None, "no FY24/FY25 indicator in candidate"

        doc_type = cast(
            Literal["annual", "sustainability", "other"],
            classify_document_type(title, filename, url),
        )

        return (
            SearchRecord(
                url=url,
                title=title,
                filename=filename,
                year=selected_year or (record.year or None),
                doc_type=doc_type,
            ),
            None,
        )
    except (AttributeError, TypeError, ValueError) as exc:
        dbg(f"[s0] Failed to map web_search parse into SearchRecord: {exc}")
        return None, f"failed to map response: {exc}"


def derive_filename(url: str, fallback: str) -> str:
    try:
        parsed_url = urlparse(url)
        path_name = (parsed_url.path or "").rsplit("/", 1)[-1].strip()
    except (AttributeError, ValueError):
        path_name = ""
    source = path_name or (fallback or "").strip()
    if not re.search(r"\.(pdf|csv|xlsx|txt|html|htm)$", (source or "").lower()):
        return "report.pdf"
    return source
