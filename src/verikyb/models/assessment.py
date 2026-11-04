"""What the agents produce about a customer: answers, screening hits, flags, insights, report."""

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, Field, model_validator

from verikyb.models.common import (
    CitedIds,
    Confidence,
    DomainModel,
    RequestId,
    RiskLevel,
    Source,
)


class AnswerStatus(StrEnum):
    AUTO_FILLED = "auto_filled"
    EDITED_BY_ANALYST = "edited_by_analyst"
    NEEDS_CUSTOMER_INPUT = "needs_customer_input"


class QuestionnaireAnswer(DomainModel):
    question_id: str
    status: AnswerStatus
    value: str | None = None
    source: Source | None = None
    evidence_ids: list[str] = []
    confidence: Confidence | None = None

    @model_validator(mode="after")
    def _check_status(self) -> Self:
        if self.status is AnswerStatus.AUTO_FILLED and not (self.evidence_ids and self.source):
            raise ValueError("an auto-filled answer must cite its source and evidence")
        if self.status is AnswerStatus.AUTO_FILLED and self.confidence is None:
            raise ValueError("an auto-filled answer must carry a confidence")
        if self.status is AnswerStatus.NEEDS_CUSTOMER_INPUT and self.value is not None:
            raise ValueError("an answer awaiting the customer has no value yet")
        return self


class ScreeningList(StrEnum):
    OPENSANCTIONS = "opensanctions"
    OFAC_SDN = "ofac_sdn"
    UN_CONSOLIDATED = "un_consolidated"


class HitKind(StrEnum):
    SANCTION = "sanction"
    PEP = "pep"


class Adjudication(StrEnum):
    TRUE_MATCH = "true_match"
    FALSE_POSITIVE = "false_positive"
    NEEDS_REVIEW = "needs_review"


class ScreeningHit(DomainModel):
    hit_id: str
    screened_name: str
    matched_name: str
    list_name: ScreeningList
    list_entry_id: str
    kind: HitKind
    match_score: Confidence
    adjudication: Adjudication = Adjudication.NEEDS_REVIEW
    rationale: str | None = Field(default=None, description="Why the adjudicator decided so")
    evidence_ids: CitedIds


class RiskCategory(StrEnum):
    REGULATORY = "regulatory"
    OWNERSHIP = "ownership"
    SCREENING = "screening"
    ADVERSE_MEDIA = "adverse_media"
    FINANCIAL = "financial"
    FILING = "filing"


class FlagStatus(StrEnum):
    OPEN = "open"
    CLEARED = "cleared"


class RiskFlag(DomainModel):
    flag_id: str
    rule_id: str = Field(description="The deterministic rule that raised the flag")
    category: RiskCategory
    severity: RiskLevel
    title: str
    evidence_ids: CitedIds
    policy_citations: CitedIds = Field(description="IDs of the policy chunks that apply")
    status: FlagStatus = FlagStatus.OPEN
    cleared_reason: str | None = None

    @model_validator(mode="after")
    def _cleared_needs_reason(self) -> Self:
        cleared = self.status is FlagStatus.CLEARED
        if cleared != bool((self.cleared_reason or "").strip()):
            raise ValueError("a cleared flag needs a reason, and only a cleared flag has one")
        return self


class InsightKind(StrEnum):
    REVENUE_TREND = "revenue_trend"
    LEVERAGE_CHANGE = "leverage_change"
    LATE_FILINGS = "late_filings"
    OWNERSHIP_CHANGE = "ownership_change"
    FCA_PERMISSION_CHANGE = "fca_permission_change"


class CitedFigure(DomainModel):
    """A number the insight text relies on; the output check compares it with computed data."""

    name: str
    period_end: date | None = None
    value: Decimal


class Insight(DomainModel):
    insight_id: str
    kind: InsightKind
    text: str
    figures: list[CitedFigure] = []
    evidence_ids: CitedIds


class Claim(DomainModel):
    text: str
    evidence_ids: list[str] = []  # empty means unsupported: the critic removes or flags it


class ReportSection(DomainModel):
    title: str
    claims: list[Claim]


class Report(DomainModel):
    request_id: RequestId
    version: int = Field(ge=1, description="Incremented on every regeneration")
    generated_at: AwareDatetime
    summary: str
    sections: list[ReportSection]
    is_draft: bool = True  # watermarked DRAFT until the request is submitted
