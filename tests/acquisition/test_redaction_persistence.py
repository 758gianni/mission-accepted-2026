"""No unredacted credential bytes are ever persisted - not even temporarily.

Covers the two reproduced blockers:

* the fd capture used to spool raw fd 1/2 into a ``tempfile.TemporaryFile``
  before redacting on exit, so a direct ``os.write`` sentinel could be read back
  with ``os.pread`` *during* the invocation. It is now an in-memory pipe pump.
* the genuine upstream writer ``eodms_cli._write_jsonl_rows_atomic`` wrote
  signed-URL tokens into ``downloads.jsonl`` before the post-exit scrub, so an
  interrupt could preserve them. Records are now sanitised **before** the writer
  touches the disk, via a scoped hook around the genuine writer.

Both are asserted *during* the invocation and after a killed child process.
No authentication and no network access occur.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from acquisition import cli as wrapper_cli
from acquisition.redaction import redaction_scope, register_secrets, sanitise_jsonl_rows
from helpers import REPO_ROOT, contains_secret, record_upstream_argv

TOKEN = "PERSISTED-SENTINEL-TOKEN-3f7a2b"
REFRESH = "PERSISTED-SENTINEL-REFRESH-91c4"
PASSWORD_SENTINEL = "persist-s3ntinel-p4ssw0rd-DO-NOT-LOG"
SECRETS = (TOKEN, REFRESH, PASSWORD_SENTINEL)

SIGNED_URL = (
    "https://order.eodms-sgdot.nrcan-rncan.gc.ca/rapi/download/"
    f"tmp/{TOKEN}.tif?access_token={TOKEN}&refresh_token={REFRESH}"
)


@pytest.fixture(autouse=True)
def _clean_registry():
    from acquisition.redaction import forget_secrets

    forget_secrets()
    yield
    forget_secrets()


def _tempdir_files() -> list[Path]:
    import tempfile

    return sorted(p for p in Path(tempfile.gettempdir()).iterdir() if p.is_file())


def _spool_files_containing(secret: str) -> list[str]:
    return [
        str(path)
        for path in _tempdir_files()
        for _ in contains_secret(path, secret)
    ]


def test_no_spool_file_holds_raw_fd_output_during_the_invocation():
    """(A) raw fd bytes must not exist on disk at any point inside the scope."""
    register_secrets(TOKEN, REFRESH)
    observed: dict[str, object] = {}
    with redaction_scope():
        os.write(1, f"chunk-one {SIGNED_URL}\n".encode())
        observed["temp_files_during"] = [str(p) for p in _tempdir_files()]
        observed["spool_hits_during"] = _spool_files_containing(TOKEN)
        observed["spool_hits_refresh_during"] = _spool_files_containing(REFRESH)
    observed["spool_hits_after"] = _spool_files_containing(TOKEN)
    assert observed["spool_hits_during"] == [], observed
    assert observed["spool_hits_refresh_during"] == [], observed
    assert observed["spool_hits_after"] == []
    # the only temp entries are pre-existing/unrelated, never a redaction spool
    assert not any("redact" in name or "spool" in name for name in observed["temp_files_during"])


def test_split_secret_across_writes_is_still_redacted():
    """A token straddling two reads must not slip through the streaming filter."""
    register_secrets(TOKEN, REFRESH)
    script = (
        "import os, sys\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        "from acquisition.redaction import redaction_scope, register_secrets\n"
        f"register_secrets({TOKEN!r}, {REFRESH!r})\n"
        "with redaction_scope():\n"
        f"    os.write(1, {('https://x/a/' + TOKEN[:12])!r}.encode())\n"
        f"    os.write(1, {(TOKEN[12:] + '.tif?token=raw')!r}.encode())\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, cwd=str(REPO_ROOT)
    )
    assert proc.returncode == 0, proc.stderr
    assert TOKEN not in proc.stdout
    assert "raw" not in proc.stdout.replace("[REDACTED]", "")
    assert "[REDACTED]" in proc.stdout


def test_manifest_records_are_sanitised_before_the_genuine_writer_runs(monkeypatch, tmp_path, upstream):
    """(B) the disk must never hold the raw token, even inside the call."""
    register_secrets(TOKEN, REFRESH)
    manifest = tmp_path / "downloads.jsonl"
    written: list[list[dict]] = []
    original = upstream._write_jsonl_rows_atomic

    def observe(file_path, rows, *args, **kwargs):
        # the genuine writer receives sanitised rows ...
        written.append([dict(row) for row in rows])
        original(file_path, rows, *args, **kwargs)
        # ... and on-disk content can be checked while the scope is still open
        assert not contains_secret(manifest, TOKEN, REFRESH)

    monkeypatch.setattr(upstream, "_write_jsonl_rows_atomic", observe)
    rows = [
        {"uuid": "item-1", "url": SIGNED_URL, "detail": f"resume {SIGNED_URL}",
         "error": f"HTTPError 403 access_token={TOKEN}"},
        {"uuid": "item-2", "status": "downloaded"},
    ]
    with redaction_scope(None, upstream):
        upstream._write_jsonl_rows_atomic(str(manifest), rows)

    body = manifest.read_text(encoding="utf-8")
    for secret in (TOKEN, REFRESH):
        assert secret not in body
    assert "[REDACTED]" in body
    assert written and written[0][0]["uuid"] == "item-1"
    assert written[0][1]["status"] == "downloaded"
    # the hook is restored afterwards
    assert upstream._write_jsonl_rows_atomic is observe


def test_killed_child_leaves_no_raw_token_on_disk():
    """Simulate SIGKILL: the child dies inside the scope, right after the write."""
    manifest_dir = Path(
        subprocess.run(
            [sys.executable, "-c",
             "import tempfile;print(tempfile.mkdtemp(prefix='acq-manifest-'))"],
            capture_output=True, text=True, check=True).stdout.strip()
    )
    script = f"""
