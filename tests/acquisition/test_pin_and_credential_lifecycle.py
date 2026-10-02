"""Tests for the fail-closed pin check and credential-default lifecycle.

Both are security controls that must hold *before* a user is prompted, so they
are tested with the prompt functions wired to fail loudly.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from acquisition import cli as wrapper_cli
from helpers import PINNED_REV, record_upstream_argv

SENTINEL_USER = "pin.sentinel@example.invalid"
SENTINEL_PASS = "p1n-s3ntinel-p4ssw0rd-DO-NOT-LOG"


def _boom_prompt(monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("credentials must not be requested before the pin check")

    monkeypatch.setattr(wrapper_cli, "prompt_username", explode)
    monkeypatch.setattr(wrapper_cli, "prompt_password", explode)


def _stub_prompts(monkeypatch):
    monkeypatch.setattr(wrapper_cli, "prompt_username", lambda *a, **k: SENTINEL_USER)
    monkeypatch.setattr(wrapper_cli, "prompt_password", lambda *a, **k: SENTINEL_PASS)


SEARCH_ARGS = [
    "search",
    "--collection",
    "Radarsat-2_Tropical_Forest_Products",
    "--bbox",
    "-100,10,-99,11",
    "--output",
    "unused.geojson",
]


def test_require_pinned_revision_passes_for_the_pinned_install(upstream):
    assert wrapper_cli.require_pinned_revision() == PINNED_REV


@pytest.mark.parametrize(
    "reported", ["0000000000000000000000000000000000000000", "unknown (source tree has no .git)"]
)
def test_require_pinned_revision_fails_closed(monkeypatch, reported):
    monkeypatch.setattr(wrapper_cli, "_upstream_revision", lambda: reported)
    with pytest.raises(Exception) as excinfo:
        wrapper_cli.require_pinned_revision()
    assert PINNED_REV in str(excinfo.value)
    assert "Refusing" in str(excinfo.value)


def test_search_refuses_before_prompting_when_the_pin_is_wrong(monkeypatch, tmp_path):
    _boom_prompt(monkeypatch)
    monkeypatch.setattr(wrapper_cli, "_upstream_revision", lambda: "deadbeef" * 5)
    recorded = record_upstream_argv(monkeypatch)
    result = CliRunner().invoke(wrapper_cli.main, [*SEARCH_ARGS, "--output", str(tmp_path / "r.geojson")])
    assert result.exit_code != 0
    assert PINNED_REV in result.output
    assert recorded == [], "upstream must not be invoked when the pin check fails"


def test_download_refuses_before_prompting_when_the_pin_is_wrong(
    monkeypatch, tmp_path, fake_aaa
):
    _boom_prompt(monkeypatch)
    monkeypatch.setattr(wrapper_cli, "_upstream_revision", lambda: "deadbeef" * 5)
    scenes = tmp_path / "selected-scenes.geojson"
    scenes.write_text('{"type": "FeatureCollection", "features": [{"id": "a"}]}')
    recorded = record_upstream_argv(monkeypatch)
    result = CliRunner().invoke(
        wrapper_cli.main, ["download", "--scenes", str(scenes)]
    )
    assert result.exit_code != 0
    assert PINNED_REV in result.output
    assert recorded == []


def test_anonymous_search_also_enforces_the_pin(monkeypatch, tmp_path):
    _boom_prompt(monkeypatch)
    monkeypatch.setattr(wrapper_cli, "_upstream_revision", lambda: "deadbeef" * 5)
    result = CliRunner().invoke(
        wrapper_cli.main, [*SEARCH_ARGS, "--output", str(tmp_path / "r.geojson"), "--anonymous"]
    )
    assert result.exit_code != 0
    assert PINNED_REV in result.output


def test_pin_check_runs_before_the_prompt_on_a_good_pin(monkeypatch, tmp_path, fake_aaa):
    order: list[str] = []
    monkeypatch.setattr(wrapper_cli, "_upstream_revision", lambda: PINNED_REV)
    def username_prompt(*args, **kwargs):
        order.append("username-prompt")
        return SENTINEL_USER

    def password_prompt(*args, **kwargs):
        order.append("password-prompt")
        return SENTINEL_PASS

    monkeypatch.setattr(wrapper_cli, "prompt_username", username_prompt)
    monkeypatch.setattr(wrapper_cli, "prompt_password", password_prompt)
    monkeypatch.setattr(
        wrapper_cli,
        "_upstream_revision",
        lambda: (order.append("pin-check") or PINNED_REV),
    )
    record_upstream_argv(monkeypatch)
    result = CliRunner().invoke(
        wrapper_cli.main, [*SEARCH_ARGS, "--output", str(tmp_path / "r.geojson")]
    )
    assert result.exit_code == 0, result.output
    assert order == ["pin-check", "username-prompt", "password-prompt"]


# --------------------------------------------------------------------------
# injected Click credential defaults must not survive the invocation
# --------------------------------------------------------------------------


def _credential_params(upstream, command_name: str):
    command = upstream.cli.commands[command_name]
    return {p.name: p for p in command.params if p.name in ("username", "password")}


def test_injected_defaults_are_restored_even_when_upstream_raises(upstream):
    params = _credential_params(upstream, "search")
    before = {name: p.default for name, p in params.items()}
    restore = wrapper_cli._apply_credential_defaults(
        upstream.cli.commands["search"], SENTINEL_USER, SENTINEL_PASS
    )
    assert {name: p.default for name, p in params.items()} == {
        "username": SENTINEL_USER,
        "password": SENTINEL_PASS,
    }
    restore()
    assert {name: p.default for name, p in params.items()} == before


def test_defaults_are_restored_when_the_upstream_invocation_exits(
    monkeypatch, tmp_path, fake_aaa, upstream
):
    params = _credential_params(upstream, "search")
    before = {name: p.default for name, p in params.items()}
    _stub_prompts(monkeypatch)
    record_upstream_argv(monkeypatch)

    assert (
        CliRunner().invoke(
            wrapper_cli.main, [*SEARCH_ARGS, "--output", str(tmp_path / "r.geojson")]
        ).exit_code
        == 0
    )
    assert {name: p.default for name, p in params.items()} == before


def test_a_later_anonymous_run_cannot_reuse_earlier_credentials(
    monkeypatch, tmp_path, fake_aaa, upstream
):
    """The concrete leak this guards: anonymous mode must not inherit a password."""
    params = _credential_params(upstream, "search")
    recorded = record_upstream_argv(monkeypatch)
    _stub_prompts(monkeypatch)
    assert (
        CliRunner().invoke(
            wrapper_cli.main, [*SEARCH_ARGS, "--output", str(tmp_path / "auth.geojson")]
        ).exit_code
        == 0
    )
    anonymous = CliRunner().invoke(
        wrapper_cli.main,
        [*SEARCH_ARGS, "--output", str(tmp_path / "anon.geojson"), "--anonymous"],
        env={"EODMS_PROBE": "1"},
    )
    assert anonymous.exit_code == 0, anonymous.output
    assert all(SENTINEL_PASS not in " ".join(argv) for argv in recorded)
    assert {name: p.default for name, p in params.items()} == {
        "username": None,
        "password": None,
    }


def test_download_defaults_are_restored_across_uuid_invocations(
    monkeypatch, tmp_path, fake_aaa, upstream
):
    params = _credential_params(upstream, "download")
    before = {name: p.default for name, p in params.items()}
    _stub_prompts(monkeypatch)
    recorded = record_upstream_argv(monkeypatch)
    result = CliRunner().invoke(
        wrapper_cli.main,
        ["download", "--collection", "C", "--uuid", "a,b", "--output-dir", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    assert len(recorded) == 2
    assert {name: p.default for name, p in params.items()} == before
