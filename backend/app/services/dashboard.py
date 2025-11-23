from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

from backend.domain.models import Company


def companies_to_dataframe(companies: Sequence[Company]) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for company in companies:
        identity = company.identity
        emissions = company.emissions
        annotations = company.annotations

        scope1 = emissions.scope_1.value if emissions.scope_1 else None
        scope2 = emissions.scope_2.value if emissions.scope_2 else None
        scope1_conf = emissions.scope_1.confidence if emissions.scope_1 else None
        scope2_conf = emissions.scope_2.confidence if emissions.scope_2 else None
        net_income = annotations.profitability_net_income_mm_aud
        revenue = annotations.profitability_revenue_mm_aud
        ebitda = annotations.profitability_ebitda_mm_aud
        assets = annotations.profitability_total_assets_mm_aud
        employees = annotations.size_employee_count
        reporting_group = annotations.reporting_group
        country = annotations.company_country
        region = annotations.company_region
        state = annotations.company_state
        rbics_sector = annotations.rbics_sector
        rbics_sub_sector = annotations.rbics_sub_sector
        rbics_group = annotations.rbics_industry_group
        rbics_industry = annotations.rbics_industry
        analysis_method = company.analysis_record.method if company.analysis_record else None

        rows.append(
            {
                "ticker": identity.ticker,
                "name": identity.name or identity.ticker,
                "scope_1": float(scope1) if scope1 is not None else None,
                "scope_2": float(scope2) if scope2 is not None else None,
                "scope_1_conf": float(scope1_conf) if scope1_conf is not None else None,
                "scope_2_conf": float(scope2_conf) if scope2_conf is not None else None,
                "anzsic_division": annotations.anzsic_division,
                "anzsic_context": annotations.anzsic_context,
                "anzsic_source": annotations.anzsic_source,
                "revenue_mm": float(revenue) if revenue is not None else None,
                "net_income_mm": float(net_income) if net_income is not None else None,
                "ebitda_mm": float(ebitda) if ebitda is not None else None,
                "assets_mm": float(assets) if assets is not None else None,
                "employees": int(employees) if employees is not None else None,
                "net_zero_mentions": int(annotations.net_zero_claims)
                if annotations.net_zero_claims is not None
                else None,
                "reporting_group": reporting_group,
                "company_country": country,
                "company_region": region,
                "company_state": state,
                "rbics_sector": rbics_sector,
                "rbics_sub_sector": rbics_sub_sector,
                "rbics_industry_group": rbics_group,
                "rbics_industry": rbics_industry,
                "analysis_method": analysis_method,
                "year": annotations.profitability_year,
            }
        )

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    s1 = pd.to_numeric(df["scope_1"], errors="coerce")
    s2 = pd.to_numeric(df["scope_2"], errors="coerce")
    combined = s1.fillna(0) + s2.fillna(0)
    combined[(s1.isna()) & (s2.isna())] = pd.NA
    df["scope_1_total"] = combined
    return df


@dataclass
class DashboardFilters:
    industries: Optional[List[str]] = None
    rbics: Optional[List[str]] = None
    states: Optional[List[str]] = None
    methods: Optional[List[str]] = None
    scope1_range: Optional[Tuple[float, float]] = None
    net_income_range: Optional[Tuple[float, float]] = None
    revenue_range: Optional[Tuple[float, float]] = None


def _column_range(df: pd.DataFrame, column: str) -> Optional[Tuple[float, float]]:
    if column not in df:
        return None
    numeric = pd.to_numeric(df[column], errors="coerce").dropna()
    if numeric.empty:
        return None
    minimum = float(numeric.min())
    maximum = float(numeric.max())
    if minimum == maximum:
        maximum = minimum + 1.0
    return minimum, maximum


def _apply_filters(df: pd.DataFrame, filters: DashboardFilters) -> pd.DataFrame:
    filtered = df.copy()
    if filters.industries:
        filtered = filtered[filtered["anzsic_division"].isin(filters.industries)]
    if filters.rbics:
        filtered = filtered[filtered["rbics_sector"].isin(filters.rbics)]
    if filters.states:
        filtered = filtered[filtered["company_state"].isin(filters.states)]
    if filters.methods:
        filtered = filtered[filtered["analysis_method"].isin(filters.methods)]

    if filters.scope1_range:
        s_min, s_max = filters.scope1_range
        scope_series = pd.to_numeric(filtered["scope_1_total"], errors="coerce")
        filtered = filtered[
            scope_series.between(s_min, s_max, inclusive="both") | scope_series.isna()
        ]
    if filters.net_income_range:
        n_min, n_max = filters.net_income_range
        filtered = filtered[
            pd.to_numeric(filtered["net_income_mm"], errors="coerce").between(n_min, n_max, inclusive="both")
            | filtered["net_income_mm"].isna()
        ]
    if filters.revenue_range:
        r_min, r_max = filters.revenue_range
        filtered = filtered[
            pd.to_numeric(filtered["revenue_mm"], errors="coerce").between(r_min, r_max, inclusive="both")
            | filtered["revenue_mm"].isna()
        ]
    return filtered


