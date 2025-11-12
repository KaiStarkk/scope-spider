from __future__ import annotations

import argparse
import math
from datetime import datetime
import base64
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, cast
from urllib.parse import urlparse

import dash
from dash import Dash, Input, Output, State, dcc, html, no_update
from dash.dash_table import DataTable
from dash.exceptions import PreventUpdate
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from src.models import (
    AnalysisRecord,
    Company,
    DownloadRecord,
    Scope2Emissions,
    Scope3Emissions,
    ScopeValue,
    SearchRecord,
)
from src.utils.companies import dump_companies, load_companies
from src.utils.pdf_preview import previews_as_data_urls
from src.utils.documents import (
    classify_document_type,
    infer_year_from_text,
    normalise_pdf_url,
)
from src.utils.query import derive_filename
from src.s0_stats import reset_company_stages, STAGE_DEPENDENCIES


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="s7_dashboard",
        description="Interactive dashboard for emissions insights and verification.",
    )
    parser.add_argument("companies", help="Path to companies.json")
    parser.add_argument(
        "--host", default="127.0.0.1", help="Dashboard host (default 127.0.0.1)"
    )
    parser.add_argument(
        "--port", type=int, default=8050, help="Dashboard port (default 8050)"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Run Dash in debug mode (enables hot reload; not for production).",
    )
    return parser.parse_args(argv)


def companies_to_dataframe(companies: List[Company]) -> pd.DataFrame:
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
        analysis_method = (
            company.analysis_record.method if company.analysis_record else None
        )

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
                "anzsic_local_division": annotations.anzsic_local_division,
                "anzsic_source": annotations.anzsic_source,
                "revenue_mm": float(revenue) if revenue is not None else None,
                "net_income_mm": float(net_income) if net_income is not None else None,
                "ebitda_mm": float(ebitda) if ebitda is not None else None,
                "assets_mm": float(assets) if assets is not None else None,
                "employees": int(employees) if employees is not None else None,
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

    return pd.DataFrame(rows)


def serialise_companies(companies: List[Company]) -> List[Dict[str, Any]]:
    return [company.model_dump(mode="json") for company in companies]


def deserialise_companies(data: Optional[List[Dict[str, Any]]]) -> List[Company]:
    if not data:
        return []
    return [Company.model_validate(item) for item in data]


def company_key(company: Company) -> str:
    return (company.identity.ticker or company.identity.name or "").strip()


def company_label(company: Company) -> str:
    ticker = company.identity.ticker or ""
    name = company.identity.name or ticker or "Unknown"
    status = company.verification.status if company.verification else "pending"
    return f"{ticker or name} — {name} (status: {status})"


def read_snippet_text(snippet_path: Optional[str]) -> Optional[str]:
    if not snippet_path:
        return None
    try:
        return Path(snippet_path).read_text(encoding="utf-8")
    except OSError:
        return None


STAGE_LABELS = {
    "s2": "search",
    "s3": "download",
    "s4": "extraction",
    "s5": "analysis",
    "s6": "annotations",
}


def _ordered_stage_dependencies(stages: Iterable[str]) -> List[str]:
    ordered: List[str] = []
    seen: Set[str] = set()
    for stage in stages:
        for dependent in STAGE_DEPENDENCIES.get(stage, ()):
            if dependent not in seen:
                ordered.append(dependent)
                seen.add(dependent)
    return ordered


def _clean_company_token(value: Optional[str]) -> str:
    text = (value or "").strip() or "company"
    return re.sub(r"[^A-Za-z0-9]+", "_", text)


def _normalise_upload_filename(filename: Optional[str]) -> str:
    if not filename:
        return "uploaded.pdf"
    name = Path(filename).name or "uploaded.pdf"
    if not name.lower().endswith(".pdf"):
        name = f"{name}.pdf"
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name)


def _decode_uploaded_pdf(contents: str) -> bytes:
    if not contents:
        raise ValueError("No upload payload provided.")
    try:
        header, encoded = contents.split(",", 1)
    except ValueError as exc:
        raise ValueError("Uploaded file payload is malformed.") from exc
    if "pdf" not in header.lower():
        raise ValueError("Uploaded file does not appear to be a PDF.")
    try:
        data = base64.b64decode(encoded)
    except ValueError as exc:
        raise ValueError("Failed to decode uploaded PDF.") from exc
    if not data.startswith(b"%PDF"):
        raise ValueError("Uploaded file is not a valid PDF document.")
    return data


def _store_uploaded_pdf(
    base_dir: Path,
    company: Company,
    filename: Optional[str],
    data: bytes,
) -> Path:
    safe_ticker = _clean_company_token(company.identity.ticker)
    safe_name = _clean_company_token(company.identity.name)
    identifier = safe_ticker or safe_name or "company"
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    sanitized_filename = _normalise_upload_filename(filename)

    download_dir = (base_dir / "downloads").resolve()
    download_dir.mkdir(parents=True, exist_ok=True)

    destination = download_dir / f"{identifier}_{timestamp}_{sanitized_filename}"
    destination.write_bytes(data)

    try:
        relative = destination.relative_to(base_dir)
        return relative
    except ValueError:
        return destination


