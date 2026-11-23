"""Project paths and environment. Secrets are read from .env and never printed."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
OUTPUTS_DIR = ROOT / "outputs"

load_dotenv(ROOT / ".env")


def dataset_dir() -> Path:
    """Read-only original dataset (the original dataset is read-only). Resolved from DATASET_DIR, default data-original/."""
    raw = os.getenv("DATASET_DIR", "").strip()
    path = Path(raw) if raw else ROOT / "data-original"
    if not path.is_absolute():
        path = ROOT / path
    return path
