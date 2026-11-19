# syntax=docker/dockerfile:1

FROM python:3.14-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Runtime libraries for the locked native embedding dependencies; no compilers.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends libgomp1 libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

FROM base AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /usr/local/bin/uv

ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project --python /usr/local/bin/python

COPY src/ ./src/
RUN uv sync --locked --no-dev --no-editable --python /usr/local/bin/python \
    && .venv/bin/python -c "import uvicorn; from verikyb.api.app import create_app; create_app()"

FROM base AS runtime

ENV PATH="/app/.venv/bin:${PATH}"

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin app

WORKDIR /app

# Non-editable installation includes the src-layout package and its metadata.
COPY --from=builder /app/.venv /app/.venv
COPY config/ ./config/

USER 10001:10001

EXPOSE 8000

CMD ["uvicorn", "verikyb.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
