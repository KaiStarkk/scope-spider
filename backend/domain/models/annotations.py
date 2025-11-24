from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Annotations(BaseModel):
    model_config = ConfigDict(extra="ignore")

    anzsic_division: Optional[str] = Field(
        default=None, description="Primary ANZSIC division (default: gpt-4o-mini)."
    )
    anzsic_confidence: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Confidence score (0-1) reported by the primary classifier.",
    )
    anzsic_context: Optional[str] = Field(
        default=None,
        description="Supporting sentence or excerpt returned by the primary classifier.",
    )
    anzsic_source: Optional[str] = Field(
        default="gpt-4o-mini", description="Identifier for the primary classifier used."
    )
    anzsic_local_division: Optional[str] = Field(
        default=None,
        description="ANZSIC division suggested by the optional local LLM (if any).",
    )
    anzsic_local_confidence: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Confidence reported by the local LLM (if any).",
    )
    anzsic_local_context: Optional[str] = Field(
        default=None,
        description="Supporting sentence or excerpt from the local LLM.",
    )
    anzsic_agreement: Optional[bool] = Field(
        default=None,
        description="Whether primary and local classifiers agreed on the division.",
    )
    profitability_year: Optional[int] = Field(
        default=None,
        description="Financial year associated with profitability metrics.",
    )
    profitability_revenue_mm_aud: Optional[float] = Field(
        default=None, description="Revenue in AUD millions."
    )
    profitability_ebitda_mm_aud: Optional[float] = Field(
        default=None, description="EBITDA in AUD millions."
    )
    profitability_net_income_mm_aud: Optional[float] = Field(
        default=None, description="Net income in AUD millions."
    )
    profitability_total_assets_mm_aud: Optional[float] = Field(
        default=None, description="Total assets in AUD millions."
    )
    profitability_ratio: Optional[float] = Field(
        default=None, description="Net income divided by revenue (unitless)."
    )
    size_employee_count: Optional[int] = Field(
        default=None, description="Employee headcount from external dataset."
    )
    reporting_group: Optional[str] = Field(
        default=None,
        description="Regulatory reporting group (Group 1, Group 2, Group 3, or None).",
    )
    rbics_sector: Optional[str] = Field(
        default=None, description="Primary FactSet RBICS sector classification."
    )
    rbics_sub_sector: Optional[str] = Field(
        default=None, description="Primary FactSet RBICS sub-sector classification."
    )
    rbics_industry_group: Optional[str] = Field(
        default=None, description="Primary FactSet RBICS industry group."
    )
    rbics_industry: Optional[str] = Field(
        default=None, description="Primary FactSet RBICS industry."
    )
    company_country: Optional[str] = Field(
        default=None, description="Company country from the external dataset."
    )
    company_region: Optional[str] = Field(
        default=None, description="Company region from the external dataset."
    )
    company_state: Optional[str] = Field(
        default=None, description="Company state from the external dataset."
    )
    net_zero_claims: Optional[int] = Field(
        default=None,
        description="Count of 'net zero' phrase occurrences found in the company's PDF document.",
    )
    reputational_concern_ratio: Optional[float] = Field(
        default=None,
        description="Net zero mentions per AUD million of revenue.",
    )
    profitability_emissions_ratio: Optional[float] = Field(
        default=None,
        description="Profitability ratio divided by scope 1+2 emissions.",
    )

    @model_validator(mode="before")
    @classmethod
    def _migrate_old_fields(cls, value: Any) -> Any:
        if not isinstance(value, Dict):
            return value
        migrated = dict(value)
        if (
            "anzsic_validation_division" in migrated
            and "anzsic_local_division" not in migrated
        ):
            migrated["anzsic_local_division"] = migrated.pop(
                "anzsic_validation_division"
            )
        if (
            "anzsic_validation_confidence" in migrated
            and "anzsic_local_confidence" not in migrated
        ):
            migrated["anzsic_local_confidence"] = migrated.pop(
                "anzsic_validation_confidence"
            )
        if (
            "anzsic_validation_context" in migrated
            and "anzsic_local_context" not in migrated
        ):
            migrated["anzsic_local_context"] = migrated.pop("anzsic_validation_context")
        return migrated


__all__ = ["Annotations"]
