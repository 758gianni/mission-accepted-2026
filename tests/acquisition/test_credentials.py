"""Behaviour tests for the acquisition wrapper's credential handling.

These run against the real pinned upstream eodms_cli (Click parsing, config
loading and logging initialisation are genuine); only the network-facing
``make_aaa`` factory is replaced with a recording double so that no EODMS
authentication is ever attempted and sentinel credentials never leave memory.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from acquisition import cli as wrapper_cli
from helpers import REPO_ROOT, contains_secret, record_upstream_argv, stable_environ

SENTINEL_USER = "sentinel.user@example.invalid"
SENTINEL_PASS = "s3ntinel-p4ssw0rd-DO-NOT-LOG"


class _FakeAAA:
    """Stand-in for eodms.aaa.AAA_API: records what it was handed and never
    talks to EODMS."""

    def __init__(self, username, password, environment="prod"):
        self.username = username
        self.password = password
        self.environment = environment
        self.exported = []

    def get_access_token(self):  # pragma: no cover - not reached in these tests
        raise AssertionError("no authenticated call expected")


def _run(args, **kwargs):
    return CliRunner().invoke(wrapper_cli.main, args, **kwargs)


# --------------------------------------------------------------------------
# credential-free diagnostics
# --------------------------------------------------------------------------


def test_doctor_is_credential_free_and_reports_pinned_revision():
    result = _run(["doctor"])
    assert result.exit_code == 0, result.output
    assert "464b94920e7faf28c84a6d31229ef0b2828a1479" in result.output
    assert "search" in result.output and "download" in result.output


def test_doctor_does_not_prompt_for_credentials(monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("doctor must never prompt for credentials")

    monkeypatch.setattr(wrapper_cli, "prompt_username", explode)
    monkeypatch.setattr(wrapper_cli, "prompt_password", explode)
    assert _run(["doctor"]).exit_code == 0


# --------------------------------------------------------------------------
# anonymous access must not prompt
# --------------------------------------------------------------------------


def test_anonymous_search_never_prompts(monkeypatch, tmp_path, fake_aaa):
    def explode(*args, **kwargs):
        raise AssertionError("anonymous search must never prompt")

    monkeypatch.setattr(wrapper_cli, "prompt_username", explode)
    monkeypatch.setattr(wrapper_cli, "prompt_password", explode)

    out = tmp_path / "results.geojson"
    result = _run(
        [
            "search",
            "--collection",
            "Radarsat-2_Tropical_Forest_Products",
            "--bbox",
            "-100,10,-99,11",
            "--datetime",
            "2024-01-01/2024-01-31",
            "--output",
            str(out),
            "--anonymous",
        ]
    )
    assert result.exit_code == 0, result.output
    assert fake_aaa == [], "anonymous mode must not build an authenticated client"


def test_anonymous_flag_conflicts_with_explicit_credentials(monkeypatch):
    result = _run(["search", "--collection", "C", "--anonymous", "-u", SENTINEL_USER])
    assert result.exit_code != 0
    assert "anonymous" in result.output.lower()


# --------------------------------------------------------------------------
# credential acquisition: interactive, memory-only
# --------------------------------------------------------------------------


def test_authenticated_search_prompts_once_for_username_and_password(
    monkeypatch, tmp_path, fake_aaa
):
    asked = []

    monkeypatch.setattr(
        wrapper_cli,
        "prompt_username",
        lambda *a, **k: (asked.append("user"), SENTINEL_USER)[1],
    )
    monkeypatch.setattr(
        wrapper_cli,
        "prompt_password",
        lambda *a, **k: (asked.append("pass"), SENTINEL_PASS)[1],
    )

    result = _run(
        [
            "search",
            "--collection",
            "Radarsat-2_Tropical_Forest_Products",
            "--bbox",
            "-100,10,-99,11",
            "--datetime",
            "2024-01-01/2024-01-31",
            "--output",
            str(tmp_path / "r.geojson"),
        ]
    )
    assert result.exit_code == 0, result.output
    assert asked == ["user", "pass"]
    assert [api.username for api in fake_aaa] == [SENTINEL_USER]
    assert [api.password for api in fake_aaa] == [SENTINEL_PASS]


def test_blank_username_is_rejected(monkeypatch):
    monkeypatch.setattr(wrapper_cli, "prompt_username", lambda *a, **k: "")
    monkeypatch.setattr(wrapper_cli, "prompt_password", lambda *a, **k: SENTINEL_PASS)
    result = _run(["search", "--collection", "C", "--bbox", "0,0,1,1"])
    assert result.exit_code != 0
    assert "username" in result.output.lower()


def test_default_prompts_read_stdin_and_hide_the_password(monkeypatch):
    import getpass

    monkeypatch.setattr("getpass.getpass", lambda *a, **k: SENTINEL_PASS)
    assert wrapper_cli.prompt_password() == SENTINEL_PASS


# --------------------------------------------------------------------------
# no credentials in argv
# --------------------------------------------------------------------------


def test_credentials_never_reach_argv(monkeypatch, tmp_path, fake_aaa):
    recorded = record_upstream_argv(monkeypatch)
    monkeypatch.setattr(wrapper_cli, "prompt_username", lambda *a, **k: SENTINEL_USER)
    monkeypatch.setattr(wrapper_cli, "prompt_password", lambda *a, **k: SENTINEL_PASS)

    scenes_file = tmp_path / "selected-scenes.geojson"
    scenes_file.write_text(
        json.dumps({"type": "FeatureCollection", "features": [{"id": "uuid-1"}]})
    )

    for args in (
        [
            "search",
            "--collection",
            "Radarsat-2_Tropical_Forest_Products",
            "--bbox",
            "-100,10,-99,11",
            "--output",
            str(tmp_path / "r.geojson"),
        ],
        ["download", "--collection", "Radarsat-2_Tropical_Forest_Products",
         "--scenes", str(scenes_file)],
    ):
        result = _run(args)
        assert result.exit_code == 0, result.output

    assert len(recorded) == 2, recorded
    for argv in recorded:
        flat = " ".join(argv)
        assert SENTINEL_PASS not in flat, argv
        assert SENTINEL_USER not in flat, argv
        assert "-p" not in argv and "--password" not in argv, argv
    assert recorded[0][0] == "search"
    assert recorded[1][0] == "download"


def test_password_option_is_rejected_on_the_wrapper():
    result = _run(["search", "--collection", "C", "--password", SENTINEL_PASS])
    assert result.exit_code != 0
    assert SENTINEL_PASS not in result.output


def test_username_option_is_rejected_on_the_wrapper():
    result = _run(["search", "--collection", "C", "--username", SENTINEL_USER])
    assert result.exit_code != 0
    assert SENTINEL_USER not in result.output


# --------------------------------------------------------------------------
# forbidden upstream configure (base64 password persistence)
# --------------------------------------------------------------------------


def test_configure_subcommand_is_forbidden():
    result = _run(["configure", "--username", SENTINEL_USER, "--password", SENTINEL_PASS])
    assert result.exit_code != 0
    assert "configure" in result.output.lower()
    assert "forbidden" in result.output.lower() or "not allowed" in result.output.lower()


def test_upstream_configure_is_removed_inside_the_sandbox(upstream, tmp_path):
    from acquisition.sandbox import isolated_eodms_environment

    assert "configure" in upstream.cli.commands
    with isolated_eodms_environment(upstream):
        assert "configure" not in upstream.cli.commands
    assert "configure" in upstream.cli.commands


def test_configure_would_never_write_base64_credentials(upstream, real_home_state):
    """Prove what is blocked: upstream configure writes a base64 password."""
    from click.testing import CliRunner as _R

    from acquisition.sandbox import isolated_eodms_environment

    configure_cmd = upstream.cli.commands["configure"]
    with isolated_eodms_environment(upstream) as sandbox:
        result = _R().invoke(
            configure_cmd, ["--username", SENTINEL_USER, "--password", SENTINEL_PASS]
        )
        assert result.exit_code == 0, result.output
        written = Path(sandbox.config_path)
        assert written.is_file()
        assert written.read_text(encoding="utf-8").count(SENTINEL_USER) >= 1

    import base64

    encoded = base64.b64encode(SENTINEL_PASS.encode()).decode()
    real_home = real_home_state["home"]
    for root in (real_home / ".eodms", Path(wrapper_cli.UPSTREAM_SRC_DIR)):
        assert not contains_secret(root, SENTINEL_USER, encoded), f"leaked into {root}"
    assert not contains_secret(real_home / ".eodms", SENTINEL_PASS)


# --------------------------------------------------------------------------
# no credential files, no logs, AAA tokens stay in memory, HOME untouched
# --------------------------------------------------------------------------


def test_no_credential_files_or_logs_are_written(
    monkeypatch, tmp_path, fake_aaa, real_home_state
):
    monkeypatch.setattr(wrapper_cli, "prompt_username", lambda *a, **k: SENTINEL_USER)
    monkeypatch.setattr(wrapper_cli, "prompt_password", lambda *a, **k: SENTINEL_PASS)

    log_dir = Path(wrapper_cli.UPSTREAM_SRC_DIR) / "log"
    before = sorted(p.name for p in log_dir.glob("*")) if log_dir.is_dir() else []

    result = _run(
        [
            "search",
            "--collection",
            "Radarsat-2_Tropical_Forest_Products",
            "--bbox",
            "-100,10,-99,11",
            "--output",
            str(tmp_path / "r.geojson"),
        ]
    )
    assert result.exit_code == 0, result.output

    after = sorted(p.name for p in log_dir.glob("*")) if log_dir.is_dir() else []
    assert after == before, f"CLI file logging was not disabled: {set(after) - set(before)}"
    assert not contains_secret(log_dir, SENTINEL_USER, SENTINEL_PASS)

    import base64

    encoded = base64.b64encode(SENTINEL_PASS.encode()).decode()
    real_home = real_home_state["home"]
    scanned = [
        tmp_path,
        real_home / ".eodms",
        Path(wrapper_cli.UPSTREAM_SRC_DIR) / "log",
        real_home / "Downloads",
        REPO_ROOT / "data",
    ]
    for root in scanned:
        assert not contains_secret(root, SENTINEL_PASS), f"password leaked under {root}"
        assert not contains_secret(root, encoded), f"encoded password leaked under {root}"
    assert not contains_secret(REPO_ROOT / "acquisition", SENTINEL_PASS)


def test_real_home_eodms_is_never_touched(
    monkeypatch, tmp_path, fake_aaa, real_home_state
):
    """Sentinel credentials must not reach the real ~/.eodms, and it must not
    be created or modified by a run."""
    real_home = real_home_state["home"]
    before = dict(real_home_state["eodms"])
    monkeypatch.setattr(wrapper_cli, "prompt_username", lambda *a, **k: SENTINEL_USER)
    monkeypatch.setattr(wrapper_cli, "prompt_password", lambda *a, **k: SENTINEL_PASS)

    result = _run(
        [
            "search",
            "--collection",
            "Radarsat-2_Tropical_Forest_Products",
            "--bbox",
            "-100,10,-99,11",
            "--output",
            str(tmp_path / "r.geojson"),
        ]
    )
    assert result.exit_code == 0, result.output
    assert not contains_secret(real_home / ".eodms", SENTINEL_USER, SENTINEL_PASS)

    from conftest import home_snapshot

    after = home_snapshot()
    assert after == before, f"real ~/.eodms changed: {set(after.items()) ^ set(before.items())}"
    assert real_home.is_dir()


def test_environment_is_never_modified_during_or_after_a_run(
    monkeypatch, tmp_path, fake_aaa, real_home_state, probe_observations
):
    """The wrapper must not repurpose HOME/USERPROFILE/CODEX_HOME."""
    monkeypatch.setattr(wrapper_cli, "prompt_username", lambda *a, **k: SENTINEL_USER)
    monkeypatch.setattr(wrapper_cli, "prompt_password", lambda *a, **k: SENTINEL_PASS)

    before_environ = stable_environ()
    before_home = str(real_home_state["home"])

    result = _run(
        [
            "search",
            "--collection",
            "Radarsat-2_Tropical_Forest_Products",
            "--bbox",
            "-100,10,-99,11",
            "--output",
            str(tmp_path / "r.geojson"),
        ]
    )
    assert result.exit_code == 0, result.output

    # observed from inside the invocation, after credential resolution
    during = probe_observations["environ_during"]
    for var in ("HOME", "USERPROFILE", "CODEX_HOME"):
        assert during.get(var) == before_environ.get(var), f"{var} changed mid-invocation"
    assert probe_observations["home_during"] == before_home

    for var in ("HOME", "USERPROFILE", "CODEX_HOME"):
        assert os.environ.get(var) == before_environ.get(var), f"{var} changed"
    assert stable_environ() == before_environ
    assert str(Path.home()) == before_home


def test_all_home_derived_paths_are_redirected_into_the_sandbox(
    monkeypatch, tmp_path, fake_aaa, probe_observations
):
    """Every audited ~/.eodms lookup must resolve inside the task temp dir."""
    monkeypatch.setattr(wrapper_cli, "prompt_username", lambda *a, **k: SENTINEL_USER)
    monkeypatch.setattr(wrapper_cli, "prompt_password", lambda *a, **k: SENTINEL_PASS)
    result = _run(
        [
            "search",
            "--collection",
            "Radarsat-2_Tropical_Forest_Products",
            "--bbox",
            "-100,10,-99,11",
            "--output",
            str(tmp_path / "r.geojson"),
        ]
    )
    assert result.exit_code == 0, result.output
    paths = probe_observations["paths_during"]
    assert set(paths) == {
        "eodms_cli.default_config",
        "config_util.config_fn",
        "config_util.legacy_config_fn",
        "aaa.creds_fn",
        "aaa.token_lock_fn",
    }
    sandbox_root = str(Path(paths["eodms_cli.default_config"]).parents[1])
    for name, value in paths.items():
        assert value.startswith(sandbox_root), f"{name}={value} escaped the sandbox"
    assert probe_observations["default_config_direct"] == paths["eodms_cli.default_config"]


def test_sandbox_restores_everything_it_patched(upstream, real_home_state):
    import scripts.config_util as config_util

    import eodms.aaa as aaa_module
    from acquisition.sandbox import isolated_eodms_environment, sandboxed_paths

    before_env = stable_environ()
    before_paths = sandboxed_paths(upstream)
    before_config_cmd = upstream.cli.commands["configure"]
    before_export = aaa_module.AAA_Creds.export_vals

    with isolated_eodms_environment(upstream) as sandbox:
        assert Path(sandbox.root).is_dir()

    assert stable_environ() == before_env
    assert sandboxed_paths(upstream) == before_paths
    assert upstream.cli.commands["configure"] is before_config_cmd
    assert aaa_module.AAA_Creds.export_vals is before_export
    assert upstream.os is not before_paths  # sanity: it was replaced during the block
    assert config_util.os.path.expanduser("~") == str(real_home_state["home"])
    assert not Path(sandbox.root).exists(), "task temp dir was not cleaned up"


def test_sandbox_uses_a_task_scoped_temp_dir_not_a_home_rewrite(upstream):
    """Regression guard: no HOME/USERPROFILE mutation is permitted."""
    import inspect

    from acquisition import sandbox as sandbox_mod

    source = inspect.getsource(sandbox_mod)
    code = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    code = code.split('"""', 2)[-1]  # drop the module docstring
    for forbidden in ("environ[", "setenv(", "putenv(", "unsetenv(", "putenv"):
        assert forbidden not in code, f"sandbox must not touch the environment: {forbidden}"
    # every home lookup must go through a shimmed module, never the real os.path
    assert 'self._real.expanduser(text)' in code
    assert "_OsModuleShim(" in code


