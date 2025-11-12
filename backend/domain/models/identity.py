from pydantic import BaseModel, Field, field_validator


class Identity(BaseModel):
    name: str = Field(description="The full company name as sourced from the register.")
    ticker: str = Field(
        description="The primary trading ticker, including exchange if relevant."
    )

    @field_validator("name", "ticker", mode="before")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return str(value).strip()


__all__ = ["Identity"]
