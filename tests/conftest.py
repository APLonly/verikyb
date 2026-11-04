"""Shared fixtures. The suite runs offline: every external call has a fake or a recorded fixture."""

import socket
from pathlib import Path
from typing import Any

import pytest

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
_real_connect = socket.socket.connect


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any test that connects beyond this machine. Loopback stays open because the
    asyncio event loop on Windows talks to itself over a local socket pair."""

    def guarded_connect(sock: socket.socket, address: Any) -> None:
        host = address[0] if isinstance(address, tuple) else address
        if host not in LOOPBACK:
            raise RuntimeError(f"tests must run offline, but one connected to {address}")
        _real_connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def config_dir() -> Path:
    return CONFIG_DIR
