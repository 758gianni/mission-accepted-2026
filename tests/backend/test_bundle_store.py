"""Bundle loading, caching and configuration behaviour."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.bundle import BundleStore, load_bundle
from backend.config import (
    BUNDLE_DIR_ENV,
    CORS_ORIGINS_ENV,
    DEFAULT_BUNDLE_DIR,
    default_bundle_dir,
    default_cors_origins,
)
from conftest import PNG_1X1_GRAY, analysis_document, regions_document, reencode_png, write_bundle


def test_missing_directory_is_awaiting(tmp_path: Path) -> None:
    snapshot = load_bundle(tmp_path / "nope")
    assert snapshot.state == "awaiting_analysis"
    assert snapshot.analysis_id is None
    assert snapshot.scene_count == 0


def test_empty_directory_is_awaiting(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert load_bundle(empty).state == "awaiting_analysis"


def test_directory_with_only_analysis_is_incomplete(tmp_path: Path) -> None:
    partial = tmp_path / "partial"
    partial.mkdir()
    (partial / "analysis.json").write_text(json.dumps(analysis_document()))
    snapshot = load_bundle(partial)
    assert snapshot.state == "error"
    assert "regions.geojson" in snapshot.message


def test_directory_with_only_regions_is_incomplete(tmp_path: Path) -> None:
    partial = tmp_path / "partial"
    partial.mkdir()
    (partial / "regions.geojson").write_text(json.dumps(regions_document()))
    assert load_bundle(partial).state == "error"


def test_bundle_path_that_is_a_file_is_an_error(tmp_path: Path) -> None:
    target = tmp_path / "bundle-file"
    target.write_text("not a directory")
    snapshot = load_bundle(target)
    assert snapshot.state == "error"
    assert str(target) not in snapshot.message


def test_snapshot_messages_never_contain_paths(tmp_path: Path) -> None:
    bundle = write_bundle(tmp_path)
    (bundle / "analysis.json").write_text("nope")
    message = load_bundle(bundle).message
    assert str(tmp_path) not in message
    assert str(bundle) not in message
    assert "nope" not in message


def test_store_picks_up_a_new_bundle_without_restart(tmp_path: Path) -> None:
    bundle = tmp_path / "current"
    client = TestClient(create_app(bundle_dir=bundle))
    assert client.get("/api/status").json()["state"] == "awaiting_analysis"
    assert client.get("/api/analysis").status_code == 404
    write_bundle(tmp_path)
    assert client.get("/api/status").json()["state"] == "ready"
    assert client.get("/api/analysis").status_code == 200
    assert client.get("/api/regions").json()["type"] == "FeatureCollection"


def test_store_does_not_serve_a_bundle_that_became_broken(tmp_path: Path) -> None:
    bundle = write_bundle(tmp_path)
    store = BundleStore(bundle)
    assert store.snapshot().state == "ready"
    (bundle / "regions.geojson").write_text("{ broken")
    assert store.snapshot().state == "error"


def test_store_caches_while_nothing_changes(tmp_path: Path) -> None:
    bundle = write_bundle(tmp_path)
    store = BundleStore(bundle)
    first = store.snapshot()
    assert store.snapshot() is first
    assert store.snapshot(force=True).state == "ready"


def test_imagery_bytes_are_read_from_disk_not_generated(tmp_path: Path) -> None:
    bundle = write_bundle(tmp_path)
    payload = reencode_png(3, 2)
    for key in ("before", "after", "change"):
        (bundle / f"{key}.png").write_bytes(payload)
    client = TestClient(create_app(bundle_dir=bundle))
    response = client.get("/api/imagery/change")
    assert response.status_code == 200
    assert response.content == payload
    assert response.content != PNG_1X1_GRAY


def test_default_bundle_dir_prefers_environment(monkeypatch) -> None:
    monkeypatch.delenv(BUNDLE_DIR_ENV, raising=False)
    assert default_bundle_dir() == Path(DEFAULT_BUNDLE_DIR)
    monkeypatch.setenv(BUNDLE_DIR_ENV, "/tmp/custom-bundle")
    assert default_bundle_dir() == Path("/tmp/custom-bundle")
    monkeypatch.setenv(BUNDLE_DIR_ENV, "   ")
    assert default_bundle_dir() == Path(DEFAULT_BUNDLE_DIR)


def test_default_cors_origins(monkeypatch) -> None:
    monkeypatch.delenv(CORS_ORIGINS_ENV, raising=False)
    assert default_cors_origins() == ["http://localhost:3000", "http://localhost:5173"]
    monkeypatch.setenv(CORS_ORIGINS_ENV, "http://localhost:4173, http://127.0.0.1:3000")
    assert default_cors_origins() == ["http://localhost:4173", "http://127.0.0.1:3000"]


def test_create_app_argument_overrides_environment(tmp_path: Path, monkeypatch) -> None:
    bundle = write_bundle(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv(BUNDLE_DIR_ENV, str(other))
    client = TestClient(create_app(bundle_dir=bundle))
    assert client.get("/api/status").json()["state"] == "ready"


def test_image_swapped_for_symlink_after_validation_is_not_served(tmp_path: Path) -> None:
    bundle = write_bundle(tmp_path)
    secret = tmp_path / "secret.png"
    secret.write_bytes(PNG_1X1_GRAY)
    client = TestClient(create_app(bundle_dir=bundle))
    assert client.get("/api/imagery/before").status_code == 200
    (bundle / "before.png").unlink()
    (bundle / "before.png").symlink_to(secret)
    response = client.get("/api/imagery/before")
    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/json")