import os, sys
sys.path.insert(0, {str(REPO_ROOT)!r})
sys.path.insert(0, {str(REPO_ROOT / '.tools/eodms-cli/src')!r})
from acquisition.redaction import redaction_scope, register_secrets
register_secrets({TOKEN!r}, {REFRESH!r})
import eodms_cli
rows = [{{"uuid": "item-1", "url": {SIGNED_URL!r},
         "detail": "resume", "error": "403 access_token={TOKEN}"}}]
with redaction_scope(None, eodms_cli):
    eodms_cli._write_jsonl_rows_atomic({str(manifest_dir / 'downloads.jsonl')!r}, rows)
    os.write(1, f"raw stdout {SIGNED_URL}\\n".encode())
    os._exit(9)
"""
    proc = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, cwd=str(REPO_ROOT)
    )
    assert proc.returncode == 9, proc.stderr
    manifest = manifest_dir / "downloads.jsonl"
    assert manifest.is_file(), "the genuine writer must have written the manifest"
    body = manifest.read_text(encoding="utf-8")
    for secret in (TOKEN, REFRESH):
        assert secret not in body, f"{secret!r} survived a killed process"
    assert "[REDACTED]" in body
    assert json.loads(body)["uuid"] == "item-1"
    for secret in (TOKEN, REFRESH):
        assert not contains_secret(manifest_dir, secret)


def test_sanitise_jsonl_rows_does_not_touch_product_binaries():
    """Row sanitisation must leave product data (e.g. byte payloads) alone."""
    import base64

    payload = base64.b64encode(b"product-tif-bytes").decode()
    rows = [{"uuid": "item-1", "asset_path": "/raw/item.tif", "sha": payload}]
    cleaned = sanitise_jsonl_rows(rows)
    assert cleaned[0]["asset_path"] == "/raw/item.tif"
    assert cleaned[0]["sha"] == payload


def test_credential_password_is_registered_before_the_upstream_call(monkeypatch, tmp_path, upstream):
    """The prompted password joins the registry, so it is scrubbed everywhere."""
    from acquisition.redaction import known_secrets

    recorded = record_upstream_argv(monkeypatch)
    monkeypatch.setattr(wrapper_cli, "prompt_username", lambda *a, **k: "persist.sentinel")
    monkeypatch.setattr(wrapper_cli, "prompt_password", lambda *a, **k: PASSWORD_SENTINEL)
    from click.testing import CliRunner

    result = CliRunner().invoke(
        wrapper_cli.main,
        ["download", "--collection", "C", "--uuid", "u1", "--output-dir", str(tmp_path / "raw")],
    )
    assert result.exit_code == 0, result.output
    assert recorded
    assert PASSWORD_SENTINEL in known_secrets()
    assert not contains_secret(tmp_path, PASSWORD_SENTINEL)
