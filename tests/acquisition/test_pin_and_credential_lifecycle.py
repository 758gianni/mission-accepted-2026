"""Tests for the fail-closed pin check and credential-default lifecycle.

Both are security controls that must hold *before* a user is prompted, so they
are tested with the prompt functions wired to fail loudly.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from acquisition import cli as wrapper_cli
from acquisition.integrity import IntegrityError
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


def _break_integrity(monkeypatch, detail: str, order=None):
    def boom():
        if order is not None:
            order.append("integrity")
        raise IntegrityError(detail)

    monkeypatch.setattr(wrapper_cli, "verify_installation", boom)


def test_require_pinned_revision_passes_for_the_pinned_install(upstream):
    assert wrapper_cli.require_pinned_revision() == PINNED_REV


@pytest.mark.parametrize(
    "detail, expect",
    [
        (
            "installed eodms-cli revision 0000000000000000000000000000000000000000 != pinned "
            + PINNED_REV,
            "0000000000000000000000000000000000000000",
        ),
        (
            "the pinned source tree is not pristine (1 modified/untracked entry:  M eodms_cli.py)",
            "not pristine",
        ),
        ("eodms/config.py fails its RECORD hash", "RECORD hash"),
    ],
)
def test_require_pinned_revision_fails_closed(monkeypatch, detail, expect):
    _break_integrity(monkeypatch, detail)
    with pytest.raises(Exception) as excinfo:
        wrapper_cli.require_pinned_revision()
    assert expect in str(excinfo.value)
    assert "Refusing to import" in str(excinfo.value)


def test_search_refuses_before_prompting_when_the_pin_is_wrong(monkeypatch, tmp_path):
    _boom_prompt(monkeypatch)
    _break_integrity(monkeypatch, "eodms/config.py fails its RECORD hash")
    recorded = record_upstream_argv(monkeypatch)
    result = CliRunner().invoke(wrapper_cli.main, [*SEARCH_ARGS, "--output", str(tmp_path / "r.geojson")])
    assert result.exit_code != 0
    assert "RECORD hash" in result.output
    assert recorded == [], "upstream must not be invoked when the integrity check fails"


def test_download_refuses_before_prompting_when_the_pin_is_wrong(
    monkeypatch, tmp_path, fake_aaa
):
    _boom_prompt(monkeypatch)
    _break_integrity(monkeypatch, "the pinned source tree is not pristine (1 untracked file)")
    scenes = tmp_path / "selected-scenes.geojson"
    scenes.write_text('{"type": "FeatureCollection", "features": [{"id": "a"}]}')
    recorded = record_upstream_argv(monkeypatch)
    result = CliRunner().invoke(
        wrapper_cli.main, ["download", "--scenes", str(scenes)]
    )
    assert result.exit_code != 0
    assert "not pristine" in result.output
    assert recorded == []


def test_anonymous_search_also_enforces_the_integrity_gate(monkeypatch, tmp_path):
    _boom_prompt(monkeypatch)
    _break_integrity(monkeypatch, "injected_module.py is untracked")
    result = CliRunner().invoke(
        wrapper_cli.main, [*SEARCH_ARGS, "--output", str(tmp_path / "r.geojson"), "--anonymous"]
    )
    assert result.exit_code != 0
    assert "injected_module.py" in result.output


def test_pin_check_runs_before_the_prompt_on_a_good_pin(monkeypatch, tmp_path, fake_aaa):
    order: list[str] = []
    monkeypatch.setattr(
        wrapper_cli,
        "verify_installation",
        lambda: (order.append("integrity") or {"revision": PINNED_REV, "packages_verified": 1}),
    )

    def username_prompt(*args, **kwargs):
        order.append("username-prompt")
        return SENTINEL_USER

    def password_prompt(*args, **kwargs):
        order.append("password-prompt")
        return SENTINEL_PASS

    monkeypatch.setattr(wrapper_cli, "prompt_username", username_prompt)
    monkeypatch.setattr(wrapper_cli, "prompt_password", password_prompt)
    record_upstream_argv(monkeypatch)
    result = CliRunner().invoke(
        wrapper_cli.main, [*SEARCH_ARGS, "--output", str(tmp_path / "r.geojson")]
    )
    assert result.exit_code == 0, result.output
    assert "integrity" in order, order
    assert order.index("integrity") == 0, "integrity must be checked first"
    assert "username-prompt" in order and "password-prompt" in order, order
    assert order.index("integrity") < order.index("username-prompt")


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
