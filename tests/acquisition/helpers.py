"""Shared constants and helpers for the acquisition wrapper tests.

The tests import the *real* pinned upstream eodms_cli from
.tools/eodms-cli/src. If that install is missing, tests that need it are
skipped with an explicit reason so nothing fails silently.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = REPO_ROOT / ".tools" / "eodms-cli"
UPSTREAM_SRC = TOOLS_DIR / "src"
VENV_PYTHON = TOOLS_DIR / ".venv" / "bin" / "python"
PINNED_REV = "464b94920e7faf28c84a6d31229ef0b2828a1479"

BOOTSTRAP_MISSING = (
    "pinned eodms-cli source not installed; run bash acquisition/bootstrap_eodms_cli.sh"
)


def ensure_repo_on_path() -> None:
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))


def import_upstream():
    """Import the pinned upstream CLI module, or return None when unavailable."""
    ensure_repo_on_path()
    if not (UPSTREAM_SRC / "eodms_cli.py").is_file():
        return None
    if str(UPSTREAM_SRC) not in sys.path:
        sys.path.insert(0, str(UPSTREAM_SRC))
    import eodms_cli  # noqa: PLC0415

    return eodms_cli


def contains_secret(path, *needles: str) -> list[str]:
    """Return ``"<needle> -> <file>"`` for every needle found under path."""
    path = Path(path)
    hits: list[str] = []
    if path.is_dir():
        targets = [p for p in path.rglob("*") if p.is_file()]
    elif path.is_file():
        targets = [path]
    else:
        return hits
    for target in targets:
        try:
            data = target.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for needle in needles:
            if needle and needle in data:
                hits.append(f"{needle} -> {target}")
    return hits


def record_upstream_argv(monkeypatch, upstream_group_name: str = "cli") -> list[list[str]]:
    """Record only the argv the wrapper hands to the *upstream* Click group.

    The wrapper's own group is invoked through the same ``click.Group.main``,
    so it is filtered out by name.
    """
    import click

    recorded: list[list[str]] = []
    original = click.Group.main

    def recording_main(self, args=None, **kwargs):
        if getattr(self, "name", None) == upstream_group_name:
            recorded.append(list(args or []))
            raise SystemExit(0)
        return original(self, args=args, **kwargs)

    monkeypatch.setattr(click.Group, "main", recording_main)
    return recorded
