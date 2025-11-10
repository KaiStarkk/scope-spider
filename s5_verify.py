import sys
import json
import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from openai import OpenAI
from s0_models import Data


DEFAULT_EXTRACT_DIR = Path("extracted")
DEFAULT_DOWNLOAD_DIR = Path("downloads")
VECTOR_STORE_ID_FILE = Path(".vector_store_id")
MANIFEST_FILE = DEFAULT_DOWNLOAD_DIR / "manifest.json"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text() or "null")


def _write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2))


def _load_manifest() -> List[Dict[str, Any]]:
    if MANIFEST_FILE.exists():
        try:
            return json.loads(MANIFEST_FILE.read_text() or "[]")
        except Exception:
            return []
    return []


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


def _find_snippet_for_manifest_row(extract_dir: Path, row: Dict[str, Any]) -> Tuple[Optional[Path], Optional[Dict[str, Any]]]:
    pdf_path = Path(row.get("path", ""))
    if not pdf_path.exists():
        return None, None
    base = pdf_path.stem
    snippet_path = extract_dir / f"{base}.snippet.txt"
    meta_path = extract_dir / f"{base}.json"
    if not snippet_path.exists() or not meta_path.exists():
        return None, None
    try:
        meta = _read_json(meta_path)
    except Exception:
        meta = None
    return snippet_path, meta


def _get_or_create_vector_store(client: OpenAI) -> str:
    if VECTOR_STORE_ID_FILE.exists():
        vsid = VECTOR_STORE_ID_FILE.read_text().strip()
        if vsid:
            return vsid
    store = client.vector_stores.create(name="scope_spider_knowledge")
    VECTOR_STORE_ID_FILE.write_text(store.id)
    return store.id


def _attach_file_to_vector_store(client: OpenAI, vector_store_id: str, path: Path) -> None:
    with path.open("rb") as f:
        fr = client.files.create(file=f, purpose="assistants")
    client.vector_stores.files.create(vector_store_id=vector_store_id, file_id=fr.id)
    # brief wait to allow indexing (best-effort)
    time.sleep(1.0)


def _parse_data_local(client: OpenAI, snippet_text: str) -> Optional[Data]:
    instructions = (
        "You will be given a snippet of PDF text from a company's 2025-period report. "
        "Extract total Scope 1, Scope 2, and optionally Scope 3 greenhouse gas emissions in kgCO2e as integers. "
        "If values are presented in tCO2e, ktCO2e, or MtCO2e, convert to kgCO2e. "
        "If a field is not present, set it to null. If values are obviously placeholders, return null. "
        "Add brief qualifiers if method (market vs location) or boundary is specified."
    )
    try:
        resp = client.responses.parse(
            instructions=instructions,
            input=snippet_text,
            text_format=Data,
            model="gpt-4o-mini",
            temperature=0,
        )
        return resp.output_parsed
    except Exception:
        return None


def _parse_data_filesearch(
    client: OpenAI, vector_store_id: str, company_name: str, ticker: str
) -> Optional[Data]:
    instructions = (
        "Search the provided vector store for this company's 2025-period report content and extract total Scope 1, "
        "Scope 2, and optionally Scope 3 greenhouse gas emissions in kgCO2e as integers. Convert any units to kgCO2e. "
        "Include qualifiers if method (market vs location) or boundary is specified. If a field is not present, set null."
    )
    query = f"Company: {company_name}\nTicker: {ticker}\nTask: Extract Scope 1, Scope 2, Scope 3 totals (kgCO2e)."
    try:
        resp = client.responses.parse(
            instructions=instructions,
            input=query,
            text_format=Data,
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


def _update_company_data(item: Dict[str, Any], parsed: Data, meta: Optional[Dict[str, Any]], method: str) -> bool:
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
    extra = f"Verified via {method}"
    if meta and meta.get("chosen_pages"):
        pages = [int(p) + 1 for p in meta["chosen_pages"]]
        extra += f" on pages {pages}"
    note_bits.append(extra)
    data["qualifiers"] = "; ".join([n for n in note_bits if n]).strip()
    return True


def main():
    if len(sys.argv) < 2:
        sys.exit(
            "Usage: s5_verify.py <companies.json> [--all] [--local-only] [--extracted extracted] [--downloads downloads]"
        )
    companies_path = Path(sys.argv[1])
    verify_all = "--all" in sys.argv[2:]
    local_only = "--local-only" in sys.argv[2:]
    if "--extracted" in sys.argv:
        extract_dir = Path(sys.argv[sys.argv.index("--extracted") + 1])
    else:
        extract_dir = DEFAULT_EXTRACT_DIR
    if "--downloads" in sys.argv:
        downloads_dir = Path(sys.argv[sys.argv.index("--downloads") + 1])
    else:
        downloads_dir = DEFAULT_DOWNLOAD_DIR

    items: List[Dict[str, Any]] = _read_json(companies_path) or []
    manifest = _load_manifest()
    manifest_by_ticker = {}
    for row in manifest:
        t = row.get("ticker")
        if t and row.get("status") == "ok":
            manifest_by_ticker.setdefault(t, []).append(row)

    client = OpenAI()
    changed = False

    # Optional: prepare vector store only if needed
    vector_store_id: Optional[str] = None

    for item in items:
        if not _needs_verification(item, verify_all):
            continue
        name = item.get("name", "").strip()
        ticker = item.get("ticker", "").strip()
        rows = manifest_by_ticker.get(ticker) or []
        if not rows:
            print(f"SKIP {ticker}: no downloaded PDF in {downloads_dir}", flush=True)
            continue
        # First viable row
        row = rows[0]
        snippet_path, meta = _find_snippet_for_manifest_row(extract_dir, row)
        if not snippet_path or not snippet_path.exists():
            print(f"SKIP {ticker}: no snippet found, run s4_extract.py", flush=True)
            continue
        snippet_text = snippet_path.read_text()
        if len(snippet_text.strip()) < 50:
            print(f"NOTE {ticker}: snippet too small; file_search may be needed", flush=True)

        # Attempt local parse first
        parsed = _parse_data_local(client, snippet_text)
        if parsed and _update_company_data(item, parsed, meta, "local snippet"):
            print(f"VERIFIED {ticker}: local", flush=True)
            changed = True
            continue

        if local_only:
            print(f"FAIL {ticker}: local parse failed; skipping due to --local-only", flush=True)
            continue

        # Attach snippet to vector store and try file_search parse
        if vector_store_id is None:
            vector_store_id = _get_or_create_vector_store(client)
        _attach_file_to_vector_store(client, vector_store_id, snippet_path)
        parsed_fs = _parse_data_filesearch(client, vector_store_id, name, ticker)
        if parsed_fs and _update_company_data(item, parsed_fs, meta, "file_search"):
            print(f"VERIFIED {ticker}: file_search", flush=True)
            changed = True
        else:
            print(f"FAIL {ticker}: file_search parse failed", flush=True)

    if changed:
        _write_json(companies_path, items)
        print(f"Updated {companies_path}", flush=True)
    else:
        print("No changes.", flush=True)


if __name__ == "__main__":
    main()
