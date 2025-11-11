from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, TYPE_CHECKING

import pandas as pd
from pydantic import BaseModel, Field
from openai import OpenAI

from src.models import Annotations, Company
from src.utils.companies import dump_companies, load_companies
from src.utils.verification import _clean_json_response  # type: ignore[attr-defined]

if TYPE_CHECKING:  # pragma: no cover
    from llama_cpp import Llama


class ClassificationResult(BaseModel):
    division: Optional[str] = Field(default=None)
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    context: Optional[str] = Field(default=None)

ANZSIC_DIVISIONS = [
    "Agriculture, Forestry and Fishing",
    "Mining",
    "Manufacturing",
    "Electricity, Gas, Water and Waste Services",
    "Construction",
    "Wholesale Trade",
    "Retail Trade",
    "Accommodation and Food Services",
    "Transport, Postal and Warehousing",
    "Information Media and Telecommunications",
    "Financial and Insurance Services",
    "Rental, Hiring and Real Estate Services",
    "Professional, Scientific and Technical Services",
    "Administrative and Support Services",
    "Public Administration and Safety",
    "Education and Training",
    "Health Care and Social Assistance",
    "Arts and Recreation Services",
    "Other Services",
]

PROFITABILITY_COLUMNS = {
    "Year": "profitability_year",
    "Revenue (MM) (AUD)": "profitability_revenue_mm_aud",
    "EBITDA (MM) (AUD)": "profitability_ebitda_mm_aud",
    "Net Income (MM) (AUD)": "profitability_net_income_mm_aud",
    "Total Assets (MM) (AUD)": "profitability_total_assets_mm_aud",
}


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="s6_annotations",
        description="Annotate companies with ANZSIC divisions using gpt-4o-mini by default.",
    )
    parser.add_argument("companies", help="Path to companies.json")
    parser.add_argument(
        "--local-llm",
        help="Optional path to a llama.cpp-compatible GGUF model for secondary classification.",
    )
    parser.add_argument(
        "--local-llm-gpu-layers",
        type=int,
        default=0,
        help="Number of layers to offload to GPU when loading the local model (default: 0).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-run annotations even if existing values are present.",
    )
    return parser.parse_args(argv)


def ensure_local_llm(model_path: Path, gpu_layers: int) -> Optional["Llama"]:
    try:
        from llama_cpp import Llama  # type: ignore[import]
    except ImportError:
        print(
            "WARN: llama_cpp is not available; skipping local LLM classification.",
            flush=True,
        )
        return None

    try:
        return Llama(
            model_path=str(model_path),
            n_ctx=2048,
            embedding=False,
            n_gpu_layers=max(0, gpu_layers),
        )
    except Exception as exc:  # pragma: no cover - hardware dependent
        print(f"WARN: failed to load local LLM ({exc}); skipping local classification.", flush=True)
        return None


def load_profitability_map(xlsx_path: Path) -> Dict[str, Dict[str, Optional[float]]]:
    if not xlsx_path.exists():
        print(f"WARN: profitability file not found ({xlsx_path}).", flush=True)
        return {}
    df = pd.read_excel(xlsx_path, header=3)
    if "Identifier" not in df.columns:
        print("WARN: profitability sheet missing 'Identifier' column.", flush=True)
        return {}
    df = df.dropna(subset=["Identifier"])
    mapping: Dict[str, Dict[str, Optional[float]]] = {}
    for _, row in df.iterrows():
        ident = str(row["Identifier"]).strip().upper()
        if not ident:
            continue
        entry: Dict[str, Optional[float]] = {}
        for col, field in PROFITABILITY_COLUMNS.items():
            val = row.get(col)
            if pd.isna(val):
                entry[field] = None
            else:
                if field == "profitability_year":
                    try:
                        entry[field] = int(val)
                    except (TypeError, ValueError):
                        entry[field] = None
                else:
                    try:
                        entry[field] = float(val)
                    except (TypeError, ValueError):
                        entry[field] = None
        mapping.setdefault(ident, entry)
    return mapping


def call_primary_gpt(client: OpenAI, company_name: str) -> Optional[ClassificationResult]:
    division_list = "\n".join(f"- {div}" for div in ANZSIC_DIVISIONS)
    instructions = (
        "Classify the following Australian company into its most likely ANZSIC division. "
        "Choose exactly one division from the provided list and respond with JSON keys: "
        "division (exact match), confidence (0-1), and context (short supporting sentence).\n"
        f"Divisions:\n{division_list}"
    )
    try:
        resp = client.responses.parse(
            instructions=instructions,
            input=f"Company name: {company_name}",
            text_format=ClassificationResult,
            model="gpt-4o-mini",
            temperature=0,
        )
        return resp.output_parsed
    except Exception as exc:  # pragma: no cover - network error
        print(f"WARN: primary gpt-4o-mini call failed ({exc}); skipping.", flush=True)
        return None


