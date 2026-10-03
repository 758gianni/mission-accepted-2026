"""The pinned generation must still be the directory it was validated as.

``_read_png`` used to resolve the pinned path, which meant a generation
directory swapped for a symlink after validation was followed and its outside
bytes served. These tests pin the directory identity at validation time and
require it to still hold on every read.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import _read_png, create_app
from backend.bundle import BundleStore
from conftest import PNG_1X1_GRAY, analysis_document, regions_document, reencode_png


def build_generation(parent: Path, analysis_id: str, size: tuple[int, int] = (1, 1)) -> Path:
    generation = parent / f".current.gen-{analysis_id}"
    generation.mkdir(parents=True)
    analysis = analysis_document()
    analysis["analysis_id"] = analysis_id
    regions = regions_document()
    for index, feature in enumerate(regions["features"], start=1):
        feature["id"] = f"{analysis_id}-{index}"
        feature["properties"]["region_id"] = f"{analysis_id}-{index}"
    analysis["demo_region_id"] = f"{analysis_id}-1"
    (generation / "analysis.json").write_text(json.dumps(analysis))
    (generation / "regions.geojson").write_text(json.dumps(regions))
    for key in ("before", "after", "change"):
        (generation / f"{key}.png").write_bytes(reencode_png(*size))
    return generation


def point_at(parent: Path, generation_name: str) -> None:
    pointer = parent / ".current.pointer-tmp"
    if pointer.exists() or pointer.is_symlink():
        pointer.unlink()
    os.symlink(generation_name, pointer)
    os.replace(pointer, parent / "current")


@pytest.fixture
def published(tmp_path: Path) -> tuple[Path, Path]:
    parent = tmp_path / "processed"
    parent.mkdir()
    generation = build_generation(parent, "aabbccdd11223344")
    point_at(parent, generation.name)
    return parent, generation


def outside_dir(tmp_path: Path, size: tuple[int, int] = (2, 1)) -> tuple[Path, bytes]:
    outside = tmp_path / "outside"
    outside.mkdir()
    payload = reencode_png(*size)
    (outside / "before.png").write_bytes(payload)
    return outside, payload


def assert_refused(directory: Path, filename: str = "before.png") -> None:
    store = BundleStore(directory)
    snapshot = store.snapshot(force=True)
    assert snapshot.state == "ready"
    assert snapshot.bundle is not None
    assert snapshot.root_identity is not None
    with pytest.raises(OSError):
        _read_png(snapshot.generation_dir, filename, snapshot.root_identity)


# ---------------------------------------------------------------------------
# replaced, moved or deleted generation roots
# ---------------------------------------------------------------------------


def test_symlinked_generation_root_is_refused(
    published: tuple[Path, Path], tmp_path: Path
) -> None:
    parent, generation = published
    outside, _ = outside_dir(tmp_path)
    store = BundleStore(parent / "current")
    snapshot = store.snapshot(force=True)
    assert snapshot.state == "ready"

    # The lead's hook: after validation, before the read.
    generation.rename(generation.with_name(generation.name + "-saved"))
    generation.symlink_to(outside, target_is_directory=True)

    with pytest.raises(OSError):
        _read_png(snapshot.generation_dir, "before.png", snapshot.root_identity)


def test_symlinked_generation_root_never_returns_external_bytes(
    published: tuple[Path, Path], tmp_path: Path
) -> None:
    parent, generation = published
    outside, payload = outside_dir(tmp_path)
    client = TestClient(create_app(bundle_dir=parent / "current"))
    url = client.get("/api/analysis").json()["imagery"]["before"]["url"]
    assert client.get(url).status_code == 200
    assert client.get(url).content != payload

    generation.rename(generation.with_name(generation.name + "-saved"))
    generation.symlink_to(outside, target_is_directory=True)
    response = client.get(url)
    assert response.status_code == 503
    assert response.content != payload
    assert response.headers["content-type"].startswith("application/json")


def test_moved_generation_root_is_refused(published: tuple[Path, Path]) -> None:
    parent, generation = published
    store = BundleStore(parent / "current")
    snapshot = store.snapshot(force=True)
    generation.rename(generation.with_name(generation.name + "-saved"))
    with pytest.raises(OSError):
        _read_png(snapshot.generation_dir, "before.png", snapshot.root_identity)


def test_deleted_generation_root_is_refused(published: tuple[Path, Path]) -> None:
    parent, generation = published
    store = BundleStore(parent / "current")
    snapshot = store.snapshot(force=True)
    for child in generation.iterdir():
        child.unlink()
    generation.rmdir()
    with pytest.raises(OSError):
        _read_png(snapshot.generation_dir, "before.png", snapshot.root_identity)


def test_replaced_generation_root_is_refused(published: tuple[Path, Path]) -> None:
    """Same path, different directory: a prune and republication must not pass."""
    parent, generation = published
    store = BundleStore(parent / "current")
    snapshot = store.snapshot(force=True)
    shutil.rmtree(generation)
    build_generation(parent, "aabbccdd11223344", size=(7, 7))
    with pytest.raises(OSError):
        _read_png(snapshot.generation_dir, "before.png", snapshot.root_identity)


def test_reused_directory_identity_is_still_checked(
    published: tuple[Path, Path], tmp_path: Path
) -> None:
    """Reusing the path with different bytes must not serve the old snapshot."""
    parent, generation = published
    store = BundleStore(parent / "current")
    snapshot = store.snapshot(force=True)
    replacement = reencode_png(9, 9)
    for child in generation.iterdir():
        child.unlink()
    (generation / "before.png").write_bytes(replacement)
    with pytest.raises(OSError):
        _read_png(snapshot.generation_dir, "before.png", snapshot.root_identity)


# ---------------------------------------------------------------------------
# the normal paths still work
# ---------------------------------------------------------------------------


def test_unchanged_generation_is_served(published: tuple[Path, Path]) -> None:
    parent, _ = published
    client = TestClient(create_app(bundle_dir=parent / "current"))
    url = client.get("/api/analysis").json()["imagery"]["before"]["url"]
    response = client.get(url)
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == PNG_1X1_GRAY


def test_plain_root_directory_still_served(tmp_path: Path) -> None:
    plain = tmp_path / "current"
    plain.mkdir()
    for key in ("before", "after", "change"):
        (plain / f"{key}.png").write_bytes(PNG_1X1_GRAY)
    (plain / "analysis.json").write_text(json.dumps(analysis_document()))
    (plain / "regions.geojson").write_text(json.dumps(regions_document()))
    store = BundleStore(plain)
    snapshot = store.snapshot(force=True)
    assert snapshot.state == "ready"
    assert snapshot.root_identity is not None
    assert _read_png(snapshot.generation_dir, "before.png", snapshot.root_identity)
    client = TestClient(create_app(bundle_dir=plain))
    assert client.get("/api/imagery/before").status_code == 200


def test_pointer_flip_mid_request_serves_the_pinned_generation(
    published: tuple[Path, Path]
) -> None:
    """An A -> B flip while a snapshot is in flight must not switch to B."""
    parent, generation_a = published
    store = BundleStore(parent / "current")
    snapshot = store.snapshot(force=True)
    assert snapshot.analysis_id == "aabbccdd11223344"
    pinned_a = _read_png(snapshot.generation_dir, "before.png", snapshot.root_identity)

    build_generation(parent, "ffffffff00000000", size=(2, 2))
    point_at(parent, ".current.gen-ffffffff00000000")

    again = _read_png(snapshot.generation_dir, "before.png", snapshot.root_identity)
    assert again == pinned_a
    assert store.snapshot().analysis_id == "ffffffff00000000"


def test_identity_is_recorded_for_a_ready_snapshot(published: tuple[Path, Path]) -> None:
    parent, generation = published
    snapshot = BundleStore(parent / "current").snapshot(force=True)
    assert snapshot.root_identity is not None
    assert snapshot.root_identity[0] == generation.stat().st_dev
    assert snapshot.root_identity[1] == generation.stat().st_ino
    assert [entry[0] for entry in snapshot.root_identity[2]] == [
        "after.png", "analysis.json", "before.png", "change.png", "regions.geojson",
    ]


def test_symlinked_image_inside_generation_still_refused(
    published: tuple[Path, Path], tmp_path: Path
) -> None:
    parent, generation = published
    outside, _ = outside_dir(tmp_path)
    (generation / "change.png").unlink()
    (generation / "change.png").symlink_to(outside / "before.png")
    client = TestClient(create_app(bundle_dir=parent / "current"))
    assert client.get("/api/status").json()["state"] == "error"
    assert client.get("/api/imagery/change").status_code == 503


def test_traversal_filename_still_refused(published: tuple[Path, Path]) -> None:
    assert_refused(published[0] / "current", "../../outside/before.png")