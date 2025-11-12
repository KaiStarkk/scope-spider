from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, TYPE_CHECKING

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
    "Number of Employees": "size_employee_count",
}

TEXT_COLUMNS = {
    "Primary FactSet RBICS Sector": "rbics_sector",
    "Primary FactSet RBICS Sub Sector": "rbics_sub_sector",
    "Primary FactSet RBICS Industry Group": "rbics_industry_group",
    "Primary FactSet RBICS Industry": "rbics_industry",
    "Company Country": "company_country",
    "Company Region": "company_region",
    "Company State": "company_state",
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
    parser.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="Number of parallel workers to use for classification (default: 1).",
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
        print(
            f"WARN: failed to load local LLM ({exc}); skipping local classification.",
            flush=True,
        )
        return None


def load_profitability_map(xlsx_path: Path) -> Dict[str, Dict[str, object]]:
    if not xlsx_path.exists():
        print(f"WARN: profitability file not found ({xlsx_path}).", flush=True)
        return {}
    df = pd.read_excel(xlsx_path, header=3)
    if "Identifier" not in df.columns:
        print("WARN: profitability sheet missing 'Identifier' column.", flush=True)
        return {}
    df = df.dropna(subset=["Identifier"])
    mapping: Dict[str, Dict[str, object]] = {}
    for _, row in df.iterrows():
        ident = str(row["Identifier"]).strip().upper()
        if not ident:
            continue
        entry: Dict[str, object] = {}
        for col, field in PROFITABILITY_COLUMNS.items():
            val = row.get(col)
            if pd.isna(val) or (isinstance(val, str) and not val.strip()) or val == "-":
                entry[field] = None
            else:
                if field == "profitability_year":
                    try:
                        entry[field] = int(val)
                    except (TypeError, ValueError):
                        entry[field] = None
                elif field == "size_employee_count":
                    try:
                        entry[field] = int(float(val))
                    except (TypeError, ValueError):
                        entry[field] = None
                else:
                    try:
                        entry[field] = float(val)
                    except (TypeError, ValueError):
                        entry[field] = None
        for col, field in TEXT_COLUMNS.items():
            raw_val = row.get(col)
            if pd.isna(raw_val):
                entry[field] = None
            else:
                text_val = str(raw_val).strip()
                entry[field] = text_val or None
        company_type = row.get("Company Type Main")
        entry["company_type_main"] = (
            str(company_type).strip() if isinstance(company_type, str) else None
        )
        raw_name = row.get("Name")
        entry["name"] = str(raw_name).strip() if isinstance(raw_name, str) else None
        mapping.setdefault(ident, entry)
    return mapping


def call_primary_gpt(
    client: OpenAI, company_name: str
) -> Optional[ClassificationResult]:
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
        print(
            f"WARN: local LLM call failed ({exc}); skipping local classification.",
            flush=True,
        )
        return None
    text = response["choices"][0]["text"]
    cleaned = _clean_json_response(text)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        print(
            "WARN: unable to parse local LLM response as JSON; skipping local classification.",
            flush=True,
        )
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


RBICS_KEYWORD_MAP: Dict[str, str] = {
    "mining": "Mining",
    "metal": "Mining",
    "lithium": "Mining",
    "coal": "Mining",
    "fossil": "Mining",
    "oil": "Mining",
    "gas": "Electricity, Gas, Water and Waste Services",
    "energy": "Electricity, Gas, Water and Waste Services",
    "utility": "Electricity, Gas, Water and Waste Services",
    "electric": "Electricity, Gas, Water and Waste Services",
    "water": "Electricity, Gas, Water and Waste Services",
    "waste": "Electricity, Gas, Water and Waste Services",
    "financial": "Financial and Insurance Services",
    "finance": "Financial and Insurance Services",
    "bank": "Financial and Insurance Services",
    "insurance": "Financial and Insurance Services",
    "capital": "Financial and Insurance Services",
    "fund": "Financial and Insurance Services",
    "real estate": "Rental, Hiring and Real Estate Services",
    "property": "Rental, Hiring and Real Estate Services",
    "construction": "Construction",
    "engineering": "Construction",
    "logistic": "Transport, Postal and Warehousing",
    "transport": "Transport, Postal and Warehousing",
    "shipping": "Transport, Postal and Warehousing",
    "retail": "Retail Trade",
    "wholesale": "Wholesale Trade",
    "telecom": "Information Media and Telecommunications",
    "communication": "Information Media and Telecommunications",
    "media": "Information Media and Telecommunications",
    "technology": "Information Media and Telecommunications",
    "software": "Information Media and Telecommunications",
    "health": "Health Care and Social Assistance",
    "medical": "Health Care and Social Assistance",
    "hospital": "Health Care and Social Assistance",
    "pharma": "Health Care and Social Assistance",
    "biotech": "Health Care and Social Assistance",
    "education": "Education and Training",
    "training": "Education and Training",
    "agric": "Agriculture, Forestry and Fishing",
    "farming": "Agriculture, Forestry and Fishing",
    "forest": "Agriculture, Forestry and Fishing",
    "food": "Manufacturing",
    "manufact": "Manufacturing",
    "industrial": "Manufacturing",
    "chemical": "Manufacturing",
    "government": "Public Administration and Safety",
}


def derive_anzsic_from_rbics(info: Dict[str, object]) -> Optional[str]:
    candidates: List[str] = []
    for key in ("rbics_industry", "rbics_industry_group", "rbics_sub_sector", "rbics_sector"):
        value = info.get(key)
        if isinstance(value, str):
            text = value.strip()
            if text:
                candidates.append(text)

    for candidate in candidates:
        lowered = candidate.lower()
        for keyword, division in RBICS_KEYWORD_MAP.items():
            if keyword in lowered:
                return division
    return None


def determine_reporting_group(info: Optional[Dict[str, Any]]) -> Optional[str]:
    if not info:
        return None

    def exceeds(value: Optional[float], threshold: float) -> bool:
        return value is not None and value > threshold

    employees = info.get("size_employee_count")
    employees_count = int(employees) if isinstance(employees, (int, float)) else None
    revenue = info.get("profitability_revenue_mm_aud")
    revenue_mm = float(revenue) if isinstance(revenue, (int, float)) else None
    assets = info.get("profitability_total_assets_mm_aud")
    assets_mm = float(assets) if isinstance(assets, (int, float)) else None
    company_type = info.get("company_type_main")
    name = info.get("name")

    def is_super_fund() -> bool:
        sources = [company_type, name]
        for source in sources:
            if isinstance(source, str) and "super" in source.lower():
                return True
        return False

    # Special rule: super funds with > 5,000 MM assets (i.e. > 5B) map to Group 2.
    if is_super_fund() and exceeds(assets_mm, 5_000):
        return "Group 2"

    if (
        exceeds(employees_count, 500)
        or exceeds(revenue_mm, 500)
        or exceeds(assets_mm, 1_000)
    ):
        return "Group 1"
    if (
        exceeds(employees_count, 250)
        or exceeds(revenue_mm, 200)
        or exceeds(assets_mm, 500)
    ):
        return "Group 2"
    if (
        exceeds(employees_count, 100)
        or exceeds(revenue_mm, 50)
        or exceeds(assets_mm, 25)
    ):
        return "Group 3"
    return None


def update_profitability(
    annotations: Annotations,
    ticker: Optional[str],
    data: Dict[str, Dict[str, object]],
) -> bool:
    numeric_fields = [
        "profitability_year",
        "profitability_revenue_mm_aud",
        "profitability_ebitda_mm_aud",
        "profitability_net_income_mm_aud",
        "profitability_total_assets_mm_aud",
        "size_employee_count",
    ]
    text_fields = list(TEXT_COLUMNS.values())
    key = (ticker or "").strip().upper()
    info = data.get(key)
    changed = False
    if info:
        for field in numeric_fields:
            new_value = info.get(field)
            if field == "size_employee_count" and isinstance(new_value, float):
                new_value = int(new_value)
            if getattr(annotations, field) != new_value:
                setattr(annotations, field, new_value)
                changed = True
        for field in text_fields:
            new_text = info.get(field)
            if getattr(annotations, field, None) != new_text:
                setattr(annotations, field, new_text)
                changed = True
        mapped_division = derive_anzsic_from_rbics(info)
        if mapped_division:
            normalised = normalise_division(mapped_division)
            if annotations.anzsic_division != normalised or annotations.anzsic_source != "rbics":
                annotations.anzsic_division = normalised
                annotations.anzsic_source = "rbics"
                annotations.anzsic_confidence = None
                annotations.anzsic_context = info.get("rbics_sector") or info.get(
                    "rbics_industry_group"
                )
                changed = True
    else:
        for field in numeric_fields + text_fields:
            if getattr(annotations, field) is not None:
                setattr(annotations, field, None)
                changed = True
    new_group = determine_reporting_group(info)
    if annotations.reporting_group != new_group:
        annotations.reporting_group = new_group
        changed = True
    return changed


def annotate_company(
    company: Company,
    *,
    ensure_client: Callable[[], OpenAI],
    llm: Optional["Llama"],
    force: bool,
    log: Callable[[str], None],
) -> bool:
    changed = False
    name = company.identity.name or company.identity.ticker or "Unknown company"
    annotations: Annotations = company.annotations

    ticker = company.identity.ticker or ""
    derived_from_rbics = annotations.anzsic_source == "rbics"
    needs_primary = (annotations.anzsic_division is None) or (force and not derived_from_rbics)
    if needs_primary:
        log(f"ANNOTATE {name}")
        primary = call_primary_gpt(ensure_client(), name)
        if primary:
            division = normalise_division(primary.division)
            confidence = primary.confidence
            context = primary.context
            annotations.anzsic_division = division
            annotations.anzsic_confidence = (
                float(confidence) if confidence is not None else None
            )
            annotations.anzsic_context = (
                context.strip() if isinstance(context, str) else None
            )
            annotations.anzsic_source = "gpt-4o-mini"
            changed = True
        else:
            log(f"FAIL annotate {name}: gpt-4o-mini returned no result")
            return changed

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
            log(
                f"LOCAL CHECK {name}: gpt='{annotations.anzsic_division}' "
                f"vs local='{division}'"
            )

    return changed


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    companies_path = Path(args.companies).expanduser().resolve()

    companies, payload = load_companies(companies_path)
    if not companies:
        print("No companies found.", flush=True)
        return 0

    profitability_map = load_profitability_map(Path("ASX ESG Screening.xlsx"))

    llm = None
    if args.local_llm:
        local_model_path = Path(args.local_llm).expanduser().resolve()
        if not local_model_path.exists():
            print(
                f"WARN: --local-llm path not found ({local_model_path}); skipping local classification.",
                flush=True,
            )
        else:
            llm = ensure_local_llm(local_model_path, args.local_llm_gpu_layers)

    jobs = max(1, args.jobs)
    if jobs > 1 and llm is not None:
        print(
            "WARN: --jobs > 1 currently requires --local-llm to be disabled; running sequentially.",
            flush=True,
        )
        jobs = 1

    changed = False

    def process_company(
        company_obj: Company,
        *,
        ensure_client_fn: Callable[[], OpenAI],
        llm_instance: Optional["Llama"],
        force_flag: bool,
        log_fn: Callable[[str], None],
    ) -> bool:
        local_changed = False
        ticker = company_obj.identity.ticker or ""
        if update_profitability(company_obj.annotations, ticker, profitability_map):
            local_changed = True
        if annotate_company(
            company_obj,
            ensure_client=ensure_client_fn,
            llm=llm_instance,
            force=force_flag,
            log=log_fn,
        ):
            local_changed = True
        return local_changed

    if jobs == 1:
        cached_client: Optional[OpenAI] = None

        def ensure_client_single() -> OpenAI:
            nonlocal cached_client
            if cached_client is None:
                cached_client = OpenAI()
            return cached_client

        for company in companies:
            name = company.identity.ticker or company.identity.name or "Unknown company"

            def log_single(message: str) -> None:
                print(message, flush=True)

            if process_company(
                company,
                ensure_client_fn=ensure_client_single,
                llm_instance=llm,
                force_flag=args.force,
                log_fn=log_single,
            ):
                changed = True
                dump_companies(companies_path, payload, companies)
    else:
        print(f"Annotating with {jobs} parallel workers.", flush=True)

        def worker(
            index: int,
            company_payload: Dict[str, Any],
        ) -> Tuple[int, Dict[str, Any], List[str], bool]:
            company_obj = Company.model_validate(company_payload)
            logs: List[str] = []
            cached_worker_client: Optional[OpenAI] = None

            def ensure_client_worker() -> OpenAI:
                nonlocal cached_worker_client
                if cached_worker_client is None:
                    cached_worker_client = OpenAI()
                return cached_worker_client

            def log_worker(message: str) -> None:
                logs.append(message)

            local_changed = process_company(
                company_obj,
                ensure_client_fn=ensure_client_worker,
                llm_instance=None,
                force_flag=args.force,
                log_fn=log_worker,
            )
            return index, company_obj.model_dump(mode="json"), logs, local_changed

        with ThreadPoolExecutor(max_workers=jobs) as executor:
            futures = [
                executor.submit(worker, idx, company.model_dump(mode="json"))
                for idx, company in enumerate(companies)
            ]
            for future in as_completed(futures):
                idx, payload_data, logs, local_changed = future.result()
                for line in logs:
                    print(line, flush=True)
                companies[idx] = Company.model_validate(payload_data)
                if local_changed:
                    changed = True
                    dump_companies(companies_path, payload, companies)

    if not changed:
        print("No changes.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