def _records(df: pd.DataFrame, columns: Iterable[str]) -> List[Dict[str, Any]]:
    if df.empty:
        return []
    subset = df[list(columns)]
    subset = subset.replace({pd.NA: None}).where(pd.notnull(subset), None)
    return subset.to_dict(orient="records")


def company_stage_summary(companies: Sequence[Company]) -> Dict[str, int]:
    total = len(companies)
    searched = sum(1 for company in companies if company.search_record is not None)
    downloaded = sum(
        1
        for company in companies
        if company.download_record and company.download_record.pdf_path
    )
    extracted = sum(1 for company in companies if company.extraction_record is not None)
    analysed = sum(1 for company in companies if company.analysis_record is not None)
    verified = sum(
        1
        for company in companies
        if company.verification and company.verification.status == "accepted"
    )
    return {
        "total": total,
        "searched": searched,
        "downloaded": downloaded,
        "extracted": extracted,
        "analysed": analysed,
        "verified": verified,
    }


def _match_filtered_companies(
    companies: Sequence[Company], filtered_df: pd.DataFrame
) -> List[Company]:
    if filtered_df.empty:
        return []
    ticker_values = {
        str(value).strip().upper()
        for value in filtered_df["ticker"].dropna().unique()
        if str(value).strip()
    }
    name_values = {
        str(value).strip().lower()
        for value in filtered_df["name"].dropna().unique()
        if str(value).strip()
    }
    if not ticker_values and not name_values:
        return []
    matched: List[Company] = []
    for company in companies:
        ticker = (company.identity.ticker or "").strip().upper()
        name = (company.identity.name or "").strip().lower()
        if ticker and ticker in ticker_values:
            matched.append(company)
            continue
        if name and name in name_values:
            matched.append(company)
    return matched


