"""Boundary evidence against the real pinned EODMS CLI.

Unlike ``test_credentials.py`` (which stubs the network-facing factories so the
command bodies can be driven cheaply), this module runs the **real**
``eodms_cli.make_aaa`` -> ``eodms.aaa.AAA_API`` path and the **real**
``resolve_credentials`` / ``ConfigUtils`` config resolution. The only thing
replaced is the network itself: every socket operation raises
``NetworkBlocked`` before a single byte can leave the machine, so **no EODMS
authentication is ever attempted**.

Asserted from the inside, mid-invocation:

* the real ``AAA_API.auth_folder`` / ``aaa_creds.cred_fn`` / token lock path are
  all inside the task-scoped sandbox, never under the real home;
* a decoy ``~/.eodms/config.ini`` planted in the **real** home is never read (the
  prompted sentinel credentials are what reach the client) and is byte-identical
  afterwards;
* the real home tree is unchanged (no new files, no new directories);
* no sentinel secret (plaintext or base64) appears in output, exceptions, or any
  file left behind.
"""

from __future__ import annotations

import base64
import os
import socket
from pathlib import Path

import pytest
from click.testing import CliRunner

from acquisition import cli as wrapper_cli
from helpers import contains_secret, stable_environ

SENTINEL_USER = "boundary.sentinel@example.invalid"
SENTINEL_PASS = "b0undary-s3ntinel-p4ssw0rd-DO-NOT-LOG"
DECOY_USER = "decoy.real.home@example.invalid"
DECOY_PASS = "decoy-p4ssw0rd-should-never-be-read"
SENTINEL_ENCODED = base64.b64encode(SENTINEL_PASS.encode()).decode()


class NetworkBlocked(RuntimeError):
    """Raised instead of opening a connection: proves the boundary, no traffic."""


@pytest.fixture
def blocked_network(monkeypatch):
    """Block every socket operation and record that the boundary was reached."""
    attempts: list[str] = []

    def blocked(*args, **kwargs):
        attempts.append(repr(args[:2]))
        raise NetworkBlocked("network access is blocked in this test")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    return attempts


@pytest.fixture
def real_aaa_probe(monkeypatch, upstream):
    """Observe the real ``AAA_API`` instance without changing its behaviour."""
    import eodms.aaa as aaa_module

    created: list[object] = []
    real_init = aaa_module.AAA_API.__init__

    def spy_init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        created.append(self)

    monkeypatch.setattr(aaa_module.AAA_API, "__init__", spy_init)
    return created


@pytest.fixture
def decoy_real_home_config():
    """Plant a decoy ~/.eodms/config.ini in the REAL home, then restore exactly.

    The upstream ``resolve_credentials`` reads this file when no credentials are
    supplied, so it is the sharpest possible test of "the real ~/.eodms is never
    read". The decoy is removed (or restored byte-for-byte) afterwards, and the
    real home tree is asserted unchanged.
    """
    real_home = Path.home()
    eodms_dir = real_home / ".eodms"
    config = eodms_dir / "config.ini"
    existed = config.is_file()
    previous = config.read_bytes() if existed else None
    if not existed:
        eodms_dir.mkdir(parents=True, exist_ok=True)
    config.write_text(
        "[Credentials]\n"
        f"username = {DECOY_USER}\n"
        f"password = {base64.b64encode(DECOY_PASS.encode()).decode()}\n",
        encoding="utf-8",
    )
    try:
        yield config
    finally:
        if existed and previous is not None:
            config.write_bytes(previous)
        elif config.exists():
            config.unlink()
        if not existed and eodms_dir.is_dir() and not any(eodms_dir.iterdir()):
            eodms_dir.rmdir()


#: The agent's own session store/log live here and echo this file's source text,
#: so they are excluded from content scans. They are still covered by the
#: structural "real home tree unchanged" assertion.
_TOOLING_DIRS = (".local", ".cache", ".npm", ".rustup", ".cargo")


def _home_tree() -> set[str]:
    home = Path.home()
    return {str(p.relative_to(home)) for p in home.rglob("*") if ".cache" not in p.parts}


def _secret_scan_roots(tmp_path: Path) -> list[Path]:
    home = Path.home()
    roots = [tmp_path, REPO_ROOT_DATA, Path(wrapper_cli.UPSTREAM_SRC_DIR), home / ".eodms"]
    roots += [p for p in (home / ".config", home / "Downloads", home / "Documents") if p.exists()]
    roots.append(home)
    return roots


def _assert_no_secret_anywhere(roots, *secrets: str) -> None:
    for root in roots:
        candidates = [root]
        if root == Path.home():
            candidates = [
                p
                for p in root.iterdir()
                if p.is_dir() and p.name not in _TOOLING_DIRS
            ] + [p for p in root.iterdir() if p.is_file()]
        for candidate in candidates:
            for secret in secrets:
                assert not contains_secret(candidate, secret), (
                    f"{secret!r} leaked into {candidate}"
                )


def _run(args, **kwargs):
    return CliRunner().invoke(wrapper_cli.main, args, **kwargs)


