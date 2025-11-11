from pydantic import BaseModel, Field


class DownloadRecord(BaseModel):
    pdf_path: str = Field(description="Absolute or relative path to the downloaded PDF.")


__all__ = ["DownloadRecord"]
