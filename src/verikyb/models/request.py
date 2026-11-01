"""KYB case: Reviewer's decision on it and audit trail."""

from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, ConfigDict, Field, model_validator

from verikyb.models.common import (
    CountryCode,
    DomainModel,
    Persona,
    RequestId,
    RiskLevel,
)


class RequestStatus(StrEnum):
    DRAFT = "draft"
    AGENTS_RUNNING = "agents_running"
    READY_FOR_REVIEW = "ready_for_review"
    SUBMITTED = "submitted"


class DecisionOutcome(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    REQUEST_MORE_INFORMATION = "request_more_information"
    ESCALATE = "escalate"


class KYBRequest(DomainModel):
    request_id: RequestId
    customer_name: str = Field(min_length=1, description="As entered, before entity resolution")
    company_number: str | None = None
    jurisdiction: CountryCode
    status: RequestStatus = RequestStatus.DRAFT
    risk_tier: RiskLevel | None = Field(default=None, description="Set by the rules engine only")
    assignee: str | None = None
    created_by: str
    created_at: AwareDatetime


class Decision(DomainModel):
    request_id: RequestId
    outcome: DecisionOutcome
    suggested_risk_level: RiskLevel
    risk_level: RiskLevel
    override_reason: str | None = None
    decided_by: str
    decided_at: AwareDatetime
    second_reviewer: str | None = None

    @property
    def is_override(self) -> bool:
        return self.risk_level is not self.suggested_risk_level

    @property
    def awaiting_second_review(self) -> bool:
        """High-risk approvals take effect only once a second reviewer has signed off."""
        return self.outcome is DecisionOutcome.APPROVE and self.risk_level is RiskLevel.HIGH and self.second_reviewer is None

    @model_validator(mode="after")
    def _check_review_rules(self) -> Self:
        if self.is_override and not (self.override_reason or "").strip():
            raise ValueError("overriding the suggested risk level needs a reason")
        if self.second_reviewer is not None and self.second_reviewer == self.decided_by:
            raise ValueError("the second reviewer must be a different person")
        return self


class AuditEvent(DomainModel):
    """One row of the append-only audit log; frozen because history is never edited."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str
    request_id: RequestId
    user_id: str
    persona: Persona
    at: AwareDatetime
    action: str = Field(min_length=1, examples=["status_changed", "answer_edited"])
    target: str | None = Field(default=None, description="What changed, e.g. a question ID")
    old_value: str | None = None
    new_value: str | None = None
    reason: str | None = None
