from typing import List, Optional

from pydantic import BaseModel, Field


class AnalysisRecord(BaseModel):
    method: str = Field(
        description="Analysis method that produced the current emissions values (e.g. python, local-llm)."
    )
    snippet_label: str = Field(
        description="Identifier for the snippet type that was analysed (e.g. text, tables)."
    )
    snippet_path: Optional[str] = Field(
        default=None,
        description="Path to the snippet file that fed the analysis, if available.",
    )
    snippet_pages: List[int] = Field(
        default_factory=list,
        description="1-based page numbers referenced by the analysed snippet.",
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Confidence score (0-1) reported by the analysis method.",
    )
    analysed_at: Optional[str] = Field(
        default=None,
        description="ISO 8601 timestamp (UTC) when the analysis was recorded.",
    )


__all__ = ["AnalysisRecord"]
