"""Building blocks shared by every domain model."""

from enum import StrEnum
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

RequestId = Annotated[str, StringConstraints(pattern=r"KYB-\d{2,}$")]
CountryCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")]
Confidence = Annotated[float, Field(ge=0.0, le=1.0)]
CitedIds = Annotated[list[str], Field(min_length=1)]


class DomainModel(BaseModel):
    """Base for every domain model."""

    model_config = ConfigDict(extra="forbid")


class Persona(StrEnum):
    ANALYST = "analyst"
    REVIEWER = "reviewer"
    ADMIN = "admin"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Source(StrEnum):
    """Where a fact came from."""

    FCA = "fca"
    COMPANIES_HOUSE = "companies_house"
    CH_ACCOUNTS = "ch_accounts"
    GLEIF = "gleif"
    EDGAR = "edgar"
    SANCTIONS = "sanctions"
    NEWS = "news"
    INDIA_REGISTRY = "india_registry"
    DOCUMENT = "document"
    ANALYST = "analyst"


class Workspace(StrEnum):
    DUE_DILIGENCE = "due-diligence"
    CUSTOMER = "customer"


class Tab(StrEnum):
    SUMMARY = "summary"
    QUESTIONNAIRE = "questionnaire"
    REPORT = "report"
    PROFILE = "profile"
    FINANCES = "finances"
    RISKS = "risks"
    INSIGHTS = "insights"
    CONCENTRATION = "concentration"


WORKSPACE_TABS: dict[Workspace, tuple[Tab, ...]] = {
    Workspace.DUE_DILIGENCE: (Tab.SUMMARY, Tab.QUESTIONNAIRE, Tab.REPORT),
    Workspace.CUSTOMER: (
        Tab.SUMMARY,
        Tab.PROFILE,
        Tab.FINANCES,
        Tab.RISKS,
        Tab.INSIGHTS,
        Tab.CONCENTRATION,
    ),
}


class Evidence(DomainModel):
    evidence_id: str
    request_id: RequestId
    source: Source
    locator: str = Field(description="How to find it again: API path, URL or document #page=N")
    excerpt: str
    retrieved_at: AwareDatetime
