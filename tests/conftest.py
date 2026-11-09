"""Shared fixtures. The suite runs offline: every external call has a fake or a recorded fixture."""

import socket
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from verikyb.config import load_yaml
from verikyb.llm.gateway import LLMGateway, ModelsConfig

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
_real_connect = socket.socket.connect
_real_getaddrinfo = socket.getaddrinfo


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any test that connects beyond this machine. Loopback stays open because the
    asyncio event loop on Windows talks to itself over a local socket pair."""

    def guarded_connect(sock: socket.socket, address: Any) -> None:
        host = address[0] if isinstance(address, tuple) else address
        if host not in LOOPBACK:
            raise RuntimeError(f"tests must run offline, but one connected to {address}")
        _real_connect(sock, address)

    def guarded_getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        if host is not None and host not in LOOPBACK:
            raise RuntimeError(f"tests must run offline, but one looked up {host}")
        return _real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def config_dir() -> Path:
    return CONFIG_DIR


@pytest.fixture
def fake_gateway() -> Callable[..., LLMGateway]:

    def build(failing: frozenset[str] = frozenset()) -> LLMGateway:
        config = load_yaml(CONFIG_DIR / "models.yaml", ModelsConfig)
        for route in config.model_list:
            failed = route.model_name in failing
            reply = "litellm.InternalServerError" if failed else route.model_name
            route.litellm_params["mock_response"] = reply
        return LLMGateway(config.model_copy(update={"num_retries": 0}))

    return build
