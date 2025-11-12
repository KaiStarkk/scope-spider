from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field


class Settings(BaseModel):
    """Runtime configuration for the backend application."""

    data_root: Path = Field(
        default_factory=lambda: Path("/home/user/code/scope-spider"),
        description="Base directory containing data assets such as companies.json.",
    )
    companies_file: Path = Field(
        default_factory=lambda: Path("/home/user/code/scope-spider/companies.json"),
        description="JSON file storing company payloads.",
    )
    downloads_dir: Path = Field(
        default_factory=lambda: Path("/home/user/code/scope-spider/downloads"),
        description="Directory for original and replacement PDF documents.",
    )


@lru_cache
def get_settings() -> Settings:
    """Return cached settings instance."""

    return Settings()
