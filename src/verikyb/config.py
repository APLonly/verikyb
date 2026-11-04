"""Typed loaders for the YAML files in `config/`.

Every file is validated on load, so a typo fails at startup rather than halfway through a run.
"""

from datetime import date
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, Field, model_validator

from verikyb.models import CountryCode, DomainModel, Source


def load_yaml[M: BaseModel](path: Path, schema: type[M]) -> M:
    with path.open(encoding="utf-8") as file:
        return schema.model_validate(yaml.safe_load(file))


class Question(DomainModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    section: Literal["identity", "regulatory", "ownership", "screening", "financial", "customer"]
    text: str
    answer_type: Literal["text", "date", "list", "yes_no"] = "text"
    sources: list[Source] = []
    customer_input: bool = False

    @model_validator(mode="after")
    def _filled_by_agents_or_customer(self) -> Self:
        if self.customer_input == bool(self.sources):
            raise ValueError(f"{self.id}: list sources, or mark it customer_input, not both")
        return self


class Questionnaire(DomainModel):
    questions: list[Question] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_ids(self) -> Self:
        ids = [question.id for question in self.questions]
        if len(ids) != len(set(ids)):
            raise ValueError("question IDs must be unique")
        return self


class BeneficialOwnershipRule(DomainModel):
    more_than_pct: float = Field(gt=0, lt=100)
    basis: str


class Thresholds(DomainModel):
    beneficial_ownership: dict[CountryCode, BeneficialOwnershipRule]


class HighRiskCountries(DomainModel):
    source: str
    as_of: date
    call_for_action: dict[CountryCode, str]
    increased_monitoring: dict[CountryCode, str]

    @model_validator(mode="after")
    def _lists_are_disjoint(self) -> Self:
        if overlap := set(self.call_for_action) & set(self.increased_monitoring):
            raise ValueError(f"a country is on both FATF lists: {sorted(overlap)}")
        return self


def load_questionnaire(config_dir: Path) -> Questionnaire:
    return load_yaml(config_dir / "questionnaire.yaml", Questionnaire)


def load_thresholds(config_dir: Path) -> Thresholds:
    return load_yaml(config_dir / "thresholds.yaml", Thresholds)


def load_high_risk_countries(config_dir: Path) -> HighRiskCountries:
    return load_yaml(config_dir / "high_risk_countries.yaml", HighRiskCountries)
