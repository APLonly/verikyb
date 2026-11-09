import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from litellm import RouterRateLimitError
from pydantic import ValidationError

from verikyb.config import load_yaml
from verikyb.llm.gateway import LLMGateway, LLMUnavailableError, ModelsConfig, ModelTask

PROMPT = [{"role": "user", "content": "Extract the company number."}]
SOURCE_DIR = Path(__file__).resolve().parents[2] / "src"
EXTRACTION_CHAIN = ["extraction", "extraction-2", "extraction-3", "extraction-local"]


@pytest.mark.anyio
async def test_complete_reports_reply_and_tokens(fake_gateway: Callable[..., LLMGateway]) -> None:
    result = await fake_gateway().complete(ModelTask.EXTRACTION, PROMPT)

    assert result.content == "extraction"
    assert result.model
    assert result.input_tokens
    assert result.output_tokens
    # LiteLLM's price table has no Mesh prices, so the cost shows as missing instead of 0.0.
    assert result.cost_usd is None


@pytest.mark.anyio
async def test_falls_back_in_order_when_providers_fail(
    fake_gateway: Callable[..., LLMGateway],
) -> None:
    for failed in range(1, len(EXTRACTION_CHAIN)):
        gateway = fake_gateway(failing=frozenset(EXTRACTION_CHAIN[:failed]))
        result = await gateway.complete(ModelTask.EXTRACTION, PROMPT)
        assert result.content == EXTRACTION_CHAIN[failed]


@pytest.mark.anyio
async def test_the_model_comes_from_config_and_an_unpriced_one_has_no_cost() -> None:
    routes = [
        {
            "model_name": task.value,
            "litellm_params": {"model": f"openai/not-priced-{task.value}", "mock_response": "ok"},
        }
        for task in ModelTask
    ]
    gateway = LLMGateway(ModelsConfig.model_validate({"model_list": routes}))

    result = await gateway.complete(ModelTask.REASONING, PROMPT)

    assert result.model == "not-priced-reasoning"
    assert result.cost_usd is None  # shown as missing, never guessed


@pytest.mark.anyio
async def test_a_free_model_costs_zero_rather_than_unknown(
    fake_gateway: Callable[..., LLMGateway],
) -> None:
    gateway = fake_gateway(failing=frozenset(EXTRACTION_CHAIN[:-1]))

    result = await gateway.complete(ModelTask.EXTRACTION, PROMPT)

    assert result.content == "extraction-local"
    assert result.cost_usd == 0


def test_config_must_define_a_route_for_every_task() -> None:
    routes = [{"model_name": "extraction", "litellm_params": {"model": "openai/any-model"}}]
    with pytest.raises(ValidationError, match="reasoning"):
        ModelsConfig.model_validate({"model_list": routes})


def test_config_must_define_a_route_for_every_fallback(config_dir: Path) -> None:
    config = load_yaml(config_dir / "models.yaml", ModelsConfig).model_dump()
    config["fallbacks"]["extraction"] = ["extraction-22"]  # a typo fails at start, not mid-run
    with pytest.raises(ValidationError, match="extraction-22"):
        ModelsConfig.model_validate(config)


@pytest.mark.anyio
@pytest.mark.parametrize("task", list(ModelTask))
async def test_without_a_mesh_key_callers_get_an_error_they_can_handle(task: ModelTask, config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The `openai/` routes would fall back to OPENAI_API_KEY, so that goes too.
    for key in ("MESH_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("MESH_API_BASE", "https://mesh.invalid/v1")
    # Nothing listens on port 9, so a local Ollama cannot answer in place of Mesh.
    monkeypatch.setenv("OLLAMA_API_BASE", "http://127.0.0.1:9")
    config = load_yaml(config_dir / "models.yaml", ModelsConfig)
    gateway = LLMGateway(config.model_copy(update={"num_retries": 0}))

    with pytest.raises(LLMUnavailableError, match=f"no {task} model answered"):
        await gateway.complete(task, PROMPT)


@pytest.mark.anyio
async def test_routes_cooling_down_give_callers_an_error_they_can_handle(fake_gateway: Callable[..., LLMGateway], monkeypatch: pytest.MonkeyPatch) -> None:
    gateway = fake_gateway()

    async def cooling_down(**_: Any) -> None:  # the Router's answer right after routes have failed
        raise RouterRateLimitError(model="extraction", cooldown_time=5, enable_pre_call_checks=False, cooldown_list=[])

    monkeypatch.setattr(gateway._router, "acompletion", cooling_down)

    with pytest.raises(LLMUnavailableError, match="no extraction model answered"):
        await gateway.complete(ModelTask.EXTRACTION, PROMPT)


@pytest.mark.anyio
async def test_when_every_route_fails_callers_get_an_error_they_can_handle(
    fake_gateway: Callable[..., LLMGateway],
) -> None:
    gateway = fake_gateway(failing=frozenset(EXTRACTION_CHAIN))

    with pytest.raises(LLMUnavailableError, match="no extraction model answered"):
        await gateway.complete(ModelTask.EXTRACTION, PROMPT)


def test_no_model_id_is_written_in_the_code(config_dir: Path) -> None:
    config = load_yaml(config_dir / "models.yaml", ModelsConfig)
    model_ids = {route.litellm_params["model"].split("/", 1)[1] for route in config.model_list}

    found = {f"{path.relative_to(SOURCE_DIR)}: {model_id}" for path in SOURCE_DIR.rglob("*.py") for model_id in model_ids if model_id in path.read_text(encoding="utf-8")}

    assert not found


def test_importing_litellm_does_not_load_a_developers_env_file() -> None:
    assert os.environ["LITELLM_MODE"] != "DEV"
