"""Multi-item download loop must survive real Click invocation semantics.

``upstream.cli.main(standalone_mode=True)`` raises ``SystemExit(0)`` after a
*successful* command. The wrapper loops once per UUID, so that exit must not
abort the loop - while a non-zero exit or a ``ClickException`` must still abort
it and reach the shell.

These regressions drive the **real** ``click.Group.main`` of the pinned CLI: the
command *body* is a recording stub installed on the real group, but Click's
argument parsing, standalone-mode handling and exit codes are genuine.
"""

from __future__ import annotations

import click
import pytest
from click.testing import CliRunner

from acquisition import cli as wrapper_cli
from helpers import import_upstream, record_upstream_argv


def _stub_prompts(monkeypatch):
    monkeypatch.setattr(wrapper_cli, "prompt_username", lambda *a, **k: "loop.sentinel")
    monkeypatch.setattr(wrapper_cli, "prompt_password", lambda *a, **k: "loop-sentinel-pass")


def _make_command(name, calls, *, fail=False):
    """A genuine Click command whose body only records what it was invoked with."""

    @click.command(name, context_settings={"help_option_names": ["-h", "--help"]})
    @click.option("-c", "--collection")
    @click.option("-l", "--limit", type=int)
    @click.option("-e", "--env")
    @click.option("--uuid")
    @click.option("--input")
    @click.option("--dl_dir")
    def command(collection, limit, env, uuid, input, dl_dir):
        calls.append({"uuid": uuid, "input": input, "dl_dir": dl_dir, "limit": limit})
        if fail:
            raise click.ClickException(f"upstream refused uuid={uuid}")
        click.echo(f"downloaded {uuid}")

    return command


@pytest.fixture
def real_group(monkeypatch):
    """Install a recording command as the pinned group's real ``download``."""
    upstream = import_upstream()
    original = upstream.cli.commands["download"]
    return upstream, original


def _install(upstream, original, monkeypatch, calls, exit_code=0):
    upstream.cli.commands["download"] = _make_command(
        "download", calls, fail=bool(exit_code)
    )
    monkeypatch.setattr(wrapper_cli, "load_upstream", lambda: upstream)
    return upstream


@pytest.mark.parametrize(
    "uuids, expected",
    [
        (["uuid-a,uuid-b"], ["uuid-a", "uuid-b"]),
        (["uuid-a", "uuid-b"], ["uuid-a", "uuid-b"]),
        (["uuid-a,uuid-b,uuid-a"], ["uuid-a", "uuid-b"]),
    ],
)
def test_every_uuid_is_invoked_through_real_click(monkeypatch, tmp_path, real_group, uuids, expected):
    upstream, original = real_group
    calls: list[dict] = []
    _install(upstream, original, monkeypatch, calls)
    _stub_prompts(monkeypatch)
    try:
        args = ["download", "--collection", "C"]
        for value in uuids:
            args += ["--uuid", value]
        result = CliRunner().invoke(
            wrapper_cli.main, [*args, "--output-dir", str(tmp_path / "raw")]
        )
    finally:
        upstream.cli.commands["download"] = original
    assert result.exit_code == 0, result.output
    assert [call["uuid"] for call in calls] == expected
    assert result.output.count("downloaded ") == len(expected)


def test_a_failing_uuid_stops_the_loop_and_propagates(monkeypatch, tmp_path, real_group):
    upstream, original = real_group
    calls: list[dict] = []
    _install(upstream, original, monkeypatch, calls, exit_code=1)
    _stub_prompts(monkeypatch)
    try:
        result = CliRunner().invoke(
            wrapper_cli.main,
            [
                "download",
                "--collection",
                "C",
                "--uuid",
                "uuid-a,uuid-b",
                "--output-dir",
                str(tmp_path / "raw"),
            ],
        )
    finally:
        upstream.cli.commands["download"] = original
    assert result.exit_code != 0, "a non-zero upstream exit must not be hidden"
    assert len(calls) == 1, "the loop must abort on the first failure"
    assert "upstream refused uuid=uuid-a" in result.output


def test_successful_exit_codes_do_not_abort_the_loop(monkeypatch, tmp_path, real_group):
    """``SystemExit(0)`` per invocation is normal Click behaviour, not a stop."""
    upstream, original = real_group
    calls: list[dict] = []

    upstream.cli.commands["download"] = _make_command("download", calls)
    monkeypatch.setattr(wrapper_cli, "load_upstream", lambda: upstream)
    _stub_prompts(monkeypatch)
    try:
        result = CliRunner().invoke(
            wrapper_cli.main,
            ["download", "--collection", "C", "--uuid", "u1,u2,u3",
             "--output-dir", str(tmp_path / "raw")],
        )
    finally:
        upstream.cli.commands["download"] = original
    assert result.exit_code == 0, result.output
    assert [call["uuid"] for call in calls] == ["u1", "u2", "u3"]


def test_single_uuid_still_produces_one_invocation(monkeypatch, tmp_path, fake_aaa):
    _stub_prompts(monkeypatch)
    recorded = record_upstream_argv(monkeypatch)
    result = CliRunner().invoke(
        wrapper_cli.main,
        ["download", "--collection", "C", "--uuid", "only-one",
         "--output-dir", str(tmp_path / "raw")],
    )
    assert result.exit_code == 0, result.output
    assert len(recorded) == 1
    assert recorded[0][recorded[0].index("--uuid") + 1] == "only-one"