def call_local_llm(llm: "Llama", company_name: str) -> Optional[Dict[str, object]]:
    division_list = "\n".join(f"- {div}" for div in ANZSIC_DIVISIONS)
    prompt = (
        "You are an assistant that maps Australian companies to their ANZSIC division.\n"
        "Choose the single most likely division from the following list:\n"
        f"{division_list}\n\n"
        "Respond with JSON containing keys: division (one of the divisions exactly), "
        "confidence (0.0-1.0), and context (short sentence justifying the choice).\n"
        f"Company name: {company_name}\n"
        "JSON:"
    )
    try:
        response = llm.create_completion(
            prompt=prompt, temperature=0, max_tokens=256, stop=["\n\n"]
        )
    except Exception as exc:  # pragma: no cover - runtime safety
        print(f"WARN: local LLM call failed ({exc}); skipping local classification.", flush=True)
        return None
    text = response["choices"][0]["text"]
    cleaned = _clean_json_response(text)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        print("WARN: unable to parse local LLM response as JSON; skipping local classification.", flush=True)
        return None
    return payload if isinstance(payload, dict) else None


def normalise_division(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    for division in ANZSIC_DIVISIONS:
        if value.lower() == division.lower():
            return division
    return value


def update_profitability(
    annotations: Annotations, ticker: Optional[str], data: Dict[str, Dict[str, Optional[float]]]
) -> bool:
    fields = [
        "profitability_year",
        "profitability_revenue_mm_aud",
        "profitability_ebitda_mm_aud",
        "profitability_net_income_mm_aud",
        "profitability_total_assets_mm_aud",
    ]
    key = (ticker or "").strip().upper()
    info = data.get(key)
    changed = False
    for field in fields:
        new_value = info.get(field) if info else None
        if getattr(annotations, field) != new_value:
            setattr(annotations, field, new_value)
            changed = True
    return changed


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    companies_path = Path(args.companies).expanduser().resolve()

    companies, payload = load_companies(companies_path)
    if not companies:
        print("No companies found.", flush=True)
        return 0

    client = OpenAI()
    profitability_map = load_profitability_map(Path("ASX ESG Screening.xlsx"))

    llm = None
    if args.local_llm:
        local_model_path = Path(args.local_llm).expanduser().resolve()
        if not local_model_path.exists():
            print(f"WARN: --local-llm path not found ({local_model_path}); skipping local classification.", flush=True)
        else:
            llm = ensure_local_llm(local_model_path, args.local_llm_gpu_layers)

    changed = False

    for company in companies:
        name = company.identity.name or company.identity.ticker or "Unknown company"
        annotations: Annotations = company.annotations

        ticker = company.identity.ticker or ""
        if update_profitability(annotations, ticker, profitability_map):
            changed = True

        needs_primary = args.force or annotations.anzsic_division is None
        if needs_primary:
            print(f"ANNOTATE {name}", flush=True)
            primary = call_primary_gpt(client, name)
            if primary:
                division = normalise_division(primary.division)
                confidence = primary.confidence
                context = primary.context
                annotations.anzsic_division = division
                annotations.anzsic_confidence = float(confidence) if confidence is not None else None
                annotations.anzsic_context = (
                    context.strip() if isinstance(context, str) else None
                )
                annotations.anzsic_source = "gpt-4o-mini"
                changed = True
            else:
                print(f"FAIL annotate {name}: gpt-4o-mini returned no result", flush=True)
                continue

            # reset secondary fields when forcing new primary
            annotations.anzsic_local_division = None
            annotations.anzsic_local_confidence = None
            annotations.anzsic_local_context = None
            annotations.anzsic_agreement = None

        if llm is not None:
            local = call_local_llm(llm, name)
            if local:
                division = normalise_division(local.get("division"))
                confidence = local.get("confidence")
                context = local.get("context")
                annotations.anzsic_local_division = division
                annotations.anzsic_local_confidence = (
                    float(confidence) if isinstance(confidence, (int, float)) else None
                )
                annotations.anzsic_local_context = (
                    context.strip() if isinstance(context, str) else None
                )
                if annotations.anzsic_division and division:
                    annotations.anzsic_agreement = (
                        annotations.anzsic_division.lower() == division.lower()
                    )
                else:
                    annotations.anzsic_agreement = None
                changed = True
                print(
                    f"LOCAL CHECK {name}: gpt='{annotations.anzsic_division}' "
                    f"vs local='{division}'",
                    flush=True,
                )

    if changed:
        dump_companies(companies_path, payload, companies)
        print(f"Updated {companies_path}", flush=True)
    else:
        print("No changes.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
