from pathlib import Path

import pytest
from pydantic import ValidationError

from verikyb.config import (
    Questionnaire,
    load_high_risk_countries,
    load_questionnaire,
    load_thresholds,
)


def test_questionnaire_has_the_standard_questions(config_dir: Path) -> None:
    questions = load_questionnaire(config_dir).questions

    assert len(questions) == 25
    assert {q.id for q in questions if q.customer_input} == {"source_of_funds", "expected_volumes"}


def test_question_ids_must_be_unique() -> None:
    question = {"id": "lei", "section": "identity", "text": "LEI?", "sources": ["gleif"]}
    with pytest.raises(ValidationError, match="unique"):
        Questionnaire.model_validate({"questions": [question, question]})


def test_question_is_filled_by_agents_or_by_the_customer() -> None:
    question = {"id": "lei", "section": "identity", "text": "LEI?", "customer_input": True}
    with pytest.raises(ValidationError, match="not both"):
        Questionnaire.model_validate({"questions": [question | {"sources": ["gleif"]}]})


def test_beneficial_ownership_thresholds_follow_uk_and_india_rules(config_dir: Path) -> None:
    rules = load_thresholds(config_dir).beneficial_ownership

    assert rules["GB"].more_than_pct == 25
    assert rules["IN"].more_than_pct == 10


def test_high_risk_countries_load(config_dir: Path) -> None:
    countries = load_high_risk_countries(config_dir)

    assert set(countries.call_for_action) == {"IR", "KP", "MM"}
    assert countries.increased_monitoring