def build_dashboard_metrics(
    companies: Sequence[Company],
    filters: DashboardFilters,
) -> Dict[str, Any]:
    df = companies_to_dataframe(companies)

    overall_stages = company_stage_summary(companies)

    response: Dict[str, Any] = {
        "filters": {
            "industries": sorted({str(v) for v in df["anzsic_division"].dropna().unique()})
            if not df.empty and "anzsic_division" in df
            else [],
            "rbics": sorted({str(v) for v in df["rbics_sector"].dropna().unique()})
            if not df.empty and "rbics_sector" in df
            else [],
            "states": sorted({str(v) for v in df["company_state"].dropna().unique()})
            if not df.empty and "company_state" in df
            else [],
            "methods": sorted({str(v) for v in df["analysis_method"].dropna().unique()})
            if not df.empty and "analysis_method" in df
            else [],
        },
        "ranges": {
            "scope1": _column_range(df, "scope_1_total"),
            "net_income": _column_range(df, "net_income_mm"),
            "revenue": _column_range(df, "revenue_mm"),
        },
        "summary": {
            "total_companies": len(df),
            "filtered_companies": 0,
            "stages": overall_stages,
            "filtered_stages": {
                "total": 0,
                "searched": 0,
                "downloaded": 0,
                "extracted": 0,
                "analysed": 0,
                "verified": 0,
            },
        },
    }

    if df.empty:
        response.update(
            {
                "scatter": {},
                "scope_averages": [],
                "group_matrix": {"rows": [], "columns": [], "cells": []},
                "table": [],
            }
        )
        return response

    filtered = _apply_filters(df, filters)
    response["summary"]["filtered_companies"] = len(filtered)
    filtered_companies = _match_filtered_companies(companies, filtered)
    response["summary"]["filtered_stages"] = company_stage_summary(filtered_companies)

    if filtered.empty:
        response.update(
            {
                "scatter": {},
                "scope_averages": [],
                "group_matrix": {"rows": [], "columns": [], "cells": []},
                "table": [],
            }
        )
        return response

    # Prepare scatter datasets
    def scatter(metric_column: str, metric_label: str) -> List[Dict[str, Any]]:
        columns = ["scope_1", "scope_2", "anzsic_division", "name"]
        if metric_column not in columns:
            columns.append(metric_column)
        if "revenue_mm" not in columns:
            columns.append("revenue_mm")
        missing = [col for col in columns if col not in filtered.columns]
        if missing:
            return []
        frame = filtered[columns].copy()
        frame["scope_1"] = pd.to_numeric(frame["scope_1"], errors="coerce")
        frame["scope_2"] = pd.to_numeric(frame["scope_2"], errors="coerce")
        combined = frame["scope_1"].fillna(0) + frame["scope_2"].fillna(0)
        valid_scope = frame["scope_1"].notna() | frame["scope_2"].notna()
        frame = frame[valid_scope].copy()
        frame["scope_1"] = combined[valid_scope]
        frame = frame.drop(columns=["scope_2"])
        frame = frame.dropna(subset=["scope_1", metric_column])
        frame[metric_column] = pd.to_numeric(frame[metric_column], errors="coerce")
        if "revenue_mm" in frame.columns:
            frame["revenue_mm"] = pd.to_numeric(frame["revenue_mm"], errors="coerce")
        else:
            frame["revenue_mm"] = frame[metric_column]
        frame["revenue_mm"] = frame["revenue_mm"].fillna(1.0)
        if frame.empty:
            return []
        frame = frame.where(pd.notnull(frame), None)
        rename_dict = {
            "scope_1": "scope_1",
            metric_column: metric_label,
            "anzsic_division": "industry",
            "name": "company",
        }
        # Only include revenue_mm in rename if it's not the metric_column being renamed
        if "revenue_mm" in frame.columns and metric_column != "revenue_mm":
            rename_dict["revenue_mm"] = "revenue_mm"
        return frame.rename(columns=rename_dict).to_dict(orient="records")

    scatter_payload = {
        "scope1_vs_net_income": scatter("net_income_mm", "net_income"),
        "scope1_vs_revenue": scatter("revenue_mm", "revenue"),
        "scope1_vs_ebitda": scatter("ebitda_mm", "ebitda"),
        "scope1_vs_assets": scatter("assets_mm", "assets"),
        "scope1_vs_employees": scatter("employees", "employees"),
        "scope1_vs_net_zero_mentions": scatter("net_zero_mentions", "net_zero_mentions"),
    }

    averages = (
        filtered.groupby("anzsic_division")[["scope_1", "scope_2"]].mean(numeric_only=True).reset_index()
        if "anzsic_division" in filtered
        else pd.DataFrame(columns=["anzsic_division", "scope_1", "scope_2"])
    )
    averages = averages.rename(
        columns={
            "anzsic_division": "industry",
            "scope_1": "scope_1_avg",
            "scope_2": "scope_2_avg",
        }
    )
    averages = averages.replace({pd.NA: None}).where(pd.notnull(averages), None)

    group_data = filtered.copy()
    group_data["reporting_group"] = group_data["reporting_group"].fillna("None")
    group_data["anzsic_division"] = group_data["anzsic_division"].fillna("Unknown")
    pivot_counts = (
        group_data.pivot_table(
            index="reporting_group",
            columns="anzsic_division",
            values="ticker",
            aggfunc="nunique",
            fill_value=0,
        )
        if not group_data.empty
        else pd.DataFrame()
    )
    pivot_emissions = (
        group_data.pivot_table(
            index="reporting_group",
            columns="anzsic_division",
            values="scope_1",
            aggfunc="sum",
            fill_value=0.0,
        )
        if not group_data.empty
        else pd.DataFrame()
    )

    if pivot_counts.empty or pivot_emissions.empty:
        group_matrix = {"rows": [], "columns": [], "cells": []}
    else:
        pivot_counts = pivot_counts.sort_index().sort_index(axis=1)
        pivot_emissions = pivot_emissions.reindex(index=pivot_counts.index, columns=pivot_counts.columns).fillna(0.0)
        rows = []
        for group in pivot_counts.index:
            cells = []
            for industry in pivot_counts.columns:
                cells.append(
                    {
                        "industry": industry,
                        "count": int(pivot_counts.loc[group, industry]),
                        "emissions": float(pivot_emissions.loc[group, industry]),
                    }
                )
            rows.append({"group": group, "cells": cells})
        group_matrix = {
            "rows": rows,
            "columns": list(pivot_counts.columns),
            "cells": [
                {
                    "group": row["group"],
                    "industry": cell["industry"],
                    "count": cell["count"],
                    "emissions": cell["emissions"],
                }
                for row in rows
                for cell in row["cells"]
            ],
        }

    table_columns = [
        "ticker",
        "name",
        "anzsic_division",
        "scope_1",
        "scope_2",
        "revenue_mm",
        "net_income_mm",
        "ebitda_mm",
        "assets_mm",
        "employees",
        "net_zero_mentions",
        "reporting_group",
        "company_state",
        "company_region",
        "company_country",
        "rbics_sector",
        "rbics_sub_sector",
        "rbics_industry_group",
        "rbics_industry",
        "analysis_method",
    ]
    table_df = filtered[table_columns].copy()
    table_df = table_df.sort_values(by="net_income_mm", ascending=False)
    table_df = table_df.replace({pd.NA: None}).where(pd.notnull(table_df), None)

    response.update(
        {
            "scatter": scatter_payload,
            "scope_averages": _records(averages, ["industry", "scope_1_avg", "scope_2_avg"]),
            "group_matrix": group_matrix,
            "table": table_df.to_dict(orient="records"),
        }
    )
    return response


__all__ = [
    "DashboardFilters",
    "companies_to_dataframe",
    "company_stage_summary",
    "build_dashboard_metrics",
]
