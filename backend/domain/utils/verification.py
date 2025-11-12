from __future__ import annotations

import re
import time
from contextlib import suppress
from pathlib import Path
from typing import Any, Optional

from openai import OpenAI
from pydantic import BaseModel, Field

from rapidfuzz import fuzz
from backend.domain.models import (
    AnalysisRecord,
    Company,
    Scope2Emissions,
    Scope3Emissions,
    ScopeValue,
)
from backend.domain.utils.pdf_preview import ensure_page_previews


class ParsedResult(BaseModel):
    scope_1: Optional[int]
    scope_1_context: Optional[str] = None
    scope_2: Optional[int]
    scope_2_context: Optional[str] = None
    scope_3: Optional[int] = None
    scope_3_context: Optional[str] = None
    qualifiers: Optional[str] = None
    scope_2_method: Optional[str] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class Advice(BaseModel):
    label: str = Field(default="unknown")
    reason: str
    suggestion: Optional[str] = None


def needs_verification(company: Company, verify_all: bool) -> bool:
    if verify_all:
        return True
    emissions = company.emissions
    scope_1 = emissions.scope_1.value if emissions.scope_1 else None
    scope_2 = emissions.scope_2.value if emissions.scope_2 else None
    if scope_1 is None or scope_2 is None:
        return True
    if scope_1 <= 0 or scope_2 <= 0:
        return True
    return False


def get_or_create_vector_store(client: OpenAI, store_file: Path) -> str:
    if store_file.exists():
        vsid = store_file.read_text(encoding="utf-8").strip()
        if vsid:
            return vsid
    store = client.vector_stores.create(name="scope_spider_knowledge")
    store_file.write_text(store.id, encoding="utf-8")
    return store.id


def attach_file_to_vector_store(
    client: OpenAI,
    vector_store_id: str,
    path: Path,
) -> None:
    with path.open("rb") as file_handle:
        file_resource = client.files.create(file=file_handle, purpose="assistants")
    client.vector_stores.files.create(
        vector_store_id=vector_store_id,
        file_id=file_resource.id,
    )
    time.sleep(1.0)


_SCOPE_PATTERNS = {
    "scope_1": (
        "total scope 1",
        "scope 1 emissions",
        "scope i emissions",
        "scope 1",
    ),
    "scope_2": (
        "total scope 2",
        "scope 2 emissions",
        "scope ii emissions",
        "scope 2",
    ),
    "scope_3": (
        "total scope 3",
        "scope 3 emissions",
        "scope iii emissions",
        "scope 3",
    ),
}

_VALUE_RE = re.compile(
    r"(-?\d[\d,\s]*(?:\.\d+)?)\s*(mt|kt|t|kg)?(?:\s*(?:co2e|co₂e|co2|tonnes|tons))?",
    re.IGNORECASE,
)

_UNIT_HINTS = (
    ("mtco2", "mt"),
    ("mt co2", "mt"),
    ("million tonnes", "mt"),
    ("ktco2", "kt"),
    ("kt co2", "kt"),
    ("thousand tonnes", "kt"),
    ("tco2", "t"),
    (" t co2", "t"),
    ("tonnes co2", "t"),
    ("tons co2", "t"),
)

_METHOD_HINTS = (
    ("market-based", "market"),
    ("market based", "market"),
    ("market", "market"),
    ("location-based", "location"),
    ("location based", "location"),
    ("locational", "location"),
    ("location", "location"),
)

_CONTEXT_KEYWORDS = (
    "scope",
    "emission",
    "co2",
    "ghg",
    "carbon",
    "tonne",
    "tco",
    "ktco",
    "mtco",
)


def _find_scope_candidate(lines: list[str], patterns: tuple[str, ...]):
    best_idx: Optional[int] = None
    best_score = 0
    for idx, line in enumerate(lines):
        lowered = line.lower()
        for pattern in patterns:
            score = fuzz.partial_ratio(lowered, pattern)
            if score > best_score:
                best_score = score
                best_idx = idx
    return best_idx, best_score


