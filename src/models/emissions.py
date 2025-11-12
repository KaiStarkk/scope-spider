import re
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class ScopeValue(BaseModel):
    value: int = Field(
        description="Total emissions in kgCO2e (rounded to nearest whole number)."
    )
    confidence: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Confidence score (0-1) returned by the analysis method.",
    )
    context: Optional[str] = Field(
        default=None,
        description="Source sentence or excerpt that supports the extracted value.",
    )


class Scope2Emissions(ScopeValue):
    method: Optional[str] = Field(
        default=None,
        description="Reporting method for Scope 2 emissions (market, location, or unsure).",
    )

    @field_validator("method", mode="before")
    @classmethod
    def _normalise_method(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        normalised = text.lower()
        condensed = re.sub(r"\s+", " ", normalised.replace("-", " ").replace("_", " ")).strip()
        mapping = {
            "market": "market",
            "location": "location",
            "locational": "location",
            "unsure": "unsure",
            "unknown": "unsure",
            "not specified": "unsure",
            "not applicable": "unsure",
            "not reported": "unsure",
            "n/a": "unsure",
        }
        if condensed.endswith(" based"):
            condensed = condensed[: -len(" based")].strip()
        if condensed in mapping:
            return mapping[condensed]
        has_market = "market" in condensed
        has_location = "location" in condensed or "locational" in condensed
        if has_market and has_location:
            return "unsure"
        if has_market:
            return "market"
        if has_location:
            return "location"
        if any(
            keyword in condensed
            for keyword in ("unknown", "unsure", "uncertain", "n/a", "not specified")
        ):
            return "unsure"
        raise ValueError("scope_2.method must be one of: market, location, unsure")


class Scope3Emissions(ScopeValue):
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
    scope_1: Optional[ScopeValue] = Field(
        default=None,
        description="Total Scope 1 emissions in kgCO2e with context metadata.",
    )
    scope_2: Optional[Scope2Emissions] = Field(
        default=None,
        description="Scope 2 emissions with reporting method and context metadata.",
    )
    scope_3: Optional[Scope3Emissions] = Field(
        default=None,
        description="Scope 3 emissions with optional qualifier and context metadata.",
    )

    @model_validator(mode="before")
    @classmethod
    def _coerce_structure(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(value, dict):
            return value
        coerced = dict(value)
        cls._normalise_scope_value(coerced, "scope_1")
        cls._normalise_scope_value(coerced, "scope_2")
        cls._normalise_scope_value(coerced, "scope_3")
        scope3 = coerced.get("scope_3")
        if isinstance(scope3, dict):
            qualifiers = scope3.get("qualifiers")
            if qualifiers is not None:
                scope3["qualifiers"] = str(qualifiers).strip()
        return coerced

    @staticmethod
    def _normalise_scope_value(data: Dict[str, Any], key: str) -> None:
        value = data.get(key)
        if value is None:
            return

        if isinstance(value, (int, float)):
            data[key] = {"value": int(value)}
            return

        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                data.pop(key, None)
                return
            try:
                data[key] = {"value": int(stripped)}
            except ValueError:
                pass
            return

        if isinstance(value, dict):
            raw = value.get("value")
            if raw is not None and not isinstance(raw, int):
                try:
                    value["value"] = int(raw)
                except (TypeError, ValueError):
                    pass


__all__ = ["ScopeValue", "Scope2Emissions", "Scope3Emissions", "EmissionsData"]
