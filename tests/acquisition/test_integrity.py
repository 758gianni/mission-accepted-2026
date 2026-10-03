"""Integrity enforcement: source tree and installed dependency pins.

Each test reproduces a real tampering scenario, proves the wrapper fails closed
**without prompting for credentials or importing the tampered module**, and then
restores the install so later tests still see an audited tree.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from acquisition import cli as wrapper_cli
from acquisition.integrity import (
    MANIFEST_PATH,
    UPSTREAM_SRC,
    IntegrityError,
    verify_installation,
    write_manifest,
)

SENTINEL_USER = "integrity.sentinel@example.invalid"
SENTINEL_PASS = "1ntegr1ty-s3ntinel-p4ssw0rd-DO-NOT-LOG"
VENV_SITE = next((UPSTREAM_SRC.parent / ".venv" / "lib").glob("python*/site-packages"))


def _prompt_must_not_run(monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("credentials must not be requested from unverified code")

    monkeypatch.setattr(wrapper_cli, "prompt_username", explode)
    monkeypatch.setattr(wrapper_cli, "prompt_password", explode)


def _restore_source(name: str, original: bytes) -> None:
    (UPSTREAM_SRC / name).write_bytes(original)


def _no_upstream_invocation(monkeypatch):
    from helpers import record_upstream_argv

    return record_upstream_argv(monkeypatch)


# --------------------------------------------------------------------------


def test_clean_install_passes():
    summary = verify_installation()
    assert summary["revision"] == wrapper_cli.EODMS_CLI_REV
    assert summary["packages_verified"] > 0


def test_modified_tracked_source_fails_closed_with_unchanged_head(monkeypatch):
    """HEAD does not change when a tracked file is edited - the bug this closes."""
    target = UPSTREAM_SRC / "eodms_cli.py"
    original = target.read_bytes()
    head_before = subprocess.run(
        ["git", "-C", str(UPSTREAM_SRC), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    try:
        target.write_bytes(original + b"\n# tampered by an integrity test\n")
        head_after = subprocess.run(
            ["git", "-C", str(UPSTREAM_SRC), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        assert head_after == head_before, "precondition: HEAD must be unchanged"

        with pytest.raises(IntegrityError) as excinfo:
            verify_installation()
        assert "not pristine" in str(excinfo.value)
        assert "eodms_cli.py" in str(excinfo.value)

        _prompt_must_not_run(monkeypatch)
        recorded = _no_upstream_invocation(monkeypatch)
        result = CliRunner().invoke(
            wrapper_cli.main, ["search", "--collection", "C", "--bbox", "0,0,1,1"]
        )
        assert result.exit_code != 0
        assert "not pristine" in result.output
        assert recorded == [], "upstream must not be imported or invoked"
    finally:
        _restore_source("eodms_cli.py", original)
    verify_installation()


def test_untracked_source_file_fails_closed(monkeypatch):
    injected = UPSTREAM_SRC / "injected_module.py"
    injected.write_text("SECRET = 'injected'\n")
    try:
        with pytest.raises(IntegrityError) as excinfo:
            verify_installation()
        assert "injected_module.py" in str(excinfo.value)

        _prompt_must_not_run(monkeypatch)
        result = CliRunner().invoke(
            wrapper_cli.main, ["download", "--collection", "C", "--uuid", "u1"]
        )
        assert result.exit_code != 0
        assert "not pristine" in result.output
    finally:
        injected.unlink()
    verify_installation()


def test_modified_installed_dependency_fails_closed():
    candidates = sorted(VENV_SITE.glob("eodms/*.py"))
    assert candidates, "expected the installed eodms package to be present"
    target = candidates[0]
    original = target.read_bytes()
    try:
        target.write_bytes(original + b'\nINJECTED_SENTINEL = "leak"\n')
        with pytest.raises(IntegrityError) as excinfo:
            verify_installation()
        assert "RECORD hash" in str(excinfo.value)
    finally:
        target.write_bytes(original)
    verify_installation()


def test_wrong_pinned_revision_is_rejected(monkeypatch):
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest["eodms_cli"]["rev"] = "0" * 40
    original = MANIFEST_PATH.read_bytes()
    try:
        MANIFEST_PATH.write_text(json.dumps(manifest), encoding="utf-8")
        with pytest.raises(IntegrityError) as excinfo:
            verify_installation()
        assert "eodms_cli" in str(excinfo.value)
    finally:
        MANIFEST_PATH.write_bytes(original)
    verify_installation()


def test_missing_manifest_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "acquisition.integrity.MANIFEST_PATH", tmp_path / "absent-manifest.json"
    )
    with pytest.raises(IntegrityError) as excinfo:
        verify_installation()
    assert "manifest missing" in str(excinfo.value)


def test_require_pinned_revision_wraps_integrity_errors(monkeypatch):
    def boom():
        raise IntegrityError("eodms/config.py fails its RECORD hash")

    monkeypatch.setattr(wrapper_cli, "verify_installation", boom)
    with pytest.raises(Exception) as excinfo:
        wrapper_cli.require_pinned_revision()
    assert "RECORD hash" in str(excinfo.value)
    assert "Refusing to import" in str(excinfo.value)


def test_write_manifest_is_deterministic(tmp_path):
    first = write_manifest(tmp_path / "m1.json")
    second = write_manifest(tmp_path / "m2.json")
    assert first.read_bytes() == second.read_bytes()
    payload = json.loads(first.read_text(encoding="utf-8"))
    assert payload["eodms_cli"]["rev"] == wrapper_cli.EODMS_CLI_REV
    assert "eodms-py" in payload["distributions"]


def test_integrity_runs_before_the_prompt_ordering(monkeypatch):
    order: list[str] = []
    monkeypatch.setattr(
        "acquisition.integrity._verify_source_tree",
        lambda: (order.append("integrity") or wrapper_cli.EODMS_CLI_REV),
    )
    monkeypatch.setattr(
        "acquisition.integrity._verify_distributions",
        lambda manifest: (order.append("distributions") or 1),
    )
    monkeypatch.setattr(
        "acquisition.integrity._verify_pins",
        lambda manifest: order.append("pins"),
    )
    from helpers import import_upstream

    real_module = import_upstream()

    def fake_load():
        order.append("import")
        return real_module

    def boom(*args, **kwargs):
        order.append("prompt")
        raise AssertionError("stop after ordering")

    monkeypatch.setattr(wrapper_cli, "load_upstream", fake_load)
    with pytest.raises(AssertionError):
        wrapper_cli._run_upstream(
            ["search"],
            anonymous=False,
            username_prompt=boom,
            password_prompt=boom,
        )
    assert order == ["integrity", "pins", "distributions", "import", "prompt"]