def _infer_unit(context: str, explicit: Optional[str]) -> str:
    unit = (explicit or "").lower()
    lowered = context.lower()
    for hint, mapped in _UNIT_HINTS:
        if hint in lowered:
            return mapped
    if unit in {"mt", "kt", "t", "kg"}:
        return unit
    if "kt" in lowered:
        return "kt"
    if "mt" in lowered:
        return "mt"
    if "kg" in lowered:
        return "kg"
    return "t"


def _extract_numeric_value(
    lines: list[str], base_index: int
) -> tuple[Optional[int], Optional[str], int, bool]:
    search_order = [0, 1, -1, 2, -2, 3]
    for offset in search_order:
        idx = base_index + offset
        if idx < 0 or idx >= len(lines):
            continue
        line = lines[idx]
        matches = list(_VALUE_RE.finditer(line))
        if not matches:
            continue
        best_candidate: Optional[tuple[int, int, float, str, str, bool]] = None
        for match in matches:
            raw_value = match.group(1)
            if not raw_value:
                continue
            cleaned = raw_value.replace(",", "").replace(" ", "")
            try:
                number = float(cleaned)
            except ValueError:
                continue
            explicit_unit = match.group(2)
            context = f"{lines[base_index]} {line}"
            unit = _infer_unit(context, explicit_unit)
            factor = {
                "kg": 1,
                "t": 1_000,
                "kt": 1_000_000,
                "mt": 1_000_000_000,
            }.get(unit, 1_000)
            value = int(round(number * factor))
            if value <= 0:
                continue
            lowered_segment = line[
                max(0, match.start() - 6) : match.end() + 12
            ].lower()
            near_unit = bool(explicit_unit) or any(
                token in lowered_segment for token in ("mt", "kt", "tco", "t ", "kg")
            )
            unit_tokens = ("tco", "tonne", "ton ", "co2", "ghg", "kt", "mt")
            unit_present = bool(explicit_unit) or any(
                token in context.lower() for token in unit_tokens
            )
            if not unit_present:
                continue
            priority = 0
            if near_unit:
                priority += 20
            if explicit_unit:
                priority += 5
            if "." in raw_value:
                priority += 2
            if number and 1000 <= number <= 2100 and "." not in raw_value and not near_unit:
                priority -= 10
            priority -= abs(offset) * 2
            priority -= match.start() // 10
            candidate = (
                priority,
                abs(offset),
                number,
                context,
                raw_value,
                near_unit,
            )
            if best_candidate is None or candidate > best_candidate:
                best_candidate = candidate
                best_value = value
                best_context = context
                best_unit_present = unit_present
                best_distance = abs(offset)
        if best_candidate is None:
            continue
        return best_value, best_context, best_distance, best_unit_present
    return None, None, len(search_order), False


def _adjust_value_from_context(
    raw_value: Optional[int], context: Optional[str]
) -> Optional[int]:
    if raw_value is None or raw_value <= 0 or not context:
        return raw_value
    match = _VALUE_RE.search(context)
    if not match:
        return raw_value
    raw_number = match.group(1)
    if not raw_number:
        return raw_value
    cleaned = raw_number.replace(",", "").replace(" ", "")
    try:
        numeric = float(cleaned)
    except ValueError:
        return raw_value
    explicit_unit = match.group(2)
    unit = _infer_unit(context, explicit_unit)
    factor = {
        "kg": 1,
        "t": 1_000,
        "kt": 1_000_000,
        "mt": 1_000_000_000,
    }.get(unit, 1_000)
    expected = int(round(numeric * factor))
    if expected <= 0:
        return raw_value
    if raw_value == expected:
        return raw_value
    if raw_value == 0:
        return expected
    ratio = expected / raw_value if raw_value else None
    if ratio is not None and ratio >= 10:
        return expected
    difference = abs(expected - raw_value)
    if difference <= max(1000, expected * 0.01):
        return expected
    return raw_value


