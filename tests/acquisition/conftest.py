"""Fixtures for the acquisition wrapper tests.

The environment is **never** modified: the wrapper must not repurpose
``HOME``/``USERPROFILE``/``CODEX_HOME``. Every fixture here snapshots the real
environment and the real ``~/.eodms`` so a test can prove nothing touched them.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from helpers import BOOTSTRAP_MISSING, REPO_ROOT, import_upstream


def environment_snapshot() -> dict[str, str]:
    return dict(os.environ)


def home_snapshot() -> dict[str, tuple[bool, float]]:
    """Existence + mtime of every entry in the real ``~/.eodms``."""
    real_home = Path.home()
    eodms_dir = real_home / ".eodms"
    if not eodms_dir.is_dir():
        return {"__dir__": (False, 0.0)}
    snap = {"__dir__": (True, eodms_dir.stat().st_mtime)}
    for entry in sorted(eodms_dir.rglob("*")):
        snap[str(entry)] = (entry.exists(), entry.stat().st_mtime)
    return snap


@pytest.fixture
def real_home_state():
    """(env snapshot, real ~/.eodms snapshot, real home path) for leak assertions."""
    return {
        "environ": environment_snapshot(),
        "home": Path.home(),
        "eodms": home_snapshot(),
    }


@pytest.fixture
def upstream():
    module = import_upstream()
    if module is None:
        pytest.skip(BOOTSTRAP_MISSING)
    return module


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def probe_observations():
    """Collect observations taken *during* a wrapper invocation.

    The ``fake_aaa`` factory runs mid-invocation, inside the sandbox, so this is
    where the environment/path invariants are observed from the inside.
    """
    observations: dict[str, object] = {}
    yield observations

class _FakeAAA:
    """Records the credentials it was handed; never talks to EODMS."""

    def __init__(self, username, password, environment="prod"):
        self.username = username
        self.password = password
        self.environment = environment


class _FakeSearch:
    """Canned STAC search: the real command body runs, HTTP never happens."""

    def __init__(self, username, password, environment):
        self.username = username
        self.password = password
        self.environment = environment

    def search_multiple_geometries(self, **kwargs):
        return [
            {
                "type": "Feature",
                "id": "sentinel-item-1",
                "geometry": {"type": "Point", "coordinates": [-99.5, 10.5]},
                "properties": {"id": "sentinel-item-1"},
            }
        ]


class _FakeDDS:
    def __init__(self, aaa_api, environment="prod"):
        self.aaa_api = aaa_api


@pytest.fixture
def fake_aaa(monkeypatch, probe_observations):
    """Stub only the network-facing factories of the pinned CLI.

    Real Click parsing, credential resolution, config loading and logging
    initialisation still execute; no HTTP request and no EODMS login occur.
    The factory body runs *inside* the sandbox, which is where the
    environment/path invariants are observed from the inside.
    """
    from acquisition import cli as wrapper_cli
    from acquisition.sandbox import sandboxed_paths

    upstream = import_upstream()
    if upstream is None:
        pytest.skip(BOOTSTRAP_MISSING)
    created = []

    def aaa_factory(username, password, environment="prod"):
        api = _FakeAAA(username, password, environment)
        created.append(api)
        probe_observations["environ_during"] = environment_snapshot()
        probe_observations["home_during"] = str(Path.home())
        probe_observations["paths_during"] = sandboxed_paths(upstream)
        probe_observations["default_config_direct"] = upstream._default_config_path()
        return api

    monkeypatch.setattr(wrapper_cli, "load_upstream", lambda: upstream)
    monkeypatch.setattr(upstream, "make_aaa", aaa_factory)
    monkeypatch.setattr(
        upstream,
        "make_search",
        lambda aaa_api, environment: _FakeSearch(
            getattr(aaa_api, "username", None),
            getattr(aaa_api, "password", None),
            environment,
        ),
    )
    monkeypatch.setattr(
        upstream, "make_dds", lambda aaa_api, environment="prod": _FakeDDS(aaa_api, environment)
    )
    return created
