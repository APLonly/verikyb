"""LLM access. This package is the only place that imports LiteLLM."""

import os

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
os.environ.setdefault("LITELLM_MODE", "PRODUCTION")
os.environ.setdefault("LITELLM_LOG", "CRITICAL")