def next_pending_key(
    companies: List[Company],
    current_key: Optional[str],
    *,
    skip_current: bool = False,
    allowed_methods: Optional[set[str]] = None,
) -> Optional[str]:
    pending_keys: List[str] = []
    for company in companies:
        if company.verification.status == "accepted":
            continue
        method = company.analysis_record.method if company.analysis_record else None
        if allowed_methods and method not in allowed_methods:
            continue
        pending_keys.append(company_key(company))
    if not pending_keys:
        return None
    if current_key not in pending_keys:
        return pending_keys[0]
    if skip_current:
        if len(pending_keys) == 1:
            return pending_keys[0]
        current_index = pending_keys.index(current_key)
        return pending_keys[(current_index + 1) % len(pending_keys)]
    return current_key


def create_dash_app(
    companies_path: Path,
    companies: List[Company],
    payload: Dict[str, Any],
) -> Dash:
    app = Dash(__name__)
    app.title = "Scope Spider"

    initial_df = companies_to_dataframe(companies)
    if not initial_df.empty and "anzsic_division" in initial_df:
        industries_series = initial_df["anzsic_division"].dropna()
        industries = sorted([str(i) for i in industries_series.unique()])
    else:
        industries = []
    if not initial_df.empty and "rbics_sector" in initial_df:
        rbics_sectors_list = sorted(
            {str(value) for value in initial_df["rbics_sector"].dropna().unique()}
        )
    else:
        rbics_sectors_list = []
    if not initial_df.empty and "company_state" in initial_df:
        state_list = sorted(
            {str(value) for value in initial_df["company_state"].dropna().unique()}
        )
    else:
        state_list = []
    if not initial_df.empty and "analysis_method" in initial_df:
        method_list = sorted(
            {
                str(value)
                for value in initial_df["analysis_method"].dropna().unique()
                if str(value).strip()
            }
        )
    else:
        method_list = []

    def _calc_range(column: str, default: Tuple[float, float]) -> Tuple[float, float]:
        if initial_df.empty or column not in initial_df:
            return default
        numeric = pd.to_numeric(initial_df[column], errors="coerce").dropna()
        if numeric.empty:
            return default
        minimum = float(numeric.min())
        maximum = float(numeric.max())
        if minimum == maximum:
            maximum = minimum + 1.0
        return minimum, maximum

    scope1_min, scope1_max = _calc_range("scope_1", (0.0, 1.0))
    net_min, net_max = _calc_range("net_income_mm", (0.0, 1.0))
    rev_min, rev_max = _calc_range("revenue_mm", (0.0, 1.0))

    def _build_slider_marks(
        minimum: float,
        maximum: float,
        *,
        segments: int = 4,
    ) -> dict[float, str]:
        if not math.isfinite(minimum) or not math.isfinite(maximum):
            return {}
        if minimum == maximum:
            return {minimum: f"{minimum:,.0f}"}
        if segments <= 0:
            segments = 1
        span = maximum - minimum
        step = span / segments
        marks: dict[float, str] = {}
        for index in range(segments + 1):
            value = minimum + step * index
            # Avoid floating artefacts in keys to keep slider happy
            key = float(round(value, 6))
            marks[key] = f"{value:,.0f}"
        return marks

    insights_tab = html.Div(
        [
            html.Div(
                [
                    html.Div(
                        [
                            html.Label("Industry / Sector"),
                            dcc.Dropdown(
                                id="industry-filter",
                                options=[{"label": i, "value": i} for i in industries],
                                multi=True,
                                placeholder="Select one or more industries",
                                value=industries if industries else None,
                            ),
                        ],
                        className="filter-block",
                    ),
                    html.Div(
                        [
                            html.Label("RBICS Sector"),
                            dcc.Dropdown(
                                id="rbics-filter",
                                options=[
                                    {"label": value, "value": value}
                                    for value in rbics_sectors_list
                                ],
                                multi=True,
                                placeholder="Select RBICS sector(s)",
                                value=rbics_sectors_list if rbics_sectors_list else None,
                            ),
                        ],
                        className="filter-block",
                    ),
                    html.Div(
                        [
                            html.Label("Company State"),
                            dcc.Dropdown(
                                id="state-filter",
                                options=[
                                    {"label": value, "value": value} for value in state_list
                                ],
                                multi=True,
                                placeholder="Select state(s)",
                                value=state_list if state_list else None,
                            ),
                        ],
                        className="filter-block",
                    ),
                    html.Div(
                        [
                            html.Label("Scope 1 range (kgCO2e)"),
                            dcc.RangeSlider(
                                id="scope1-slider",
                                min=scope1_min,
                                max=scope1_max,
                                value=[scope1_min, scope1_max],
                                marks=_build_slider_marks(scope1_min, scope1_max),
                                step=None,
                                tooltip={
                                    "placement": "bottom",
                                    "always_visible": False,
                                },
                            ),
                        ],
                        className="filter-block",
                    ),
                    html.Div(
                        [
                            html.Label("Net Income range (MM AUD)"),
                            dcc.RangeSlider(
                                id="netincome-slider",
                                min=net_min,
                                max=net_max,
                                value=[net_min, net_max],
                                marks=_build_slider_marks(net_min, net_max),
                                step=None,
                                tooltip={
                                    "placement": "bottom",
                                    "always_visible": False,
                                },
                            ),
                        ],
                        className="filter-block",
                    ),
                    html.Div(
                        [
                            html.Label("Revenue range (MM AUD)"),
                            dcc.RangeSlider(
                                id="revenue-slider",
                                min=rev_min,
                                max=rev_max,
                                value=[rev_min, rev_max],
                                marks=_build_slider_marks(rev_min, rev_max),
                                step=None,
                                tooltip={
                                    "placement": "bottom",
                                    "always_visible": False,
                                },
                            ),
                        ],
                        className="filter-block",
                    ),
                ],
                className="controls",
            ),
            dcc.Graph(id="scatter-emissions-net-income"),
            dcc.Graph(id="scatter-emissions-revenue"),
            dcc.Graph(id="scatter-emissions-ebitda"),
            dcc.Graph(id="scatter-emissions-assets"),
            dcc.Graph(id="bar-top-revenue"),
            dcc.Graph(id="scope-bar"),
            html.H2("Group vs Industry Summary"),
            dcc.Graph(id="group-industry-table"),
            html.H2("Filtered Companies"),
            DataTable(
                id="company-table",
                columns=[
                    {"name": "Ticker", "id": "ticker"},
                    {"name": "Name", "id": "name"},
                    {"name": "Industry / Sector", "id": "anzsic_division"},
                    {"name": "Scope 1 (kgCO2e)", "id": "scope_1", "type": "numeric"},
                    {"name": "Scope 2 (kgCO2e)", "id": "scope_2", "type": "numeric"},
                    {"name": "Revenue (MM AUD)", "id": "revenue_mm", "type": "numeric"},
                    {
                        "name": "Net Income (MM AUD)",
                        "id": "net_income_mm",
                        "type": "numeric",
                    },
                    {"name": "EBITDA (MM AUD)", "id": "ebitda_mm", "type": "numeric"},
                    {
                        "name": "Total Assets (MM AUD)",
                        "id": "assets_mm",
                        "type": "numeric",
                    },
                    {"name": "Employees", "id": "employees", "type": "numeric"},
                    {"name": "Reporting Group", "id": "reporting_group"},
                    {"name": "Company State", "id": "company_state"},
                    {"name": "Company Region", "id": "company_region"},
                    {"name": "Company Country", "id": "company_country"},
                    {"name": "RBICS Sector", "id": "rbics_sector"},
                    {"name": "RBICS Sub-Sector", "id": "rbics_sub_sector"},
                    {"name": "RBICS Industry Group", "id": "rbics_industry_group"},
                    {"name": "RBICS Industry", "id": "rbics_industry"},
                    {"name": "Analysis Method", "id": "analysis_method"},
                ],
                data=initial_df.to_dict("records") if not initial_df.empty else [],
                page_size=10,
                filter_action="native",
                sort_action="native",
                style_table={"overflowX": "auto"},
            ),
        ],
        className="insights-tab",
    )

    verification_tab = html.Div(
        [
            dcc.Store(id="verification-current-key"),
            html.Div(
                [
                    html.Label("Analysis Method"),
                    dcc.Dropdown(
                        id="verification-method-filter",
                        options=[
                            {"label": value, "value": value} for value in method_list
                        ],
                        multi=True,
                        placeholder="Filter by analysis method",
                        value=method_list if method_list else None,
                    ),
                ],
                className="verification-filter",
            ),
            html.Div(
                [
                    html.Div(
                        [
                            html.Div(id="verification-summary", className="verification-summary"),
                            html.Div(
                                [
                                    html.H4("Snippet"),
                                    html.Pre(
                                        id="verification-snippet",
                                        className="verification-snippet-text",
                                    ),
                                ],
                                className="verification-snippet",
                            ),
                            html.Div(
                                [
                                    html.Label("Replacement PDF URL"),
                                    dcc.Input(
                                        id="verification-new-url",
                                        type="url",
                                        debounce=True,
                                        placeholder="https://example.com/report.pdf",
                                        style={"width": "100%"},
                                    ),
                                    html.Label("Or upload replacement PDF"),
                                    dcc.Upload(
                                        id="verification-upload",
                                        multiple=False,
                                        accept=".pdf,application/pdf",
                                        children=html.Div(
                                            [
                                                "Drag and drop or click to select a PDF"
                                            ]
                                        ),
                                        className="verification-upload",
                                    ),
                                ],
                                className="verification-reset-inputs",
                            ),
                            html.Div(
                                [
                                    html.Div(
                                        [
                                            html.Label("Scope 1 override (kgCO2e)"),
                                            dcc.Input(
                                                id="override-scope1",
                                                type="number",
                                                debounce=True,
                                            ),
                                        ],
                                        className="override-field",
                                    ),
                                    html.Div(
                                        [
                                            html.Label("Scope 2 override (kgCO2e)"),
                                            dcc.Input(
                                                id="override-scope2",
                                                type="number",
                                                debounce=True,
                                            ),
                                        ],
                                        className="override-field",
                                    ),
                                    html.Div(
                                        [
                                            html.Label("Scope 3 override (kgCO2e)"),
                                            dcc.Input(
                                                id="override-scope3",
                                                type="number",
                                                debounce=True,
                                            ),
                                        ],
                                        className="override-field",
                                    ),
                                ],
                                className="verification-overrides",
                            ),
                            html.Div(
                                [
                                    html.Label("Notes"),
                                    dcc.Textarea(
                                        id="verification-notes",
                                        style={"width": "100%", "height": "120px"},
                                    ),
                                ],
                                className="verification-notes",
                            ),
                        ],
                        className="verification-pane verification-left",
                    ),
                    html.Div(
                        id="verification-images",
                        className="verification-pane verification-right",
                    ),
                ],
                className="verification-content",
            ),
            html.Div(
                [
                    html.Button(
                        "Accept",
                        id="verify-accept-btn",
                        n_clicks=0,
                        className="primary",
                    ),
                    html.Button(
                        "Reject",
                        id="verify-reject-btn",
                        n_clicks=0,
                        className="danger",
                    ),
                    html.Button(
                        "Save Corrections",
                        id="verify-save-btn",
                        n_clicks=0,
                        className="primary",
                    ),
                    html.Button(
                        "Skip",
                        id="verify-skip-btn",
                        n_clicks=0,
                        className="secondary",
                    ),
                ],
                className="verification-actions",
            ),
            html.Div(id="verification-feedback", className="verification-feedback"),
        ],
        className="verification-tab",
    )

    app.layout = html.Div(
        [
            dcc.Store(id="companies-store", data=serialise_companies(companies)),
            html.H1("Scope Spider Dashboard"),
            dcc.Tabs(
                id="main-tabs",
                value="insights",
                children=[
                    dcc.Tab(label="Insights", value="insights", children=insights_tab),
                    dcc.Tab(
                        label="Verification",
                        value="verification",
                        children=verification_tab,
                    ),
                ],
            ),
        ],
        className="container",
    )

    @app.callback(
        Output("scatter-emissions-net-income", "figure"),
        Output("scatter-emissions-revenue", "figure"),
        Output("scatter-emissions-ebitda", "figure"),
        Output("scatter-emissions-assets", "figure"),
        Output("bar-top-revenue", "figure"),
        Output("scope-bar", "figure"),
        Output("group-industry-table", "figure"),
        Output("company-table", "data"),
        Input("companies-store", "data"),
        Input("industry-filter", "value"),
        Input("rbics-filter", "value"),
        Input("state-filter", "value"),
        Input("scope1-slider", "value"),
        Input("netincome-slider", "value"),
        Input("revenue-slider", "value"),
    )
    def update_visuals(
        store_data: Optional[List[Dict[str, Any]]],
        industries_selected: Optional[List[str]],
        rbics_selected: Optional[List[str]],
        states_selected: Optional[List[str]],
        scope1_range: List[float],
        net_income_range: List[float],
        revenue_range: List[float],
    ):
        def empty_response(message: str):
            empty_scatter = px.scatter(title=message)
            empty_bar = px.bar(title="")
            return (
                empty_scatter,
                empty_scatter,
                empty_scatter,
                empty_scatter,
                empty_bar,
                empty_bar,
                empty_bar,
                [],
            )

        companies_current = deserialise_companies(store_data)
        if not companies_current:
            return empty_response("No data available.")

        df = companies_to_dataframe(companies_current)
        if df.empty:
            return empty_response("No data available.")

        filtered = cast(pd.DataFrame, df.copy())
        if industries_selected:
            filtered = cast(
                pd.DataFrame,
                filtered[filtered["anzsic_division"].isin(industries_selected)],
            )
        if rbics_selected:
            filtered = cast(
                pd.DataFrame,
                filtered[filtered["rbics_sector"].isin(rbics_selected)],
            )
        if states_selected:
            filtered = cast(
                pd.DataFrame,
                filtered[filtered["company_state"].isin(states_selected)],
            )

        filtered = cast(
            pd.DataFrame,
            filtered.assign(
                scope_1_numeric=pd.to_numeric(filtered["scope_1"], errors="coerce"),
                scope_2_numeric=pd.to_numeric(filtered["scope_2"], errors="coerce"),
                revenue_numeric=pd.to_numeric(filtered["revenue_mm"], errors="coerce"),
                net_income_numeric=pd.to_numeric(
                    filtered["net_income_mm"], errors="coerce"
                ),
                ebitda_numeric=pd.to_numeric(filtered["ebitda_mm"], errors="coerce"),
                assets_numeric=pd.to_numeric(filtered["assets_mm"], errors="coerce"),
            ),
        )

        s_min, s_max = scope1_range
        n_min, n_max = net_income_range
        r_min, r_max = revenue_range

        scope1_series = filtered["scope_1_numeric"]
        net_series = filtered["net_income_numeric"]
        revenue_series = filtered["revenue_numeric"]

        mask_scope1 = (
            scope1_series.between(s_min, s_max, inclusive="both") | scope1_series.isna()
        )
        mask_net = (
            net_series.between(n_min, n_max, inclusive="both") | net_series.isna()
        )
        mask_rev = (
            revenue_series.between(r_min, r_max, inclusive="both")
            | revenue_series.isna()
        )

        filtered = filtered[mask_scope1 & mask_net & mask_rev]

        if filtered.empty:
            return empty_response("No data matches the current filters.")

        size_series = filtered["revenue_numeric"].fillna(1.0)

        def build_scatter(
            metric_column: str,
            metric_label: str,
            title: str,
        ):
            metric_frame = filtered[
                [
                    "scope_1_numeric",
                    metric_column,
                    "anzsic_division",
                    "name",
                ]
            ].copy()
            metric_frame[metric_column] = pd.to_numeric(
                metric_frame[metric_column], errors="coerce"
            )
            available = metric_frame.dropna(subset=["scope_1_numeric", metric_column])
            if available.empty:
                fig = px.scatter(title=f"{title} (insufficient data)")
                fig.update_layout(
                    xaxis_title="Scope 1 (kgCO2e)",
                    yaxis_title=metric_label,
                )
                return fig
            fig = px.scatter(
                metric_frame,
                x="scope_1_numeric",
                y=metric_column,
                color="anzsic_division",
                size=size_series,
                size_max=60,
                hover_name="name",
                title=title,
                labels={
                    "scope_1_numeric": "Scope 1 (kgCO2e)",
                    metric_column: metric_label,
                    "anzsic_division": "Industry / Sector",
                },
            )
            return fig

        net_income_fig = build_scatter(
            "net_income_numeric",
            "Net Income (MM AUD)",
            "Scope 1 vs Net Income",
        )
        revenue_fig = build_scatter(
            "revenue_numeric",
            "Revenue (MM AUD)",
            "Scope 1 vs Revenue",
        )
        ebitda_fig = build_scatter(
            "ebitda_numeric",
            "EBITDA (MM AUD)",
            "Scope 1 vs EBITDA",
        )
        assets_fig = build_scatter(
            "assets_numeric",
            "Total Assets (MM AUD)",
            "Scope 1 vs Total Assets",
        )

        top_revenue = cast(
            pd.DataFrame,
            filtered.sort_values(by="revenue_numeric", ascending=False).head(10),
        )

        bar_revenue_fig = px.bar(
            top_revenue,
            x="name",
            y="revenue_numeric",
            color="anzsic_division",
            title="Top 10 Companies by Revenue",
            labels={
                "revenue_numeric": "Revenue (MM AUD)",
                "anzsic_division": "Industry / Sector",
            },
        )

        scope_avgs = cast(
            pd.DataFrame,
            filtered.groupby("anzsic_division")[
                ["scope_1_numeric", "scope_2_numeric"]
            ].mean(numeric_only=True),
        ).reset_index()
        scope_avgs = scope_avgs.melt(
            id_vars="anzsic_division",
            value_vars=["scope_1_numeric", "scope_2_numeric"],
            var_name="Scope",
            value_name="kgCO2e",
        )
        scope_avgs["Scope"] = scope_avgs["Scope"].replace(
            {"scope_1_numeric": "Scope 1", "scope_2_numeric": "Scope 2"}
        )
        scope_fig = px.bar(
            scope_avgs,
            x="anzsic_division",
            y="kgCO2e",
            color="Scope",
            title="Average Scope 1 & 2 Emissions by Industry / Sector",
            labels={"anzsic_division": "Industry / Sector", "kgCO2e": "kgCO2e"},
        )

        table_cols = [
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
        table_df = cast(
            pd.DataFrame,
            filtered[table_cols + ["net_income_numeric"]].copy(),
        )
        table_df = table_df.sort_values(by="net_income_numeric", ascending=False)
        table_df = table_df.drop(columns=["net_income_numeric"])
        table_df = table_df.fillna("")

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
                values="scope_1_numeric",
                aggfunc="sum",
                fill_value=0.0,
            )
            if not group_data.empty
            else pd.DataFrame()
        )

        if pivot_counts.empty or pivot_emissions.empty:
            matrix_fig = go.Figure()
            matrix_fig.update_layout(
                title="Group vs Industry (no data)",
                xaxis=dict(visible=False),
                yaxis=dict(visible=False),
                annotations=[
                    dict(
                        text="No data matches the current filters.",
                        showarrow=False,
                        xref="paper",
                        yref="paper",
                        x=0.5,
                        y=0.5,
                    )
                ],
            )
        else:
            z_counts = pivot_counts.reindex(sorted(pivot_counts.index)).reindex(
                sorted(pivot_counts.columns), axis=1
            )
            z_emissions = pivot_emissions.reindex(sorted(pivot_emissions.index)).reindex(
                sorted(pivot_emissions.columns), axis=1
            )
            annotations = []
            for i, group in enumerate(z_counts.index):
                for j, division in enumerate(z_counts.columns):
                    count = z_counts.iloc[i, j]
                    emissions_total = z_emissions.iloc[i, j]
                    text = f"{int(count)}<br>{emissions_total:,.0f} kg"
                    annotations.append(
                        dict(
                            x=j,
                            y=i,
                            text=text,
                            showarrow=False,
                            font=dict(color="white" if count > 0 else "black"),
                        )
                    )
            matrix_fig = go.Figure(
                data=go.Heatmap(
                    z=z_counts.values,
                    x=z_counts.columns.tolist(),
                    y=z_counts.index.tolist(),
                    colorscale="Blues",
                    colorbar=dict(title="Company count"),
                )
            )
            matrix_fig.update_layout(
                title="Companies & Scope 1 Emissions by Reporting Group / Industry",
                xaxis_title="ANZSIC Division",
                yaxis_title="Reporting Group",
                annotations=annotations,
            )

        return (
            net_income_fig,
            revenue_fig,
            ebitda_fig,
            assets_fig,
            bar_revenue_fig,
            scope_fig,
            matrix_fig,
            table_df.to_dict("records"),
        )

    @app.callback(
        Output("verification-method-filter", "options"),
        Output("verification-method-filter", "value"),
        Input("companies-store", "data"),
        State("verification-method-filter", "value"),
    )
    def refresh_method_filter(
        store_data: Optional[List[Dict[str, Any]]],
        selected_methods: Optional[List[str]],
    ) -> tuple[list[Dict[str, str]], Optional[List[str]]]:
        companies_current = deserialise_companies(store_data)
        methods = sorted(
            {
                company.analysis_record.method
                for company in companies_current
                if company.analysis_record and company.analysis_record.method
            }
        )
        options = [{"label": value, "value": value} for value in methods]
        if not methods:
            return options, None
        if selected_methods:
            filtered_selection = [value for value in selected_methods if value in methods]
            if filtered_selection:
                return options, filtered_selection
        return options, methods

    @app.callback(
        Output("verification-current-key", "data"),
        Input("companies-store", "data"),
        Input("verification-method-filter", "value"),
        State("verification-current-key", "data"),
    )
    def ensure_verification_key(
        store_data: Optional[List[Dict[str, Any]]],
        selected_methods: Optional[List[str]],
        current_key: Optional[str],
    ) -> Optional[str]:
        companies_current = deserialise_companies(store_data)
        if not companies_current:
            return None
        allowed_methods = (
            {str(value) for value in selected_methods if value}
            if selected_methods
            else None
        )
        return next_pending_key(
            companies_current, current_key, allowed_methods=allowed_methods
        )

    @app.callback(
        Output("verification-summary", "children"),
        Output("verification-snippet", "children"),
        Output("verification-images", "children"),
        Output("override-scope1", "value"),
        Output("override-scope2", "value"),
        Output("override-scope3", "value"),
        Output("verification-notes", "value"),
        Input("verification-current-key", "data"),
        Input("companies-store", "data"),
    )
    def update_verification_view(
        current_key: Optional[str],
        store_data: Optional[List[Dict[str, Any]]],
    ):
        companies_current = deserialise_companies(store_data)
        if not companies_current:
            return (
                html.Div("No companies available."),
                "",
                [html.P("No previews available.")],
                None,
                None,
                None,
                "",
            )

        if not current_key:
            return (
                html.Div("All companies are verified."),
                "",
                [html.P("No pending companies.")],
                None,
                None,
                None,
                "",
            )

        target = next(
            (
                company
                for company in companies_current
                if company_key(company) == current_key
            ),
            None,
        )
        if target is None:
            return (
                html.Div("Company not found."),
                "",
                [html.P("No previews available.")],
                None,
                None,
                None,
                "",
            )

        identity = target.identity
        annotations = target.annotations
        emissions = target.emissions
        verification = target.verification
        analysis = target.analysis_record

        scope1 = emissions.scope_1.value if emissions.scope_1 else None
        scope2 = emissions.scope_2.value if emissions.scope_2 else None
        scope3 = emissions.scope_3.value if emissions.scope_3 else None
        scope1_conf = emissions.scope_1.confidence if emissions.scope_1 else None
        scope2_conf = emissions.scope_2.confidence if emissions.scope_2 else None

        if analysis is not None:
            method = analysis.method or "unknown"
            confidence = analysis.confidence
            snippet_label = analysis.snippet_label or ""
        else:
            method = "unknown"
            confidence = None
            snippet_label = ""

        verified_at = (
            verification.verified_at.isoformat(timespec="seconds")
            if verification and verification.verified_at
            else None
        )

        summary = html.Div(
            [
                html.H3(identity.ticker or identity.name or "Unknown"),
                html.P(f"Status: {verification.status if verification else 'pending'}"),
                html.P(f"Verified at: {verified_at or '—'}"),
                html.P(f"Reporting group: {annotations.reporting_group or '—'}"),
                html.P(
                    f"Location: {annotations.company_state or annotations.company_region or annotations.company_country or '—'}"
                ),
                html.P(
                    f"Scope 1: {scope1 if scope1 is not None else '—'} "
                    f"(conf={scope1_conf if scope1_conf is not None else '—'})"
                ),
                html.P(
                    f"Scope 2: {scope2 if scope2 is not None else '—'} "
                    f"(conf={scope2_conf if scope2_conf is not None else '—'})"
                ),
                html.P(f"Scope 3: {scope3 if scope3 is not None else '—'}"),
                html.P(f"Analysis method: {method}"),
                html.P(
                    f"Analysis confidence: {confidence:.2f}"
                    if confidence is not None
                    else "Analysis confidence: —"
                ),
                html.P(f"Snippet label: {snippet_label or '—'}"),
            ],
            className="verification-summary",
        )

        snippet_path = analysis.snippet_path if analysis else None
        snippet_text = read_snippet_text(snippet_path)
        snippet_display = snippet_text or "No snippet available for this analysis."

        image_children: List[html.Div] = []
        if (
            analysis
            and analysis.snippet_pages
            and target.download_record
            and target.download_record.pdf_path
        ):
            pdf_path = Path(target.download_record.pdf_path)
            previews = previews_as_data_urls(pdf_path, analysis.snippet_pages)
            if previews:
                for page, data_url in previews:
                    image_children.append(
                        html.Div(
                            [
                                html.P(f"Page {page}"),
                                html.Img(
                                    src=data_url,
                                    style={
                                        "maxWidth": "100%",
                                        "border": "1px solid #ccc",
                                    },
                                ),
                            ],
                            className="verification-image",
                        )
                    )
        if not image_children:
            image_children = [html.P("No page previews available.")]

        return (
            summary,
            snippet_display,
            image_children,
            verification.scope_1_override,
            verification.scope_2_override,
            verification.scope_3_override,
            verification.notes or "",
        )

    @app.callback(
        Output("companies-store", "data"),
        Output("verification-feedback", "children"),
        Output("verification-new-url", "value"),
        Output("verification-upload", "contents"),
        Output("verification-upload", "filename"),
        Output("verification-current-key", "data", allow_duplicate=True),
        Input("verify-accept-btn", "n_clicks"),
        Input("verify-reject-btn", "n_clicks"),
        Input("verify-save-btn", "n_clicks"),
        Input("verify-skip-btn", "n_clicks"),
        State("verification-current-key", "data"),
        State("override-scope1", "value"),
        State("override-scope2", "value"),
        State("override-scope3", "value"),
        State("verification-notes", "value"),
        State("verification-new-url", "value"),
        State("verification-upload", "contents"),
        State("verification-upload", "filename"),
        State("verification-method-filter", "value"),
        State("companies-store", "data"),
        prevent_initial_call=True,
    )
    def handle_verification_actions(
        accept_clicks: int,
        reject_clicks: int,
        save_clicks: int,
        skip_clicks: int,
        current_key: Optional[str],
        override_scope1: Optional[float],
        override_scope2: Optional[float],
        override_scope3: Optional[float],
        notes: Optional[str],
        new_url: Optional[str],
        upload_contents: Optional[str],
        upload_filename: Optional[str],
        selected_methods: Optional[List[str]],
        store_data: Optional[List[Dict[str, Any]]],
    ):
        ctx = dash.callback_context
        if not ctx.triggered or store_data is None:
            raise PreventUpdate

        triggered = ctx.triggered[0]["prop_id"].split(".")[0]
        companies_current = deserialise_companies(store_data)
        if not companies_current:
            raise PreventUpdate

        allowed_methods = (
            {str(value) for value in selected_methods if value}
            if selected_methods
            else None
        )

        if triggered == "verify-skip-btn":
            next_key = next_pending_key(
                companies_current,
                current_key,
                skip_current=True,
                allowed_methods=allowed_methods,
            )
            message = (
                "Skipped current company."
                if current_key is not None
                else "No company selected to skip."
            )
            return (
                store_data,
                html.Div(message),
                no_update,
                no_update,
                no_update,
                next_key,
            )

        if current_key is None:
            raise PreventUpdate

        target = next(
            (
                company
                for company in companies_current
                if company_key(company) == current_key
            ),
            None,
        )
        if target is None:
            next_key = next_pending_key(
                companies_current,
                current_key,
                skip_current=True,
                allowed_methods=allowed_methods,
            )
            return (
                store_data,
                html.Div("Company not found."),
                no_update,
                no_update,
                no_update,
                next_key,
            )

        verification = target.verification
        now = datetime.utcnow()
        message = ""
        skip_current = False
        url_value = (new_url or "").strip()
        upload_data = upload_contents
        upload_name = upload_filename

        if triggered == "verify-accept-btn":
            verification.status = "accepted"
            verification.verified_at = now
            verification.notes = notes or None
            message = "Verification accepted."
        elif triggered == "verify-reject-btn":
            stages_requested: List[str] = []
            sanitized_url: Optional[str] = None
            upload_bytes: Optional[bytes] = None

            if url_value:
                candidate_url, is_pdf = normalise_pdf_url(url_value)
                parsed = urlparse(candidate_url)
                if (
                    not candidate_url
                    or not is_pdf
                    or parsed.scheme not in ("http", "https")
                    or not parsed.netloc
                ):
                    return (
                        store_data,
                        html.Div("Provide a valid PDF URL (http/https ending with .pdf) before rejecting."),
                        url_value,
                        upload_data,
                        upload_name,
                        current_key,
                    )
                sanitized_url = candidate_url
                stages_requested.append("s2")

            if upload_data:
                try:
                    upload_bytes = _decode_uploaded_pdf(upload_data)
                except ValueError as exc:
                    return (
                        store_data,
                        html.Div(str(exc)),
                        url_value,
                        upload_data,
                        upload_name,
                        current_key,
                    )
                if "s2" not in stages_requested:
                    stages_requested.append("s3")

            if not stages_requested:
                return (
                    store_data,
                    html.Div("Provide a replacement PDF URL or upload a new PDF before rejecting."),
                    url_value,
                    upload_data,
                    upload_name,
                    current_key,
                )

            reset_company_stages(target, stages_requested)
            verification = target.verification

            if sanitized_url:
                filename = derive_filename(
                    sanitized_url,
                    target.search_record.filename if target.search_record else "",
                )
                title = target.identity.name or filename
                inferred_year = infer_year_from_text(title, filename, sanitized_url)
                doc_type = classify_document_type(title, filename, sanitized_url)
                target.search_record = SearchRecord(
                    url=sanitized_url,
                    title=title,
                    filename=filename,
                    year=inferred_year,
                    doc_type=doc_type,
                )

            if upload_bytes is not None:
                stored_path = _store_uploaded_pdf(
                    companies_path.parent,
                    target,
                    upload_name,
                    upload_bytes,
                )
                target.download_record = DownloadRecord(pdf_path=str(stored_path))

            ordered = _ordered_stage_dependencies(stages_requested)
            stage_display = ", ".join(stage.upper() for stage in ordered)
            verification.status = "rejected"
            verification.verified_at = now
            verification.notes = notes or None
            message = (
                f"Verification rejected. Pipeline reset: {stage_display}."
                if stage_display
                else "Verification rejected."
            )
            skip_current = True
        elif triggered == "verify-save-btn":
            if override_scope1 is None or override_scope2 is None:
                return (
                    store_data,
                    html.Div("Provide Scope 1 and Scope 2 overrides to save corrections."),
                    url_value,
                    upload_data,
                    upload_name,
                    current_key,
                )
            scope1_int = int(override_scope1)
            scope2_int = int(override_scope2)
            scope3_int = int(override_scope3) if override_scope3 is not None else None

            if target.emissions.scope_1 is None:
                target.emissions.scope_1 = ScopeValue(value=scope1_int, confidence=1.0)
            else:
                target.emissions.scope_1.value = scope1_int
                target.emissions.scope_1.confidence = 1.0

            if target.emissions.scope_2 is None:
                target.emissions.scope_2 = Scope2Emissions(
                    value=scope2_int,
                    confidence=1.0,
                    method="manual",
                )
            else:
                target.emissions.scope_2.value = scope2_int
                target.emissions.scope_2.confidence = 1.0
                target.emissions.scope_2.method = (
                    target.emissions.scope_2.method or "manual"
                )

            if scope3_int is not None:
                if target.emissions.scope_3 is None:
                    target.emissions.scope_3 = Scope3Emissions(
                        value=scope3_int,
                        confidence=1.0,
                    )
                else:
                    target.emissions.scope_3.value = scope3_int
                    target.emissions.scope_3.confidence = 1.0
            elif target.emissions.scope_3 is not None:
                target.emissions.scope_3.confidence = (
                    target.emissions.scope_3.confidence or 1.0
                )

            previous_record = target.analysis_record
            target.analysis_record = AnalysisRecord(
                method="manual-override",
                snippet_label=(
                    previous_record.snippet_label if previous_record else "manual"
                ),
                snippet_path=(
                    previous_record.snippet_path if previous_record else None
                ),
                snippet_pages=(
                    previous_record.snippet_pages if previous_record else []
                ),
                confidence=1.0,
            )

            verification.status = "accepted"
            verification.verified_at = now
            verification.scope_1_override = scope1_int
            verification.scope_2_override = scope2_int
            verification.scope_3_override = scope3_int
            verification.notes = notes or None
            message = "Manual corrections saved and accepted."
        else:
            raise PreventUpdate

        new_store = serialise_companies(companies_current)
        dump_companies(companies_path, payload, companies_current)

        next_key = next_pending_key(
            companies_current,
            current_key,
            skip_current=skip_current,
            allowed_methods=allowed_methods,
        )

        return (
            new_store,
            html.Div(message),
            "",
            None,
            None,
            next_key,
        )

    return app


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    companies_path = Path(args.companies).expanduser().resolve()
    if not companies_path.exists():
        print(f"ERROR: companies file not found ({companies_path})", file=sys.stderr)
        return 1

    try:
        companies, payload = load_companies(companies_path)
    except ValueError as exc:
        print(f"ERROR: failed to load data ({exc})", file=sys.stderr)
        return 1

    app = create_dash_app(companies_path, companies, payload)
    app.run(host=args.host, port=args.port, debug=args.debug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