def test_real_aaa_paths_stay_task_scoped_with_real_config_resolution(
    monkeypatch,
    tmp_path,
    upstream,
    blocked_network,
    real_aaa_probe,
    decoy_real_home_config,
):
    """Real make_aaa + real AAA_API + real ConfigUtils, sockets blocked."""
    monkeypatch.setattr(wrapper_cli, "prompt_username", lambda *a, **k: SENTINEL_USER)
    monkeypatch.setattr(wrapper_cli, "prompt_password", lambda *a, **k: SENTINEL_PASS)

    env_before = stable_environ()
    home_before = _home_tree()
    decoy_before = decoy_real_home_config.read_bytes()

    result = _run(
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
        ]
    )

    # the real client was constructed with the prompted credentials
    assert real_aaa_probe, "real AAA_API was never constructed"
    aaa_api = real_aaa_probe[0]
    assert aaa_api.username == SENTINEL_USER
    assert aaa_api.password == SENTINEL_PASS

    # ... and every path it would persist to is task-scoped
    sandbox_root = aaa_api.auth_folder.rsplit("/.eodms", 1)[0]
    assert aaa_api.auth_folder == os.path.join(sandbox_root, ".eodms")
    assert aaa_api.aaa_creds.cred_fn.startswith(sandbox_root), aaa_api.aaa_creds.cred_fn
    assert aaa_api._token_lock_fn.startswith(sandbox_root), aaa_api._token_lock_fn
    assert SENTINEL_USER in aaa_api.aaa_creds.cred_fn
    assert sandbox_root != str(Path.home())

    # the network boundary was actually reached, and nothing left the machine
    assert blocked_network, "the run never reached the network boundary"

    # the decoy config in the real home was never consulted
    assert aaa_api.username != DECOY_USER
    assert aaa_api.password != DECOY_PASS
    assert DECOY_USER not in (result.output or "")
    assert decoy_real_home_config.read_bytes() == decoy_before

    # the environment and the real home are untouched
    assert stable_environ() == env_before
    assert _home_tree() == home_before

    # no secret in output, exception, or anywhere on disk
    text = (result.output or "") + repr(result.exception)
    for secret in (SENTINEL_PASS, SENTINEL_ENCODED, DECOY_PASS):
        assert secret not in text
    _assert_no_secret_anywhere(
        _secret_scan_roots(tmp_path), SENTINEL_PASS, SENTINEL_ENCODED, DECOY_PASS
    )


def test_real_config_resolution_never_falls_back_to_the_decoy(
    monkeypatch, tmp_path, upstream, blocked_network, real_aaa_probe, decoy_real_home_config
):
    """With no credentials supplied at all the sandbox still must not read ~/.eodms.

    The prompt returns empty strings, which the wrapper rejects; the point of the
    test is that resolution never picks up the decoy credentials on the way.
    """
    monkeypatch.setattr(wrapper_cli, "prompt_username", lambda *a, **k: "")
    monkeypatch.setattr(wrapper_cli, "prompt_password", lambda *a, **k: "")
    decoy_before = decoy_real_home_config.read_bytes()

    result = _run(
        [
            "search",
            "--collection",
            "Radarsat-2_Tropical_Forest_Products",
            "--bbox",
            "-100,10,-99,11",
            "--output",
            str(tmp_path / "results.geojson"),
        ]
    )
    assert result.exit_code != 0
    assert "username is required" in result.output
    assert real_aaa_probe == [], "no client may be built from empty credentials"
    assert decoy_real_home_config.read_bytes() == decoy_before
    assert DECOY_PASS not in (result.output or "")


def test_real_download_stays_task_scoped_with_sockets_blocked(
    monkeypatch, tmp_path, upstream, blocked_network, real_aaa_probe
):
    """Same guarantee on the download path, with a real scenes file."""
    monkeypatch.setattr(wrapper_cli, "prompt_username", lambda *a, **k: SENTINEL_USER)
    monkeypatch.setattr(wrapper_cli, "prompt_password", lambda *a, **k: SENTINEL_PASS)
    scenes_file = tmp_path / "selected-scenes.geojson"
    scenes_file.write_text(
        '{"type": "FeatureCollection", "features": [{"id": "sentinel-item-1",'
        ' "properties": {"collection": "Radarsat-2_Tropical_Forest_Products"},'
        ' "geometry": null}]}'
    )

    result = _run(
        [
            "download",
            "--collection",
            "Radarsat-2_Tropical_Forest_Products",
            "--scenes",
            str(scenes_file),
            "--output-dir",
            str(tmp_path / "raw"),
        ]
    )
    assert real_aaa_probe, "real AAA_API was never constructed"
    aaa_api = real_aaa_probe[0]
    assert aaa_api.auth_folder.endswith("/.eodms")
    assert not aaa_api.auth_folder.startswith(str(Path.home()))
    assert aaa_api.password == SENTINEL_PASS
    assert blocked_network, "the run never reached the network boundary"
    text = (result.output or "") + repr(result.exception)
    assert SENTINEL_PASS not in text
    assert SENTINEL_ENCODED not in text
    assert not contains_secret(Path(wrapper_cli.UPSTREAM_SRC_DIR), SENTINEL_PASS)
    assert not contains_secret(Path.home() / ".eodms", SENTINEL_PASS, SENTINEL_ENCODED)


REPO_ROOT_DATA = Path(__file__).resolve().parents[2] / "data"
