from pydantic import BaseModel, Field


class ExtractionRecord(BaseModel):
    json_path: str = Field(description="Path to JSON payload containing snippets and/or tables.")
    snippet_count: int = Field(ge=0, description="Number of textual snippets extracted.")
    table_count: int = Field(ge=0, description="Number of tables extracted.")


__all__ = ["ExtractionRecord"]