def _detect_scope2_method(context: Optional[str]) -> Optional[str]:
    if not context:
        return None
    lowered = context.lower()
    for hint, mapped in _METHOD_HINTS:
        if hint in lowered:
            return mapped
    return None


def parse_data_fuzzy(snippet_text: str) -> Optional[ParsedResult]:
    lines = [line.strip() for line in snippet_text.splitlines() if line.strip()]
    if not lines:
        return None

    values: dict[str, int] = {}
    scores: dict[str, float] = {}
    contexts: dict[str, Optional[str]] = {}
    for scope_key, patterns in _SCOPE_PATTERNS.items():
        idx, score = _find_scope_candidate(lines, patterns)
        if idx is None or score < 60:
            continue
        value, context, distance, unit_present = _extract_numeric_value(lines, idx)
        if value is None or not context:
            continue
        if not unit_present:
            continue
        normalized_context = context.lower()
        if not any(keyword in normalized_context for keyword in _CONTEXT_KEYWORDS):
            continue
        values[scope_key] = value
        contexts[scope_key] = context
        adjusted_score = max(score - distance * 5, 0)
        scores[scope_key] = adjusted_score

    if "scope_1" not in values or "scope_2" not in values:
        return None

    scope_2_method = _detect_scope2_method(contexts.get("scope_2"))
    scope3_value = values.get("scope_3")
    score_floor = min(scores["scope_1"], scores["scope_2"])
    confidence = max(0.1, min(0.95, score_floor / 100.0))

    return ParsedResult(
        scope_1=values["scope_1"],
        scope_1_context=contexts.get("scope_1"),
        scope_2=values["scope_2"],
        scope_2_context=contexts.get("scope_2"),
        scope_3=scope3_value,
        scope_3_context=contexts.get("scope_3"),
        qualifiers=None,
        scope_2_method=scope_2_method,
        confidence=confidence,
    )


def parse_data_local(client: OpenAI, snippet_text: str) -> Optional[ParsedResult]:
    instructions = (
        "You will be given a snippet of PDF text from a company's 2025-period report.\n"
        "- Extract total Scope 1, Scope 2, and optionally Scope 3 greenhouse gas emissions in kgCO2e as integers.\n"
        "- If values are presented in tCO2e, ktCO2e, or MtCO2e, convert to kgCO2e.\n"
        "- If a field is not present, set it to null. If values are placeholders or uncertain, prefer null.\n"
        "- Include brief qualifiers (e.g., market vs location, boundary) if present.\n"
        "- If you can identify the Scope 2 reporting method (market or location), include scope_2_method.\n"
        "- Only provide a scope value when the snippet explicitly states a greenhouse gas figure with a unit "
        '(e.g., "tCO2e", "tonnes CO2e", "kt CO2e"); otherwise set the value to null.\n'
        "- Provide the sentence or short excerpt that justifies each scope value (scope_1_context, scope_2_context, scope_3_context).\n"
        "- Copy the supporting text verbatim from the snippet; do not paraphrase or summarise it.\n"
        "- Return a numeric confidence from 0.0 to 1.0 that reflects how likely these values are correct given the snippet.\n"
        "Return only JSON with keys: scope_1, scope_1_context, scope_2, scope_2_context, scope_3, scope_3_context, qualifiers, scope_2_method, confidence."
    )
    with suppress(Exception):
        resp = client.responses.parse(
            instructions=instructions,
            input=snippet_text,
            text_format=ParsedResult,
            model="gpt-4o-mini",
            temperature=0,
        )
        return resp.output_parsed
    return None


