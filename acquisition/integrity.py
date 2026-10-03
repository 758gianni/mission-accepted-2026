"""Fail-closed integrity enforcement for the pinned EODMS CLI install.

``git rev-parse HEAD`` alone is not enough: a modified, deleted or injected file
in the source tree keeps the same HEAD. This module therefore verifies, **before
the upstream module is imported and before any credential prompt**:

1. the source tree is checked out at the pinned revision;
2. the source tree has no modified, staged, deleted **or untracked** files, so the
   bytes that will be imported are the audited bytes;
3. the installed distributions recorded by the bootstrap match their pinned
   versions, and every installed file still hashes to the value in the
   distribution's own ``RECORD`` (pip's integrity hash), so no dependency file was
   edited in place;
4. the recorded pin set matches the pins compiled into the wrapper.

Any mismatch raises :class:`IntegrityError` and nothing is prompted for. The
manifest is written by ``acquisition/bootstrap_eodms_cli.sh`` (or
``python -m acquisition.integrity --write-manifest``).
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import (
    EODMS_CLI_REPO,
    EODMS_CLI_REV,
    EODMS_PY_REPO,
    EODMS_PY_REV,
    RAPI_REPO,
    RAPI_REV,
    REPO_ROOT,
    VENV_DIR,
)

TOOLS_PATH = Path(REPO_ROOT) / VENV_DIR
MANIFEST_PATH = TOOLS_PATH.parent / "install-manifest.json"
UPSTREAM_SRC = TOOLS_PATH.parent / "src"

#: Never part of the integrity surface (installer tooling, not runtime code).
_EXCLUDED_DISTRIBUTIONS = {"pip", "setuptools", "wheel", "pkg_resources"}

_ALGORITHMS = {"sha256": hashlib.sha256, "sha384": hashlib.sha384, "sha512": hashlib.sha512}


class IntegrityError(RuntimeError):
    """Raised when the install cannot be proven to match the audited pins."""


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(UPSTREAM_SRC), *args],
        capture_output=True,
        text=True,
    )


def _record_files(dist: Any) -> list[tuple[str, str, str]]:
    """Return ``(relative path, algorithm, digest)`` triples from a RECORD file."""
    try:
        text = dist.read_text("RECORD")
    except Exception as exc:  # pragma: no cover - RECORD is always shipped
        raise IntegrityError(f"{dist.metadata['Name']}: RECORD unreadable ({exc})") from exc
    if not text:
        raise IntegrityError(f"{dist.metadata['Name']}: RECORD is empty")
    entries: list[tuple[str, str, str]] = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 3 or not row[1]:
            continue
        algorithm, _, digest = row[1].partition("=")
        entries.append((row[0], algorithm, digest))
    return entries


def _hash_file(path: Path, algorithm: str) -> str:
    hasher = _ALGORITHMS[algorithm]()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            hasher.update(chunk)
    return base64.urlsafe_b64encode(hasher.digest()).rstrip(b"=").decode()


def _installed_distributions() -> dict[str, Any]:
    from importlib.metadata import distributions

    found: dict[str, Any] = {}
    for dist in distributions():
        name = (dist.metadata["Name"] or "").lower()
        if name and name not in _EXCLUDED_DISTRIBUTIONS:
            found[name] = dist
    return found


def build_manifest() -> dict[str, Any]:
    """Hash every installed file of every distribution in the venv."""
    distributions: dict[str, Any] = {}
    for name, dist in sorted(_installed_distributions().items()):
        files = _record_files(dist)
        digest = hashlib.sha256()
        for relative, algorithm, expected in sorted(files):
            path = Path(dist.locate_file(relative))
            digest.update(f"{relative}:{expected}\n".encode())
        distributions[name] = {
            "version": dist.version,
            "file_count": len(files),
            "record_sha256": digest.hexdigest(),
        }
    return {
        "eodms_cli": {"repo": EODMS_CLI_REPO, "rev": EODMS_CLI_REV},
        "eodms_py": {"repo": EODMS_PY_REPO, "rev": EODMS_PY_REV},
        "py_eodms_rapi": {"repo": RAPI_REPO, "rev": RAPI_REV},
        "python": sys.version.split()[0],
        "distributions": distributions,
    }


def write_manifest(path: Path | None = None) -> Path:
    target = Path(path or MANIFEST_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = build_manifest()
    temp = target.with_suffix(".json.tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temp, target)
    return target


def _verify_source_tree() -> str:
    if not (UPSTREAM_SRC / "eodms_cli.py").is_file():
        raise IntegrityError(
            f"Pinned EODMS CLI source missing at {UPSTREAM_SRC}. "
            "Run: bash acquisition/bootstrap_eodms_cli.sh"
        )
    head = _git("rev-parse", "HEAD")
    revision = head.stdout.strip()
    if head.returncode != 0:
        raise IntegrityError(f"cannot determine the installed revision: {head.stderr.strip()}")
    if revision != EODMS_CLI_REV:
        raise IntegrityError(
            f"installed eodms-cli revision {revision} != pinned {EODMS_CLI_REV}"
        )

    status = _git("status", "--porcelain", "--untracked-files=all")
    if status.returncode != 0:
        raise IntegrityError(f"cannot verify the source tree: {status.stderr.strip()}")
    dirty = [line for line in status.stdout.splitlines() if line.strip()]
    if dirty:
        preview = "; ".join(dirty[:5])
        raise IntegrityError(
            "the pinned source tree is not pristine "
            f"({len(dirty)} modified/untracked entr{'y' if len(dirty) == 1 else 'ies'}: {preview}). "
            "Refusing to import or send credentials to unverified code. "
            "Restore it with: bash acquisition/bootstrap_eodms_cli.sh"
        )
    return revision


def _verify_pins(manifest: dict[str, Any]) -> None:
    expected = {
        "eodms_cli": EODMS_CLI_REV,
        "eodms_py": EODMS_PY_REV,
        "py_eodms_rapi": RAPI_REV,
    }
    for key, revision in expected.items():
        found = (manifest.get(key) or {}).get("rev")
        if found != revision:
            raise IntegrityError(
                f"install manifest {key} pin {found!r} != wrapper pin {revision!r}; "
                "re-run the bootstrap"
            )


def _verify_distributions(manifest: dict[str, Any]) -> int:
    recorded = manifest.get("distributions") or {}
    if not recorded:
        raise IntegrityError("install manifest records no distributions; re-run the bootstrap")
    installed = _installed_distributions()

    missing = sorted(set(recorded) - set(installed))
    if missing:
        raise IntegrityError(
            f"installed dependencies missing vs the manifest: {', '.join(missing[:5])}; "
            "re-run the bootstrap"
        )
    changed: list[str] = []
    for name, expected in sorted(recorded.items()):
        dist = installed[name]
        if dist.version != expected["version"]:
            changed.append(f"{name} {dist.version} != pinned {expected['version']}")
            continue
        digest = hashlib.sha256()
        entries = _record_files(dist)
        if len(entries) != expected["file_count"]:
            changed.append(f"{name} file count {len(entries)} != {expected['file_count']}")
            continue
        for relative, algorithm, expected_hash in sorted(entries):
            path = Path(dist.locate_file(relative))
            if not path.is_file():
                changed.append(f"{name}: missing {relative}")
                break
            try:
                actual = _hash_file(path, algorithm)
            except OSError as exc:
                changed.append(f"{name}: unreadable {relative} ({exc})")
                break
            if actual != expected_hash:
                changed.append(f"{name}: {relative} fails its RECORD hash")
                break
            digest.update(f"{relative}:{expected_hash}\n".encode())
        else:
            if digest.hexdigest() != expected["record_sha256"]:
                changed.append(f"{name} RECORD digest differs from the manifest")
    if changed:
        raise IntegrityError(
            "installed dependencies fail their pins: "
            + "; ".join(changed[:5])
            + ". Refusing to import or send credentials. Re-run the bootstrap."
        )
    return len(recorded)


def verify_installation() -> dict[str, Any]:
    """Verify source-tree and dependency integrity. Raises on any mismatch."""
    revision = _verify_source_tree()
    if not MANIFEST_PATH.is_file():
        raise IntegrityError(
            f"install manifest missing at {MANIFEST_PATH}. "
            "Run: bash acquisition/bootstrap_eodms_cli.sh"
        )
    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IntegrityError(f"install manifest unreadable: {exc}") from exc
    _verify_pins(manifest)
    packages = _verify_distributions(manifest)
    return {
        "revision": revision,
        "packages_verified": packages,
        "manifest": str(MANIFEST_PATH),
    }


def _main(argv: list[str]) -> int:
    if "--write-manifest" in argv:
        target = write_manifest()
        print(f"[integrity] wrote {target}")
        return 0
    try:
        summary = verify_installation()
    except IntegrityError as exc:
        sys.stderr.write(f"[integrity] FAILED: {exc}\n")
        return 1
    print(
        f"[integrity] OK revision={summary['revision']} "
        f"packages={summary['packages_verified']} manifest={summary['manifest']}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main(sys.argv[1:]))