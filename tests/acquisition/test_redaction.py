"""Signed-URL / credential redaction, proved at the real network boundary.

Simulated signed-URL token sentinels are pushed through the genuine
``requests`` transport boundary - ``HTTPAdapter.send`` raises an
``HTTPError`` whose response URL carries the sentinel - so the exception, the log
records and the download manifest are produced by the real AAA/DDS code paths.
Sockets stay blocked (see ``test_boundary_real_aaa.py``); no authentication and
no real network access occur.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest
import requests
from click.testing import CliRunner

from acquisition import cli as wrapper_cli
from acquisition.redaction import (
    redact_text,
    redact_value,
    scrub_download_manifest,
)
from helpers import contains_secret, import_upstream

TOKEN = "SIGNED-URL-ACCESS-TOKEN-SENTINEL-9f2c"
REFRESH = "SIGNED-URL-REFRESH-TOKEN-SENTINEL-77bd"
BASIC = "Authorization-BASIC-SENTINEL-4a1e"
PASSWORD_SENTINEL = "redaction-s3ntinel-p4ssw0rd-DO-NOT-LOG"
SECRETS = (TOKEN, REFRESH, BASIC, PASSWORD_SENTINEL)

SIGNED_URL = (
    "https://order.eodms-sgdot.nrcan-rncan.gc.ca/rapi/download/"
    f"tmp/{TOKEN}.tif?access_token={TOKEN}&refresh_token={REFRESH}"
)


class _BlockedSocket(RuntimeError):
    pass


@pytest.fixture
def blocked_sockets(monkeypatch):
    def blocked(*args, **kwargs):
        raise _BlockedSocket("sockets are blocked in this test")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)


@pytest.fixture(autouse=True)
def _clean_secret_registry():
    from acquisition.redaction import forget_secrets

    forget_secrets()
    yield
    forget_secrets()


@pytest.fixture
def live_tokens(monkeypatch):
    """Simulated AAA tokens: registered as live secrets, exactly as the sandbox does."""
    from acquisition.redaction import register_secrets

    register_secrets(TOKEN, REFRESH)
    return (TOKEN, REFRESH)


@pytest.fixture
def signed_url_http_error(monkeypatch):
    """Raise HTTPError with a signed-URL sentinel at the genuine HTTP boundary."""
    attempted: list[str] = []

    def fake_send(self, request, **kwargs):  # noqa: ANN001
        attempted.append(request.url)
        response = requests.Response()
        response.status_code = 403
        response.url = request.url
        response.request = request
        response.reason = f"Forbidden for token={TOKEN}"
        error = requests.exceptions.HTTPError(
            f"403 Client Error for url: {request.url} with Bearer {BASIC}", response=response
        )
        raise error

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", fake_send)
    return attempted


# --------------------------------------------------------------------------
# pure redaction behaviour
# --------------------------------------------------------------------------


def test_query_string_tokens_are_redacted():
    redacted = redact_text(f"GET {SIGNED_URL}")
    assert REFRESH not in redacted
    assert "access_token=[REDACTED]" in redacted
    assert "refresh_token=[REDACTED]" in redacted


def test_live_tokens_are_scrubbed_even_when_embedded_in_a_url_path(live_tokens):
    """Signed URLs put the token in the path, where no key=value pattern applies."""
    redacted = redact_text(f"GET {SIGNED_URL}")
    assert TOKEN not in redacted
    assert "[REDACTED].tif" in redacted


def test_bearer_and_basic_material_is_redacted():
    assert BASIC not in redact_text(f"header Authorization: Bearer {BASIC}")
    assert PASSWORD_SENTINEL not in redact_text(f"password={PASSWORD_SENTINEL}")


def test_url_userinfo_is_redacted():
    assert PASSWORD_SENTINEL not in redact_text(
        f"https://user:{PASSWORD_SENTINEL}@example.invalid/a.tif"
    )


def test_structured_values_are_redacted_recursively(live_tokens):
    payload = {
        "id": "item-1",
        "url": SIGNED_URL,
        "nested": [{"token": TOKEN}, {"detail": f"failed for {REFRESH}"}],
        "password": PASSWORD_SENTINEL,
    }
    cleaned = redact_value(payload)
    blob = json.dumps(cleaned)
    for secret in SECRETS:
        assert secret not in blob
    assert cleaned["id"] == "item-1"
    assert cleaned["password"] == "[REDACTED]"


def test_download_manifest_detail_error_and_url_fields_are_scrubbed(tmp_path, live_tokens):
    manifest = tmp_path / "downloads.jsonl"
    manifest.write_text(
        json.dumps(
            {
                "uuid": "item-1",
                "url": SIGNED_URL,
                "detail": f"resume from {SIGNED_URL}",
                "error": f"HTTP 403 token={TOKEN}",
            }
        )
        + "\n"
        + json.dumps({"uuid": "item-2", "status": "downloaded"}) + "\n",
        encoding="utf-8",
    )
    rewritten = scrub_download_manifest(tmp_path)
    assert rewritten == [str(manifest)]
    body = manifest.read_text(encoding="utf-8")
    for secret in SECRETS:
        assert secret not in body, secret
    assert "item-1" in body and "item-2" in body
    assert "[REDACTED]" in body


def test_redaction_scope_covers_process_stdout_stderr_and_logs(tmp_path):
    """Run the scope in a real child process (fd capture, exactly like the shell)."""
    import subprocess
    import sys as _sys

    from helpers import REPO_ROOT

    script = tmp_path / "probe.py"
    script.write_text(
        "import logging, sys\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        "from acquisition.redaction import redaction_scope, register_secrets\n"
        f"register_secrets({TOKEN!r}, {REFRESH!r}, {BASIC!r}, {PASSWORD_SENTINEL!r})\n"
        "logging.basicConfig(level=logging.DEBUG, stream=sys.stderr, format='%(message)s')\n"
        f"url = {SIGNED_URL!r}\n"
        "with redaction_scope():\n"
        "    print('stdout line ' + url)\n"
        "    sys.stderr.write('stderr line %s\\n' % url)\n"
        "    logging.getLogger('eodms.dds').error('log line %s', url)\n"
        "    logging.getLogger('eodms.dds').error('exc line %s', RuntimeError(url))\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [_sys.executable, str(script)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 0, proc.stderr
    for secret in SECRETS:
        assert secret not in proc.stdout, f"{secret!r} leaked via stdout"
        assert secret not in proc.stderr, f"{secret!r} leaked via stderr/logs"
    assert "stdout line" in proc.stdout and "[REDACTED]" in proc.stdout
    assert "stderr line" in proc.stderr
    assert "log line" in proc.stderr and "exc line" in proc.stderr


# --------------------------------------------------------------------------
# end-to-end through the genuine AAA/DDS boundary
# --------------------------------------------------------------------------


def test_signed_url_is_redacted_from_output_exception_and_logs(
    monkeypatch, tmp_path, upstream, blocked_sockets, signed_url_http_error, live_tokens
):
    """A real AAA request fails at the HTTP boundary with a signed-URL token."""
    monkeypatch.setattr(wrapper_cli, "prompt_username", lambda *a, **k: "redaction.sentinel")
    monkeypatch.setattr(wrapper_cli, "prompt_password", lambda *a, **k: PASSWORD_SENTINEL)
    real_aaa_probe: list[object] = []
    import eodms.aaa as aaa_module

    real_init = aaa_module.AAA_API.__init__

    def spy_init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        real_aaa_probe.append(self)

    monkeypatch.setattr(aaa_module.AAA_API, "__init__", spy_init)

    result = CliRunner().invoke(
        wrapper_cli.main,
        [
            "search",
            "--collection",
            "Radarsat-2_Tropical_Forest_Products",
            "--bbox",
            "-100,10,-99,11",
            "--datetime",
            "2024-01-01/2024-12-31",
            "--output",
            str(tmp_path / "results.geojson"),
        ],
    )
    assert real_aaa_probe, "the real AAA client must be constructed"
    assert signed_url_http_error, "the HTTP boundary must actually be reached"

    surfaces = {
        "stdout": result.output or "",
        "exception": repr(result.exception),
    }
    for name, text in surfaces.items():
        for secret in SECRETS:
            assert secret not in text, f"{secret!r} leaked via {name}"

    if result.exception is not None and not isinstance(result.exception, SystemExit):
        assert PASSWORD_SENTINEL not in str(result.exception)

    for root in (Path(wrapper_cli.UPSTREAM_SRC_DIR), tmp_path, Path.home() / ".eodms"):
        for secret in SECRETS:
            assert not contains_secret(root, secret), f"{secret!r} written under {root}"


def test_signed_url_from_a_download_manifest_is_scrubbed_end_to_end(
    monkeypatch, tmp_path, upstream, live_tokens
):
    """A manifest written during the invocation is redacted by the wrapper's scrub pass."""
    import click

    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    monkeypatch.setattr(wrapper_cli, "prompt_username", lambda *a, **k: "redaction.sentinel")
    monkeypatch.setattr(wrapper_cli, "prompt_password", lambda *a, **k: PASSWORD_SENTINEL)

    @click.command("download")
    @click.option("-c", "--collection")
    @click.option("--input")
    @click.option("--dl_dir")
    @click.option("--uuid")
    @click.option("-l", "--limit", type=int)
    @click.option("-e", "--env")
    def recording_download(**kwargs):
        """Stand-in body: the upstream still writes its real manifest layout."""
        (raw_dir / "downloads.jsonl").write_text(
            json.dumps(
                {
                    "uuid": "item-1",
                    "url": SIGNED_URL,
                    "detail": f"resume from {SIGNED_URL}",
                    "error": f"HTTPError 403 access_token={TOKEN}",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        click.echo(f"would download {kwargs.get('input')}")

    original_download = upstream.cli.commands["download"]
    upstream.cli.commands["download"] = recording_download
    monkeypatch.setattr(wrapper_cli, "load_upstream", lambda: upstream)
    scenes = tmp_path / "selected-scenes.geojson"
    scenes.write_text(
        '{"type": "FeatureCollection", "features": [{"id": "item-1", "geometry": null}]}'
    )
    try:
        result = CliRunner().invoke(
            wrapper_cli.main,
            [
                "download",
                "--collection",
                "Radarsat-2_Tropical_Forest_Products",
                "--scenes",
                str(scenes),
                "--output-dir",
                str(raw_dir),
            ],
        )
        assert result.exit_code == 0, result.output
    finally:
        upstream.cli.commands["download"] = original_download

    manifest = raw_dir / "downloads.jsonl"
    assert manifest.is_file(), "the test must exercise the real manifest path"
    body = manifest.read_text(encoding="utf-8")
    for secret in SECRETS:
        assert secret not in body, f"{secret!r} survived in the download manifest"
    assert "[REDACTED]" in body


def test_upstream_module_is_the_pinned_one():
    module = import_upstream()
    assert module is not None
    assert module.__name__ == "eodms_cli"