from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional, cast

import plotly.express as px
from dash import Dash, Input, Output, dcc, html
from dash.dash_table import DataTable

import pandas as pd
from src.utils.companies import load_companies


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="s7_dashboard",
        description="Interactive dashboard for emissions and profitability insights.",
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


def load_dataframe(companies_path: Path) -> pd.DataFrame:
    companies, _ = load_companies(companies_path)
    rows = []
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
                "assets_mm": annotations.profitability_total_assets_mm_aud,
                "year": annotations.profitability_year,
            }
        )

    if not rows:
        raise ValueError("No companies found in the dataset.")

    return pd.DataFrame(rows)


def create_dash_app(df: pd.DataFrame) -> Dash:
    app = Dash(__name__)

    industries = sorted([i for i in df["anzsic_division"].dropna().unique()])
    scope1_min = float(df["scope_1"].min(skipna=True) or 0)
    scope1_max = float(df["scope_1"].max(skipna=True) or 1)
    net_min = float(df["net_income_mm"].min(skipna=True) or 0)
    net_max = float(df["net_income_mm"].max(skipna=True) or 1)
    rev_min = float(df["revenue_mm"].min(skipna=True) or 0)
    rev_max = float(df["revenue_mm"].max(skipna=True) or 1)

    app.layout = html.Div(
        [
            html.H1("Scope Spider Insights Dashboard"),
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
                            html.Label("Scope 1 range (kgCO2e)"),
                            dcc.RangeSlider(
                                id="scope1-slider",
                                min=scope1_min,
                                max=scope1_max,
                                value=[scope1_min, scope1_max],
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
            dcc.Graph(id="scatter-emissions-profit"),
            dcc.Graph(id="bar-top-revenue"),
            dcc.Graph(id="scope-bar"),
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
                ],
                data=df.to_dict("records"),
                page_size=10,
                filter_action="native",
                sort_action="native",
                style_table={"overflowX": "auto"},
            ),
        ],
        className="container",
    )

    @app.callback(
        Output("scatter-emissions-profit", "figure"),
        Output("bar-top-revenue", "figure"),
        Output("scope-bar", "figure"),
        Output("company-table", "data"),
        Input("industry-filter", "value"),
        Input("scope1-slider", "value"),
        Input("netincome-slider", "value"),
        Input("revenue-slider", "value"),
    )
    def update_visuals(
        industries_selected: Optional[List[str]],
        scope1_range: List[float],
        net_income_range: List[float],
        revenue_range: List[float],
    ):
        filtered = df.copy()
        if industries_selected:
            filtered = filtered[filtered["anzsic_division"].isin(industries_selected)]

        filtered = filtered.assign(
            scope_1_numeric=pd.to_numeric(filtered["scope_1"], errors="coerce"),
            scope_2_numeric=pd.to_numeric(filtered["scope_2"], errors="coerce"),
            revenue_numeric=pd.to_numeric(filtered["revenue_mm"], errors="coerce"),
            net_income_numeric=pd.to_numeric(
                filtered["net_income_mm"], errors="coerce"
            ),
            ebitda_numeric=pd.to_numeric(filtered["ebitda_mm"], errors="coerce"),
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

        filtered = cast(pd.DataFrame, filtered[mask_scope1 & mask_net & mask_rev])

        if filtered.empty:  # type: ignore[attr-defined]
            empty_fig = px.scatter(title="No data matches the current filters.")
            return empty_fig, px.bar(title=""), px.bar(title=""), []

        scatter_fig = px.scatter(
            filtered,
            x="scope_1_numeric",
            y="net_income_numeric",
            color="anzsic_division",
            size=filtered["revenue_numeric"].fillna(1.0),
            size_max=60,
            hover_name="name",
            title="Scope 1 vs Net Income",
            labels={
                "scope_1_numeric": "Scope 1 (kgCO2e)",
                "net_income_numeric": "Net Income (MM AUD)",
                "anzsic_division": "Industry / Sector",
                "revenue_numeric": "Revenue (MM AUD)",
            },
        )

        top_revenue = cast(
            pd.DataFrame,
            filtered.sort_values(by="revenue_numeric", ascending=False),
        ).head(10)
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

        scope_avgs = (
            filtered.groupby("anzsic_division")[["scope_1_numeric", "scope_2_numeric"]]
            .mean(numeric_only=True)
            .reset_index()
        )
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
        ]
        table_df = cast(
            pd.DataFrame,
            filtered[table_cols + ["net_income_numeric"]],
        ).copy()
        table_df = table_df.sort_values(by="net_income_numeric", ascending=False)
        table_df = table_df.drop(columns=["net_income_numeric"])
        table_df = table_df.fillna("")
        return scatter_fig, bar_revenue_fig, scope_fig, table_df.to_dict("records")

    return app


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    companies_path = Path(args.companies).expanduser().resolve()
    if not companies_path.exists():
        print(f"ERROR: companies file not found ({companies_path})", file=sys.stderr)
        return 1

    try:
        df: pd.DataFrame = load_dataframe(companies_path)
    except ValueError as exc:
        print(f"ERROR: failed to load data ({exc})", file=sys.stderr)
        return 1

    app = create_dash_app(df)
    app.run(host=args.host, port=args.port, debug=args.debug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
