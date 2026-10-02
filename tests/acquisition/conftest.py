"""Fixtures for the acquisition wrapper tests.

Nothing here performs an EODMS login; the real ~/.eodms is never read or
written because HOME/USERPROFILE are redirected at every test.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from helpers import BOOTSTRAP_MISSING, REPO_ROOT, import_upstream


@pytest.fixture(scope="session")
def upstream():
    module = import_upstream()
    if module is None:
        pytest.skip(BOOTSTRAP_MISSING)
    return module


@pytest.fixture
def real_home(monkeypatch) -> Path:
    """Value of the home directory *before* the test redirects it."""
    return Path(os.path.expanduser("~"))


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch, real_home):
    """Redirect HOME/USERPROFILE to a throwaway dir and assert it stays clean."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("EODMS_REAL_HOME", str(real_home))
    yield home
    leaked = sorted(p.name for p in home.rglob("*")) if home.exists() else []
    assert leaked == [], f"test wrote into the isolated HOME: {leaked}"


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


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
def fake_aaa(monkeypatch):
    """Stub only the network-facing factories of the pinned CLI.

    Real Click parsing, credential resolution, config loading and logging
    initialisation still execute; no HTTP request and no EODMS login occur.
    """
    from acquisition import cli as wrapper_cli

    upstream = import_upstream()
    if upstream is None:
        pytest.skip(BOOTSTRAP_MISSING)
    created = []

    def aaa_factory(username, password, environment="prod"):
        api = _FakeAAA(username, password, environment)
        created.append(api)
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
