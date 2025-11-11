from typing import List

from pydantic import BaseModel, ConfigDict, Field


class ExtractionRecord(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    text_path: str = Field(
        alias="json_path",
        description="Path to text snippet file containing relevant PDF excerpts.",
    )
    text_token_count: int = Field(
        default=0,
        ge=0,
        description="Count of tokens in the text snippet (OpenAI model equivalent or estimate).",
    )
    snippet_page_count: int = Field(
        default=0,
        ge=0,
        alias="snippet_count",
        description="Number of PDF pages represented in the text snippet.",
    )
    table_path: str | None = Field(
        default=None,
        description="Path to table snippet file containing relevant tables (if any).",
    )
    table_count: int = Field(
        default=0,
        ge=0,
        description="Number of tables extracted.",
    )
    table_token_count: int = Field(
        default=0,
        ge=0,
        description="Count of tokens in the table snippet (OpenAI model equivalent or estimate).",
    )
    text_pages: List[int] = Field(
        default_factory=list,
        description="1-based page numbers included in the text snippet.",
    )
    table_pages: List[int] = Field(
        default_factory=list,
        description="1-based page numbers associated with extracted tables.",
    )


__all__ = ["ExtractionRecord"]
