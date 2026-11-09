"""The single door to every LLM call."""

from enum import StrEnum
from pathlib import Path
from typing import Any, Self

import openai
from litellm import RouterRateLimitError, model_cost
from litellm.router import Router
from litellm.types.llms.openai import AllMessageValues
from pydantic import Field, NonNegativeInt, PositiveFloat, model_validator

from verikyb.config import load_yaml
from verikyb.models import DomainModel

_PRICED_MODELS = frozenset(model_cost)


class ModelTask(StrEnum):
    EXTRACTION = "extraction"
    REASONING = "reasoning"


class LLMUnavailableError(Exception):
    """Every Router for a task failed."""


class ModelRoute(DomainModel):
    model_name: str
    litellm_params: dict[str, Any]


class ModelsConfig(DomainModel):
    model_list: list[ModelRoute]
    fallbacks: dict[str, list[str]] = {}
    num_retries: NonNegativeInt = 2
    timeout_seconds: PositiveFloat = 60

    @model_validator(mode="after")
    def _every_route_is_defined(self) -> Self:
        defined = {route.model_name for route in self.model_list}
        targets = {target for chain in self.fallbacks.values() for target in chain}
        if missing := (set(ModelTask) | set(self.fallbacks) | targets) - defined:
            raise ValueError(f"no route defined for: {sorted(missing)}")
        return self


class LLMResult(DomainModel):
    """One answer. Usage and cost stay None when the provider or price table lacks them."""

    content: str
    model: str | None = Field(description="The model that answered, after any fallback")
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None


class LLMGateway:
    def __init__(self, config: ModelsConfig) -> None:
        self._router = Router(
            model_list=[route.model_dump() for route in config.model_list],
            fallbacks=[{task: chain} for task, chain in config.fallbacks.items()],
            num_retries=config.num_retries,
            timeout=config.timeout_seconds,
        )

    @classmethod
    def from_file(cls, path: Path) -> Self:
        return cls(load_yaml(path, ModelsConfig))

    async def complete(self, task: ModelTask, messages: list[AllMessageValues]) -> LLMResult:
        try:
            response = await self._router.acompletion(model=task.value, messages=messages)
        except (openai.OpenAIError, RouterRateLimitError) as exc:
            raise LLMUnavailableError(f"no {task} model answered ({type(exc).__name__})") from exc
        usage = getattr(response, "usage", None)  # LiteLLM attaches usage dynamically
        return LLMResult(
            content=response.choices[0].message.content or "",
            model=response.model,
            input_tokens=usage.prompt_tokens if usage else None,
            output_tokens=usage.completion_tokens if usage else None,
            cost_usd=_cost_usd(response),
        )


def _cost_usd(response: Any) -> float | None:
    """The call's cost. LiteLLM reports 0.0 for a model missing from the price table, which would look free, so that becomes None."""
    cost: float | None = response._hidden_params.get("response_cost")
    provider = response._hidden_params.get("custom_llm_provider")
    if cost == 0 and not {response.model, f"{provider}/{response.model}"} & _PRICED_MODELS:
        return None
    return cost
