"""Configuration for the ForestWatch backend API.

Shared project configuration is owned by the team lead; this module only
holds the small set of knobs the backend needs, all overridable through
environment variables.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_BUNDLE_DIR = "data/processed/current"
BUNDLE_DIR_ENV = "FORESTWATCH_BUNDLE_DIR"
CORS_ORIGINS_ENV = "FORESTWATCH_CORS_ORIGINS"

#: Local development frontends that are allowed to call the API cross-origin.
DEFAULT_CORS_ORIGINS: tuple[str, ...] = (
    "http://localhost:3000",
    "http://localhost:5173",
)


def default_bundle_dir() -> Path:
    """Resolve the bundle directory: ``FORESTWATCH_BUNDLE_DIR`` or the default."""
    configured = os.environ.get(BUNDLE_DIR_ENV, "").strip()
    return Path(configured) if configured else Path(DEFAULT_BUNDLE_DIR)


def default_cors_origins() -> list[str]:
    """Resolve the allowed CORS origins from the environment."""
    configured = os.environ.get(CORS_ORIGINS_ENV)
    if configured is None:
        return list(DEFAULT_CORS_ORIGINS)
    return [origin.strip() for origin in configured.split(",") if origin.strip()]


@dataclass(frozen=True)
class Settings:
    """Immutable settings snapshot taken when the application is created."""

    bundle_dir: Path = field(default_factory=default_bundle_dir)
    cors_origins: list[str] = field(default_factory=default_cors_origins)
