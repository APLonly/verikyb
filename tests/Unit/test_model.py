from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from verikyb.models import (
    AnswerStatus,
    AuditEvent,
    ChatMessage,
    ChatRole,
    Customer,
    Decision,
    DecisionOutcome,
    FlagStatus,
    KYBRequest,
    OwnershipEdge,
    Persona,
    QuestionnaireAnswer,
    RiskFlag,
    RiskLevel,
    Tab,
    Workspace,
)

NOW = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
REQUEST = {
    "request_id": "KYB-01",
    "customer_name": "Northwind Payments Ltd",
    "jurisdiction": "GB",
    "created_by": "analyst-1",
    "created_at": NOW,
}
FLAG = {
    "flag_id": "FLAG-1",
    "rule_id": "accounts_overdue",
    "category": "filing",
    "severity": "medium",
    "title": "Annual accounts overdue",
    "evidence_ids": ["EV-1"],
    "policy_citations": ["POL-3"],
}


def decision(**overrides: Any) -> Decision:
    fields = {
        "request_id": "KYB-01",
        "outcome": DecisionOutcome.APPROVE,
        "suggested_risk_level": RiskLevel.MEDIUM,
        "risk_level": RiskLevel.MEDIUM,
        "decided_by": "reviewer-1",
        "decided_at": NOW,
    }
    return Decision.model_validate(fields | overrides)


def high_risk_approval(**overrides: Any) -> Decision:
    return decision(suggested_risk_level=RiskLevel.HIGH, risk_level=RiskLevel.HIGH, **overrides)


@pytest.mark.parametrize("request_id", ["KYB-1", "kyb-01", "KYB01", "KYB-01a"])
def test_request_id_must_look_like_kyb_nn(request_id: str) -> None:
    with pytest.raises(ValidationError):
        KYBRequest.model_validate(REQUEST | {"request_id": request_id})


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        KYBRequest.model_validate(REQUEST | {"risk_score": 99})


def test_timestamps_must_carry_a_timezone() -> None:
    with pytest.raises(ValidationError):
        KYBRequest.model_validate(REQUEST | {"created_at": NOW.replace(tzinfo=None)})


def test_risk_override_needs_a_reason() -> None:
    with pytest.raises(ValidationError, match="needs a reason"):
        decision(risk_level=RiskLevel.HIGH)

    assert decision(risk_level=RiskLevel.HIGH, override_reason="Opaque offshore parent").is_override


def test_high_risk_approval_waits_for_a_different_second_reviewer() -> None:
    assert high_risk_approval().awaiting_second_review
    assert not high_risk_approval(second_reviewer="reviewer-2").awaiting_second_review
    with pytest.raises(ValidationError, match="different person"):
        high_risk_approval(second_reviewer="reviewer-1")


def test_audit_events_cannot_be_edited() -> None:
    event = AuditEvent(
        event_id="AUD-1",
        request_id="KYB-01",
        user_id="analyst-1",
        persona=Persona.ANALYST,
        at=NOW,
        action="status_changed",
        old_value="draft",
        new_value="agents_running",
    )
    with pytest.raises(ValidationError, match="frozen"):
        event.new_value = "submitted"


def test_risk_flag_must_cite_policy() -> None:
    assert RiskFlag.model_validate(FLAG).status is FlagStatus.OPEN
    with pytest.raises(ValidationError):
        RiskFlag.model_validate(FLAG | {"policy_citations": []})


def test_cleared_risk_flag_needs_a_reason() -> None:
    with pytest.raises(ValidationError, match="needs a reason"):
        RiskFlag.model_validate(FLAG | {"status": "cleared"})

    cleared = FLAG | {
        "status": "cleared",
        "cleared_reason": "Accounts filed on 2026-10-01",
    }
    assert RiskFlag.model_validate(cleared).status is FlagStatus.CLEARED


def test_auto_filled_answer_must_cite_its_evidence() -> None:
    with pytest.raises(ValidationError, match="cite"):
        QuestionnaireAnswer(
            question_id="lei",
            status=AnswerStatus.AUTO_FILLED,
            value="213800ABCDEFGHIJKL12",
            confidence=0.97,
        )


def test_answer_awaiting_the_customer_has_no_value() -> None:
    with pytest.raises(ValidationError, match="no value"):
        QuestionnaireAnswer(
            question_id="source_of_funds",
            status=AnswerStatus.NEEDS_CUSTOMER_INPUT,
            value="Trading income",
        )


def test_ownership_range_must_be_ordered() -> None:
    with pytest.raises(ValidationError, match="cannot exceed"):
        OwnershipEdge.model_validate(
            {
                "owner_id": "P-1",
                "owner_kind": "person",
                "owned_id": "C-1",
                "kind": "shares",
                "min_pct": 75,
                "max_pct": 50,
                "evidence_ids": ["EV-2"],
            }
        )


def test_customer_field_sources_must_name_real_fields() -> None:
    customer = {
        "request_id": "KYB-01",
        "legal_name": "Northwind Payments Ltd",
        "jurisdiction": "GB",
        "is_synthetic": True,
    }
    Customer.model_validate(customer | {"field_sources": {"legal_name": ["EV-1"]}})
    with pytest.raises(ValidationError, match="unknown fields"):
        Customer.model_validate(customer | {"field_sources": {"turnover": ["EV-1"]}})


def test_chat_tab_must_belong_to_its_workspace() -> None:
    with pytest.raises(ValidationError, match="not part of"):
        ChatMessage(
            message_id="MSG-1",
            request_id="KYB-01",
            workspace=Workspace.DUE_DILIGENCE,
            tab=Tab.FINANCES,
            role=ChatRole.USER,
            content="What was revenue last year?",
            user_id="analyst-1",
            created_at=NOW,
        )