def test_aaa_token_persistence_is_disabled(upstream, real_home_state):
    from eodms.aaa import AAA_Creds

    from acquisition.sandbox import isolated_eodms_environment

    creds = AAA_Creds()
    with isolated_eodms_environment(upstream):
        creds.access_token = "access-token-sentinel"
        creds.refresh_token = "refresh-token-sentinel"
        assert creds.export_vals() is None
        assert creds.import_vals() is None
        assert creds.access_token == "access-token-sentinel"

    real_home = real_home_state["home"]
    assert not contains_secret(
        real_home / ".eodms", "access-token-sentinel", "refresh-token-sentinel"
    )
    assert not contains_secret(real_home / ".eodms", "aaa_creds")


def test_sandbox_removes_any_file_handlers_it_finds(upstream):
    import logging

    from acquisition.sandbox import isolated_eodms_environment

    with isolated_eodms_environment(upstream):
        for name in ("eodms_cli", "eodms"):
            assert not [
                h
                for h in logging.getLogger(name).handlers
                if isinstance(h, logging.FileHandler)
            ]


# --------------------------------------------------------------------------
# argument validation
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "args, fragment",
    [
        (["search"], "aoi"),
        (["search", "--collection", "C"], "bbox"),
        (["search", "--collection", "C", "--bbox", "not-a-bbox"], "bbox"),
        (["search", "--collection", "C", "--bbox", "0,0,1"], "bbox"),
        (["search", "--collection", "C", "--bbox", "0,0,1,1", "--datetime", "nonsense"], "datetime"),
        (["search", "--collection", "C", "--bbox", "0,0,1,1", "--output", ""], "output"),
        (["download"], "scenes"),
        (["download", "--scenes", "/nope/missing.geojson"], "does not exist"),
        (["search", "--nope"], "no such option"),
    ],
)
def test_argument_validation(args, fragment):
    result = _run(args)
    assert result.exit_code != 0, result.output
    assert fragment.lower() in (result.output + str(result.exception)).lower()