def _clean_json_response(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        # Remove optional ```json fences
        parts = text.split("```")
        text = ""
        for part in parts:
            part = part.strip()
            if not part:
                continue
            if part.lower().startswith("json"):
                part = part[4:].strip()
            text = part
            break
    if text.endswith("```"):
        text = text[: text.rfind("```")].strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text


def parse_data_llama(llm: Any, snippet_text: str) -> Optional[ParsedResult]:
    if llm is None:
        return None
    prompt = (
        "You will be given a snippet of PDF text from a company's 2025-period report.\n"
        "Extract total Scope 1, Scope 2, and optionally Scope 3 greenhouse gas emissions in kgCO2e as integers. "
        "If the values use units like tCO2e, ktCO2e, or MtCO2e, convert them to kgCO2e. "
        "Set any missing values to null. Capture qualifiers (e.g., boundary or method) if present. "
        "If you can detect the Scope 2 reporting method (market or location), include it. "
        "Only provide a scope value when the snippet explicitly states a greenhouse gas figure with a unit "
        '(e.g., "tCO2e", "tonnes CO2e", "kt CO2e"); otherwise set the value to null. '
        "For each scope value, include the sentence or short excerpt that supports it (scope_1_context, scope_2_context, scope_3_context). "
        "Return a JSON object with keys: scope_1, scope_1_context, scope_2, scope_2_context, scope_3, scope_3_context, qualifiers, scope_2_method, confidence "
        "(confidence should be between 0.0 and 1.0).\n\n"
        "Snippet:\n"
        f"{snippet_text}\n\n"
        "JSON:"
    )
    with suppress(Exception):
        response = llm.create_completion(
            prompt=prompt, temperature=0, max_tokens=512, stop=["\n\n"]
        )
        text = response["choices"][0]["text"]
        cleaned = _clean_json_response(text)
        return ParsedResult.model_validate_json(cleaned)
    return None


def advise_on_failure(
    client: OpenAI,
    snippet_text: str,
    pdf_path: Path,
    company_name: str,
    year: str = "2025",
) -> Optional[Advice]:
    instructions = (
        "You are given a snippet of text extracted from a PDF and the expected company name and reporting year. "
        "Determine why Scope 1/2 emissions could not be confirmed and provide a short recommendation. "
        "Return JSON with: label(one of retry_search, needs_ocr, wrong_company, wrong_report_type, year_mismatch, "
        "insufficient_content, give_up, unknown), reason, suggestion (optional concise query or next step)."
    )
    payload = f"Company: {company_name}\nExpected year: {year}\nPDF file: {pdf_path.name}\n\nSnippet:\n{snippet_text[:8000]}"
    with suppress(Exception):
        resp = client.responses.parse(
            instructions=instructions,
            input=payload,
            text_format=Advice,
            model="gpt-4o-mini",
            temperature=0,
        )
        return resp.output_parsed
    return None


def parse_data_filesearch(
    client: OpenAI,
    vector_store_id: str,
    company_name: str,
    ticker: str,
) -> Optional[ParsedResult]:
    instructions = (
        "Search the provided vector store for this company's 2025-period report content and extract total Scope 1, "
        "Scope 2, and optionally Scope 3 greenhouse gas emissions in kgCO2e as integers. Convert any units to kgCO2e. "
        "Include qualifiers if method (market vs location) or boundary is specified. If a field is not present, set null. "
        "If you can identify the Scope 2 reporting method (market or location), include scope_2_method. "
        "Only provide a scope value when the retrieved text explicitly states a greenhouse gas figure with a unit "
        '(e.g., "tCO2e", "tonnes CO2e", "kt CO2e"); otherwise set the value to null. '
        "Provide the sentence or short excerpt that supports each scope value (scope_1_context, scope_2_context, scope_3_context). "
        "Copy the supporting text verbatim from the retrieved content; do not paraphrase or summarise it. "
        "Also return a numeric confidence from 0.0 to 1.0.\n"
        "Return only JSON with keys: scope_1, scope_1_context, scope_2, scope_2_context, scope_3, scope_3_context, qualifiers, scope_2_method, confidence."
    )
    query = f"Company: {company_name}\nTicker: {ticker}\nTask: Extract Scope 1, Scope 2, Scope 3 totals (kgCO2e)."
    with suppress(Exception):
        resp = client.responses.parse(
            instructions=instructions,
            input=query,
            text_format=ParsedResult,
            tools=[
                {
                    "type": "file_search",
                    "vector_store_ids": [vector_store_id],
                    "max_num_results": 3,
                }
            ],
            include=["file_search_call.results"],
            tool_choice="required",
            temperature=0,
            model="gpt-4o-mini",
        )
        return resp.output_parsed
    return None


def update_company_emissions(
    company: Company,
    parsed: ParsedResult,
    *,
    method: str,
    snippet_label: str,
    snippet_path: Path | None,
    snippet_pages: list[int],
) -> bool:
    if not parsed or parsed.scope_1 is None or parsed.scope_2 is None:
        return False
    if parsed.scope_1 <= 0 or parsed.scope_2 <= 0:
        return False

    scope1_value = _adjust_value_from_context(parsed.scope_1, parsed.scope_1_context)
    scope2_value = _adjust_value_from_context(parsed.scope_2, parsed.scope_2_context)
    scope3_value = _adjust_value_from_context(parsed.scope_3, parsed.scope_3_context)

    if scope1_value is None or scope2_value is None:
        return False

    # Scope 1
    existing_scope1_context = (
        company.emissions.scope_1.context if company.emissions.scope_1 else None
    )
    company.emissions.scope_1 = ScopeValue(
        value=int(scope1_value),
        confidence=parsed.confidence,
        context=(parsed.scope_1_context or existing_scope1_context or "").strip()
        or None,
    )

    # Scope 2
    existing_method = (
        company.emissions.scope_2.method if company.emissions.scope_2 else None
    )
    scope2_method = parsed.scope_2_method or existing_method
    existing_scope2_context = (
        company.emissions.scope_2.context if company.emissions.scope_2 else None
    )
    company.emissions.scope_2 = Scope2Emissions(
        value=int(scope2_value),
        method=scope2_method,
        confidence=parsed.confidence,
        context=(parsed.scope_2_context or existing_scope2_context or "").strip()
        or None,
    )

    # Scope 3
    if scope3_value is not None and scope3_value > 0:
        qualifiers = parsed.qualifiers or (
            company.emissions.scope_3.qualifiers if company.emissions.scope_3 else None
        )
        existing_scope3_context = (
            company.emissions.scope_3.context if company.emissions.scope_3 else None
        )
        company.emissions.scope_3 = Scope3Emissions(
            value=int(scope3_value),
            qualifiers=qualifiers.strip() if qualifiers else None,
            confidence=parsed.confidence,
            context=(parsed.scope_3_context or existing_scope3_context or "").strip()
            or None,
        )
    elif parsed.qualifiers and company.emissions.scope_3:
        company.emissions.scope_3.qualifiers = parsed.qualifiers.strip()

    snippet_path_str = str(snippet_path) if snippet_path else None
    page_numbers = [int(page) for page in snippet_pages if isinstance(page, int)]
    company.analysis_record = AnalysisRecord(
        method=method,
        snippet_label=snippet_label,
        snippet_path=snippet_path_str,
        snippet_pages=page_numbers,
        confidence=float(parsed.confidence or 0.0),
    )

    if (
        page_numbers
        and company.download_record
        and company.download_record.pdf_path
    ):
        pdf_path = Path(company.download_record.pdf_path)
        ensure_page_previews(pdf_path, page_numbers)

    if hasattr(company, "verification") and company.verification is not None:
        company.verification.status = "pending"
        company.verification.verified_at = None
        company.verification.scope_1_override = None
        company.verification.scope_2_override = None
        company.verification.scope_3_override = None
        company.verification.notes = None

    return True
