import sys
import json
import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Literal
from pydantic import BaseModel, Field

from openai import OpenAI


VECTOR_STORE_ID_FILE = Path(".vector_store_id")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text() or "null")


def _write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2))


def _needs_verification(item: Dict[str, Any], verify_all: bool) -> bool:
    if verify_all:
        return True
    report = item.get("report") or {}
    data = report.get("data") or {}
    s1 = data.get("scope_1")
    s2 = data.get("scope_2")
    if s1 is None or s2 is None:
        return True
    if s1 <= 0 or s2 <= 0:
        return True
    return False


def _get_or_create_vector_store(client: OpenAI) -> str:
    if VECTOR_STORE_ID_FILE.exists():
        vsid = VECTOR_STORE_ID_FILE.read_text().strip()
        if vsid:
            return vsid
    store = client.vector_stores.create(name="scope_spider_knowledge")
    VECTOR_STORE_ID_FILE.write_text(store.id)
    return store.id


def _attach_file_to_vector_store(
    client: OpenAI, vector_store_id: str, path: Path
) -> None:
    with path.open("rb") as f:
        fr = client.files.create(file=f, purpose="assistants")
    client.vector_stores.files.create(vector_store_id=vector_store_id, file_id=fr.id)
    # brief wait to allow indexing (best-effort)
    time.sleep(1.0)


class ParsedResult(BaseModel):
    scope_1: Optional[int]
    scope_2: Optional[int]
    scope_3: Optional[int] = None
    qualifiers: Optional[str] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


def _parse_data_local(client: OpenAI, snippet_text: str) -> Optional[ParsedResult]:
    instructions = (
        "You will be given a snippet of PDF text from a company's 2025-period report.\n"
        "- Extract total Scope 1, Scope 2, and optionally Scope 3 greenhouse gas emissions in kgCO2e as integers.\n"
        "- If values are presented in tCO2e, ktCO2e, or MtCO2e, convert to kgCO2e.\n"
        "- If a field is not present, set it to null. If values are placeholders or uncertain, prefer null.\n"
        "- Include brief qualifiers (e.g., market vs location, boundary) if present.\n"
        "- Return a numeric confidence from 0.0 to 1.0 that reflects how likely these values are correct given the snippet.\n"
        "Return only JSON with keys: scope_1, scope_2, scope_3, qualifiers, confidence."
    )
    try:
        resp = client.responses.parse(
            instructions=instructions,
            input=snippet_text,
            text_format=ParsedResult,
            model="gpt-4o-mini",
            temperature=0,
        )
        return resp.output_parsed
    except Exception:
        return None


class Advice(BaseModel):
    label: Literal[
        "retry_search",
        "needs_ocr",
        "wrong_company",
        "wrong_report_type",
        "year_mismatch",
        "insufficient_content",
        "give_up",
        "unknown",
    ] = Field(default="unknown")
    reason: str
    suggestion: Optional[str] = None