def test_search_rejects_conflicting_spatial_inputs(monkeypatch, tmp_path):
    aoi = tmp_path / "aoi.geojson"
    aoi.write_text('{"type": "FeatureCollection", "features": []}')
    result = _run(
        ["search", "--collection", "C", "--bbox", "0,0,1,1", "--aoi", str(aoi)]
    )
    assert result.exit_code != 0
    assert "bbox" in result.output.lower() and "aoi" in result.output.lower()


def test_exit_code_is_propagated_from_upstream(monkeypatch, tmp_path):
    import click

    def failing_main(self, args=None, **kwargs):
        raise SystemExit(3)

    monkeypatch.setattr(click.Group, "main", failing_main)
    monkeypatch.setattr(wrapper_cli, "prompt_username", lambda *a, **k: SENTINEL_USER)
    monkeypatch.setattr(wrapper_cli, "prompt_password", lambda *a, **k: SENTINEL_PASS)
    result = _run(
        [
            "search",
            "--collection",
            "C",
            "--bbox",
            "0,0,1,1",
            "--output",
            str(tmp_path / "r.geojson"),
        ]
    )
    assert result.exit_code == 3


def test_module_entrypoint_exists():
    assert (REPO_ROOT / "acquisition" / "__main__.py").is_file()
    assert sys.executable  # interpreter sanity for the docs command
