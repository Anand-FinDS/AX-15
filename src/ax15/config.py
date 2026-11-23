"""Model configuration from config/models.yaml (never hard-code model IDs)."""
from __future__ import annotations

from functools import lru_cache

import yaml

from ax15.settings import ROOT


@lru_cache(maxsize=1)
def models() -> dict:
    return yaml.safe_load((ROOT / "config" / "models.yaml").read_text(encoding="utf-8"))


def embedding_cfg() -> dict:
    return models()["embeddings"]


def base_llm() -> dict:
    return models()["llm"]["base"]


def fallback_llm() -> dict:
    return models()["llm"]["fallback"]


def node_cfg(name: str) -> dict:
    """Per-node settings (config nodes.<name>) over the base model settings."""
    return {**base_llm(), **(models().get("nodes") or {}).get(name, {})}
