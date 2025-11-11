from typing import Optional

from pydantic import BaseModel, Field

from .download import DownloadRecord
from .emissions import EmissionsData
from .extraction import ExtractionRecord
from .identity import Identity
from .search import SearchRecord


class Company(BaseModel):
    identity: Identity
    emissions: EmissionsData = Field(default_factory=EmissionsData)
    search_record: Optional[SearchRecord] = None
    download_record: Optional[DownloadRecord] = None
    extraction_record: Optional[ExtractionRecord] = None


__all__ = ["Company"]
