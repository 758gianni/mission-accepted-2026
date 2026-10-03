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


#: Content scanning is deliberately bounded: a test must never walk an entire
#: home directory, follow symlinks out of the tree, or materialise huge/binary
#: files. Structural assertions (metadata snapshots) prove the rest.
MAX_SCAN_BYTES = 2 * 1024 * 1024
SKIP_SUFFIXES = frozenset(
    {".so", ".pyc", ".pyo", ".o", ".a", ".dll", ".dylib", ".zip", ".gz", ".bz2",
     ".xz", ".zst", ".tar", ".whl", ".png", ".jpg", ".jpeg", ".tif", ".tiff",
     ".pdf", ".db", ".sqlite", ".bin", ".woff", ".woff2", ".ttf"}
)


def iter_scan_targets(path, *, max_files: int = 2000):
    """Yield regular, non-symlink files under ``path`` with a hard file cap."""
    path = Path(path)
    seen = 0
    if path.is_symlink() or not path.exists():
        return
    if path.is_file():
        yield path
        return
    for root, dirs, files in __import__("os").walk(path, followlinks=False):
        dirs[:] = [d for d in dirs if not (Path(root) / d).is_symlink()]
        for name in files:
            candidate = Path(root) / name
            if candidate.is_symlink() or not candidate.is_file():
                continue
            if candidate.suffix.lower() in SKIP_SUFFIXES:
                continue
            try:
                if candidate.stat().st_size > MAX_SCAN_BYTES:
                    continue
            except OSError:
                continue
            seen += 1
            if seen > max_files:
                return
            yield candidate


def contains_secret(path, *needles: str) -> list[str]:
    """Return ``"<needle> -> <file>"`` for every needle found under ``path``.

    Bounded on purpose: symlinks are skipped, binary/large/known-extension files
    are skipped, at most ``max_files`` files are read and each is read in chunks.
    """
    hits: list[str] = []
    needles = [needle for needle in needles if needle]
    if not needles:
        return hits
    for target in iter_scan_targets(path):
        try:
            with target.open("rb") as handle:
                data = b""
                while len(data) <= MAX_SCAN_BYTES:
                    chunk = handle.read(65536)
                    if not chunk:
                        break
                    data += chunk
        except OSError:
            continue
        text = data.decode("utf-8", errors="ignore")
        for needle in needles:
            if needle in text:
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
            # record and return without executing: the upstream command body never
            # runs, and the wrapper keeps going (multi-UUID loops stay observable)
            recorded.append(list(args or []))
            return None
        return original(self, args=args, **kwargs)

    monkeypatch.setattr(click.Group, "main", recording_main)
    return recorded


#: Variables the test runner itself mutates between setup and call phases.
PYTEST_MANAGED_ENV = {"PYTEST_CURRENT_TEST", "PYTEST_XDIST_WORKER", "PYTEST_ADDOPTS"}


def stable_environ() -> dict[str, str]:
    """os.environ minus the keys pytest itself rewrites between phases."""
    import os

    return {k: v for k, v in os.environ.items() if k not in PYTEST_MANAGED_ENV}
