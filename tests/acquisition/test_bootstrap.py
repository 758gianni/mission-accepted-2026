"""Tests for the install bootstrap script and pinned dependency manifest."""

from __future__ import annotations

import re
import stat
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = REPO_ROOT / "acquisition" / "bootstrap_eodms_cli.sh"
REQUIREMENTS = REPO_ROOT / "requirements-acquisition.txt"
TOOLS_DIR = REPO_ROOT / ".tools" / "eodms-cli"
UPSTREAM_SRC = TOOLS_DIR / "src"
VENV_PYTHON = TOOLS_DIR / ".venv" / "bin" / "python"
PINNED_REV = "464b94920e7faf28c84a6d31229ef0b2828a1479"


def test_bootstrap_script_exists_and_is_executable():
    assert BOOTSTRAP.is_file()
    assert BOOTSTRAP.stat().st_mode & stat.S_IXUSR


def test_bootstrap_pins_the_official_cli_revision_and_url():
    text = BOOTSTRAP.read_text(encoding="utf-8")
    assert PINNED_REV in text
    assert "https://github.com/eodms-sgdot/eodms-cli.git" in text
    assert re.search(r"EODMS_CLI_REV=\"[0-9a-f]{40}\"", text)


def test_bootstrap_installs_into_ignored_tools_dir():
    """The install tree must stay out of git, via the root .gitignore (lead-owned)."""
    import subprocess

    text = BOOTSTRAP.read_text(encoding="utf-8")
    assert '.tools/eodms-cli' in text
    probe = TOOLS_DIR / "src" / "eodms_cli.py"
    proc = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "check-ignore", "-v", str(probe)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        pytest.skip(
            "nothing ignores .tools/ yet -- lead-owned integration item; "
            "expected line in the root .gitignore: /.tools/"
        )
    # Either the root .gitignore (lead-owned, shared) or a local
    # .git/info/exclude may provide the rule; neither is a file we commit.
    source = proc.stdout.split(":", 1)[0]
    assert source.endswith((".gitignore", "info/exclude")), proc.stdout


def test_bootstrap_contains_no_credential_handling():
    text = BOOTSTRAP.read_text(encoding="utf-8")
    for forbidden in ("--password", "--username", "EODMS_PASSWORD", "getpass", "configure", "aaa_creds"):
        assert forbidden not in text, f"bootstrap must not reference {forbidden!r}"


def test_bootstrap_does_a_credential_free_help_check():
    text = BOOTSTRAP.read_text(encoding="utf-8")
    assert "help check" in text
    assert "eodms_cli.cli" in text
    assert "--help" in text


def test_requirements_manifest_pins_wrapper_dependencies():
    text = REQUIREMENTS.read_text(encoding="utf-8")
    assert "click" in text
    assert "pytest" in text
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            assert "==" in line, f"unpinned requirement: {line}"


def test_installed_source_is_at_the_pinned_revision():
    if not (UPSTREAM_SRC / "eodms_cli.py").is_file():
        pytest.skip("pinned eodms-cli source not installed")
    rev_file = UPSTREAM_SRC / ".git" / "HEAD"
    assert rev_file.is_file()
    import subprocess

    head = subprocess.run(
        ["git", "-C", str(UPSTREAM_SRC), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert head == PINNED_REV


def test_venv_interpreter_can_import_pinned_cli():
    if not VENV_PYTHON.is_file():
        pytest.skip("bootstrap venv not installed")
    import subprocess

    proc = subprocess.run(
        [str(VENV_PYTHON), "-W", "ignore", "-c", "import eodms_cli; print('ok')"],
        cwd=str(UPSTREAM_SRC),
        capture_output=True,
        text=True,
        env={"HOME": "/nonexistent-eodms-home", "PATH": "/usr/bin:/bin"},
    )
    assert proc.returncode == 0, proc.stderr
    assert "ok" in proc.stdout
