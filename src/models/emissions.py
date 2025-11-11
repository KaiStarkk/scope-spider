from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class Scope2Emissions(BaseModel):
    value: int = Field(
        description="Total Scope 2 emissions in kgCO2e (rounded to nearest whole number)."
    )
    method: Optional[str] = Field(
        default=None,
        description="Reporting method for Scope 2 emissions (market, location, or unsure).",
    )

    @field_validator("method", mode="before")
    @classmethod
    def _normalise_method(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalised = str(value).strip().lower()
        mapping = {
            "market": "market",
            "location": "location",
            "locational": "location",
            "unsure": "unsure",
            "unknown": "unsure",
        }
        if normalised not in mapping:
            raise ValueError("scope_2.method must be one of: market, location, unsure")
        return mapping[normalised]


class Scope3Emissions(BaseModel):
    value: int = Field(
        description="Total Scope 3 emissions in kgCO2e (rounded to nearest whole number)."
    )
    qualifiers: Optional[str] = Field(
        default=None,
        description="Free-text qualifiers or caveats describing Scope 3 coverage.",
    )

    @field_validator("qualifiers", mode="before")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return str(value).strip()


class EmissionsData(BaseModel):
    scope_1: Optional[int] = Field(
        default=None,
        description="Total Scope 1 emissions in kgCO2e.",
    )
    scope_2: Optional[Scope2Emissions] = Field(
        default=None,
        description="Scope 2 emissions with reporting method if available.",
    )
    scope_3: Optional[Scope3Emissions] = Field(
        default=None,
        description="Scope 3 emissions with optional qualifier text.",
    )

    @model_validator(mode="before")
    @classmethod
    def _coerce_structure(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(value, dict):
            return value
        coerced = dict(value)
        scope1 = coerced.get("scope_1")
        if scope1 is not None and not isinstance(scope1, int):
            try:
                coerced["scope_1"] = int(scope1)
            except (TypeError, ValueError):
                pass

        scope2 = coerced.get("scope_2")
        if isinstance(scope2, (int, float)):
            coerced["scope_2"] = {"value": int(scope2)}
        elif isinstance(scope2, dict):
            scope2_value = scope2.get("value")
            if scope2_value is not None and not isinstance(scope2_value, int):
                try:
                    scope2["value"] = int(scope2_value)
                except (TypeError, ValueError):
                    pass

        scope3 = coerced.get("scope_3")
        if isinstance(scope3, (int, float)):
            coerced["scope_3"] = {"value": int(scope3)}
        elif isinstance(scope3, dict):
            scope3_value = scope3.get("value")
            if scope3_value is not None and not isinstance(scope3_value, int):
                try:
                    scope3["value"] = int(scope3_value)
                except (TypeError, ValueError):
                    pass
            qualifiers = scope3.get("qualifiers")
            if qualifiers is not None:
                scope3["qualifiers"] = str(qualifiers).strip()
        return coerced


__all__ = ["Scope2Emissions", "Scope3Emissions", "EmissionsData"]
