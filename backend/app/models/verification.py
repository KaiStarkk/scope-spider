from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class VerificationDecision(BaseModel):
    notes: Optional[str] = Field(default=None, description="Optional reviewer notes.")
    reviewer: Optional[str] = Field(
        default=None, description="Identifier for the reviewer making this decision."
    )


class VerificationReject(BaseModel):
    notes: Optional[str] = Field(default=None, description="Optional reviewer notes.")
    reviewer: Optional[str] = Field(
        default=None, description="Identifier for the reviewer recording the rejection."
    )
    replacement_url: Optional[str] = Field(
        default=None,
        description="Replacement PDF URL used to restart the pipeline.",
    )
    upload_contents: Optional[str] = Field(
        default=None,
        description="Data URL payload of an uploaded PDF to replace the existing document.",
    )
    upload_filename: Optional[str] = Field(
        default=None, description="Original filename of the uploaded PDF (if provided)."
    )


class VerificationOverride(BaseModel):
    scope_1: int = Field(..., ge=0, description="Manual override for Scope 1 (kgCO2e).")
    scope_2: int = Field(..., ge=0, description="Manual override for Scope 2 (kgCO2e).")
    scope_3: Optional[int] = Field(
        default=None, ge=0, description="Optional manual override for Scope 3 (kgCO2e)."
    )
    notes: Optional[str] = Field(default=None, description="Optional reviewer notes.")
    reviewer: Optional[str] = Field(
        default=None, description="Identifier for the reviewer saving the override."
    )
