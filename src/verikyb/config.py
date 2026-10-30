"""Typed loaders for the YAML files in `config/`."""

from pathlib import Path

import yaml
from pydantic import BaseModel


def load_yaml[M: BaseModel](path: Path, schema: type[M]) -> M:
    with path.open(encoding="utf-8") as file:
        return schema.model_validate(yaml.safe_load(file))
