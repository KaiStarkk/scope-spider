from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class VerificationRecord(BaseModel):
    status: Literal["pending", "accepted", "rejected"] = Field(
        default="pending",
        description="Current verification state for the analysed emissions data.",
    )
    reviewer: Optional[str] = Field(
        default=None,
        description="Identifier of the person who performed the verification (optional).",
    )
    verified_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp when the latest verification decision was recorded (UTC).",
    )
    scope_1_override: Optional[int] = Field(
        default=None,
        description="Manual override value for Scope 1 emissions if supplied during verification.",
    )
    scope_2_override: Optional[int] = Field(
        default=None,
        description="Manual override value for Scope 2 emissions if supplied during verification.",
    )
    scope_3_override: Optional[int] = Field(
        default=None,
        description="Manual override value for Scope 3 emissions if supplied during verification.",
    )
    notes: Optional[str] = Field(
        default=None,
        description="Freeform notes captured during verification (e.g. rationale, corrections).",
    )


__all__ = ["VerificationRecord"]


