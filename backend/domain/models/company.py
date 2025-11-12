from typing import Optional

from pydantic import BaseModel, Field

from .analysis import AnalysisRecord
from .annotations import Annotations
from .download import DownloadRecord
from .emissions import EmissionsData
from .extraction import ExtractionRecord
from .identity import Identity
from .search import SearchRecord
from .verification import VerificationRecord


class Company(BaseModel):
    identity: Identity
    emissions: EmissionsData = Field(default_factory=EmissionsData)
    annotations: Annotations = Field(default_factory=Annotations)
    search_record: Optional[SearchRecord] = None
    download_record: Optional[DownloadRecord] = None
    extraction_record: Optional[ExtractionRecord] = None
    analysis_record: Optional[AnalysisRecord] = None
    verification: VerificationRecord = Field(default_factory=VerificationRecord)


__all__ = ["Company"]
