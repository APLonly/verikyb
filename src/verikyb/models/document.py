"""Upload documents and the fields extracted from them."""

from enum import StrEnum

from pydantic import AwareDatetime, Field

from verikyb.models.common import Confidence, DomainModel, RequestId


class DocumentType(StrEnum):
    CERTIFICATE_OF_INCORPORATION = "certificate_of_incorporation"
    GST_CERTIFICATE = "gst_certificate"
    PAN_CARD = "pan_card"
    SHAREHOLDING_EXTRACT = "shareholding_extract"
    ANNUAL_REPORT = "annual_report"
    OTHER = "other"


class ExtractionMethod(StrEnum):
    TEXT = "text"
    VISION = "vision"
    OCR = "ocr"


class ExtractedField(DomainModel):
    name: str
    value: str
    page: int = Field(ge=1)
    confidence: Confidence
    method: ExtractionMethod


class Document(DomainModel):
    document_id: str
    request_id: RequestId
    filename: str
    doc_type: DocumentType
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_count: int = Field(ge=1)
    uploaded_by: str
    uploaded_at: AwareDatetime
    fields: list[ExtractionMethod] = []
    injection_score: Confidence | None = None