def _advise_on_failure(
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
    try:
        resp = client.responses.parse(
            instructions=instructions,
            input=payload,
            text_format=Advice,
            model="gpt-4o-mini",
            temperature=0,
        )
        return resp.output_parsed
    except Exception:
        return None


def _parse_data_filesearch(
    client: OpenAI, vector_store_id: str, company_name: str, ticker: str
) -> Optional[ParsedResult]:
    instructions = (
        "Search the provided vector store for this company's 2025-period report content and extract total Scope 1, "
        "Scope 2, and optionally Scope 3 greenhouse gas emissions in kgCO2e as integers. Convert any units to kgCO2e. "
        "Include qualifiers if method (market vs location) or boundary is specified. If a field is not present, set null. "
        "Also return a numeric confidence from 0.0 to 1.0.\n"
        "Return only JSON with keys: scope_1, scope_2, scope_3, qualifiers, confidence."
    )
    query = f"Company: {company_name}\nTicker: {ticker}\nTask: Extract Scope 1, Scope 2, Scope 3 totals (kgCO2e)."
    try:
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
    except Exception:
        return None


def _update_company_data(
    item: Dict[str, Any],
    parsed: ParsedResult,
    meta: Optional[Dict[str, Any]],
    method: str,
) -> bool:
    if not parsed or parsed.scope_1 is None or parsed.scope_2 is None:
        return False
    if parsed.scope_1 <= 0 or parsed.scope_2 <= 0:
        return False
    report = item.setdefault("report", {})
    data = report.setdefault("data", {})
    data["scope_1"] = int(parsed.scope_1)
    data["scope_2"] = int(parsed.scope_2)
    if parsed.scope_3 is not None and parsed.scope_3 > 0:
        data["scope_3"] = int(parsed.scope_3)
    qualifiers_existing = data.get("qualifiers") or ""
    note_bits = [qualifiers_existing.strip()] if qualifiers_existing else []
    conf_pct = f"{round((parsed.confidence or 0.0) * 100)}%"
    extra = f"Verified via {method} (confidence {conf_pct})"
    if meta and meta.get("chosen_pages"):
        pages = [int(p) + 1 for p in meta["chosen_pages"]]
        extra += f" on pages {pages}"
    note_bits.append(extra)
    if parsed.qualifiers:
        note_bits.append(parsed.qualifiers.strip())
    data["qualifiers"] = "; ".join([n for n in note_bits if n]).strip()
    return True


def main():
    if len(sys.argv) < 2:
        sys.exit(
            "Usage: s5_verify.py <companies.json> [--all] [--local-only] [--threshold 0.7]"
        )
    companies_path = Path(sys.argv[1])
    verify_all = "--all" in sys.argv[2:]
    local_only = "--local-only" in sys.argv[2:]
    # Confidence threshold
    if "--threshold" in sys.argv:
        try:
            threshold = float(sys.argv[sys.argv.index("--threshold") + 1])
        except Exception:
            threshold = 0.7
    else:
        threshold = 0.7
    items: List[Dict[str, Any]] = _read_json(companies_path) or []

    client = OpenAI()
    changed = False

    # Optional: prepare vector store only if needed
    vector_store_id: Optional[str] = None

    # Pre-count items that need verification for progress
    total_need = 0
    for it in items:
        if _needs_verification(it, verify_all):
            total_need += 1
    idx_need = 0

    for item in items:
        if not _needs_verification(item, verify_all):
            continue
        idx_need += 1
        info = item.get("info") or {}
        name = (info.get("name") or "").strip()
        ticker = (info.get("ticker") or "").strip()
        report = item.get("report") or {}
        extraction = report.get("extraction") or {}
        snippet_path = Path(extraction.get("snippet_path") or "")
        meta = extraction or None
        if not (report.get("download") or {}).get("path"):
            print(
                f"SKIP [{idx_need}/{total_need}] {ticker}: no downloaded PDF path in companies.json",
                flush=True,
            )
            continue
        if not snippet_path or not snippet_path.exists():
            print(
                f"SKIP [{idx_need}/{total_need}] {ticker}: no snippet found, run s4_extract.py",
                flush=True,
            )
            continue
        snippet_text = snippet_path.read_text()
        if len(snippet_text.strip()) < 50:
            print(
                f"NOTE [{idx_need}/{total_need}] {ticker}: snippet too small; file_search may be needed",
                flush=True,
            )

        # Attempt local parse first (cheapest)
        parsed_local = _parse_data_local(client, snippet_text)
        if (
            parsed_local
            and parsed_local.scope_1
            and parsed_local.scope_2
            and parsed_local.scope_1 > 0
            and parsed_local.scope_2 > 0
        ):
            if parsed_local.confidence >= threshold:
                if _update_company_data(item, parsed_local, meta, "local snippet"):
                    print(
                        f"VERIFIED [{idx_need}/{total_need}] {ticker}: local (conf={parsed_local.confidence:.2f})",
                        flush=True,
                    )
                    changed = True
                    continue
            else:
                print(
                    f"LOW CONF [{idx_need}/{total_need}] {ticker}: local conf={parsed_local.confidence:.2f} < {threshold:.2f}; trying file_search",
                    flush=True,
                )
        else:
            print(
                f"MISS [{idx_need}/{total_need}] {ticker}: local parse did not yield usable values; trying file_search",
                flush=True,
            )

        if local_only:
            print(
                f"FAIL [{idx_need}/{total_need}] {ticker}: local parse failed; skipping due to --local-only",
                flush=True,
            )
            continue

        # Attach snippet to vector store and try file_search parse
        if vector_store_id is None:
            vector_store_id = _get_or_create_vector_store(client)
        _attach_file_to_vector_store(client, vector_store_id, snippet_path)
        parsed_fs = _parse_data_filesearch(client, vector_store_id, name, ticker)
        if (
            parsed_fs
            and parsed_fs.scope_1
            and parsed_fs.scope_2
            and parsed_fs.scope_1 > 0
            and parsed_fs.scope_2 > 0
        ):
            if parsed_fs.confidence >= threshold:
                if _update_company_data(item, parsed_fs, meta, "file_search"):
                    print(
                        f"VERIFIED [{idx_need}/{total_need}] {ticker}: file_search (conf={parsed_fs.confidence:.2f})",
                        flush=True,
                    )
                    changed = True
            else:
                print(
                    f"LOW CONF [{idx_need}/{total_need}] {ticker}: file_search conf={parsed_fs.confidence:.2f} < {threshold:.2f}",
                    flush=True,
                )
        else:
            print(
                f"FAIL [{idx_need}/{total_need}] {ticker}: file_search parse failed",
                flush=True,
            )
            # Advisory
            advice = _advise_on_failure(
                client,
                snippet_text,
                Path((report.get("download") or {}).get("path") or ""),
                name,
                ((report.get("file") or {}).get("year")) or "2025",
            )
            if advice:
                print(
                    f"ADVISE [{idx_need}/{total_need}] {ticker}: {advice.label} - {advice.reason}"
                    + (
                        f" | suggestion: {advice.suggestion}"
                        if advice.suggestion
                        else ""
                    ),
                    flush=True,
                )

    if changed:
        _write_json(companies_path, items)
        print(f"Updated {companies_path}", flush=True)
    else:
        print("No changes.", flush=True)


if __name__ == "__main__":
    main()
