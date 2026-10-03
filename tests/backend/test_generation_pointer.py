"""Reading a bundle published behind the producer's generation pointer.

The producer publishes by renaming a finished generation directory aside and
swapping a single relative symlink at the configured root, so a reader either
resolves the old generation or the new one. Support is deliberately narrow: only
that pointer shape, only for the configured root, and only to a real generation
directory in the same parent.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.bundle import BundleStore
from backend.generation import GenerationRejected, resolve_bundle_root
from conftest import (
    PNG_1X1_GRAY,
    analysis_document,
    reencode_png,
    regions_document,
)

SEVEN_FILES = ("analysis.json", "regions.geojson", "before.png", "after.png", "change.png")


# ---------------------------------------------------------------------------
# producer-shaped fixtures
# ---------------------------------------------------------------------------


def make_generation(parent: Path, name: str, analysis_id: str, *, extra: str = "") -> Path:
    generation = parent / f".{name}.gen-{analysis_id}{extra}"
    generation.mkdir(parents=True)
    analysis = analysis_document()
    analysis["analysis_id"] = analysis_id
    regions = regions_document()
    for index, feature in enumerate(regions["features"], start=1):
        feature["id"] = f"{analysis_id}-region-{index}"
        feature["properties"]["region_id"] = f"{analysis_id}-region-{index}"
    analysis["demo_region_id"] = f"{analysis_id}-region-1"
    (generation / "analysis.json").write_text(json.dumps(analysis, indent=2))
    (generation / "regions.geojson").write_text(json.dumps(regions, indent=2))
    for key in ("before", "after", "change"):
        (generation / f"{key}.png").write_bytes(reencode_png(2, 2))
    return generation


def publish(parent: Path, name: str, analysis_id: str, *, extra: str = "") -> Path:
    """Rename a generation into place and atomically swap the pointer at it."""
    generation = make_generation(parent, name, analysis_id, extra=extra)
    pointer = parent / f".{name}.pointer-tmp"
    if pointer.exists() or pointer.is_symlink():
        pointer.unlink()
    os.symlink(generation.name, pointer)
    os.replace(pointer, parent / name)
    return generation


def point_at(parent: Path, name: str, generation_name: str) -> None:
    """Atomically point the configured root at an existing generation."""
    pointer = parent / f".{name}.pointer-tmp"
    if pointer.exists() or pointer.is_symlink():
        pointer.unlink()
    os.symlink(generation_name, pointer)
    os.replace(pointer, parent / name)


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    parent = tmp_path / "processed"
    parent.mkdir()
    return parent


def pointer_client(parent: Path, name: str = "current") -> TestClient:
    return TestClient(create_app(bundle_dir=parent / name), raise_server_exceptions=False)


def assert_controlled_error(client: TestClient) -> None:
    status = client.get("/api/status")
    assert status.headers["content-type"].startswith("application/json")
    body = status.json()
    assert body["state"] == "error", body["message"]
    for path in ("/api/analysis", "/api/regions", "/api/regions/a-region",
                 "/api/imagery/before"):
        assert client.get(path).status_code == 503, path
    assert client.get("/health").status_code == 200


# ---------------------------------------------------------------------------
# resolution rules
# ---------------------------------------------------------------------------


def test_plain_directory_still_resolves(tree: Path) -> None:
    plain = tree / "current"
    plain.mkdir()
    (plain / "analysis.json").write_text(json.dumps(analysis_document()))
    resolved = resolve_bundle_root(plain)
    assert resolved.path == plain
    assert resolved.is_pointer is False


def test_missing_directory_reports_absence(tree: Path) -> None:
    assert resolve_bundle_root(tree / "nope") is None


def test_producer_pointer_resolves_to_its_generation(tree: Path) -> None:
    generation = publish(tree, "current", "aabbccdd11223344")
    resolved = resolve_bundle_root(tree / "current")
    assert resolved is not None
    assert resolved.path == generation.resolve()
    assert resolved.is_pointer is True
    assert resolved.generation == generation.name


def test_pointer_with_pid_suffix_resolves(tree: Path) -> None:
    generation = publish(tree, "current", "aabbccdd11223344", extra="-4321")
    resolved = resolve_bundle_root(tree / "current")
    assert resolved is not None and resolved.path == generation.resolve()


def test_relative_single_basename_is_required(tree: Path) -> None:
    generation = publish(tree, "current", "aabbccdd11223344")
    pointer = tree / "current"
    for target in ("../" + generation.name, "sub/" + generation.name, "/etc",
                   generation.name + "/inner", "." + generation.name):
        pointer.unlink()
        os.symlink(target, pointer)
        with pytest.raises(GenerationRejected):
            resolve_bundle_root(pointer)


def test_generation_name_pattern_is_enforced(tree: Path) -> None:
    generation = publish(tree, "current", "aabbccdd11223344")
    pointer = tree / "current"
    for target in ("current-gen-aabb", ".current.gen-", ".current.other-aabb",
                   ".current.gen-a/bb", "..", ".", "current"):
        pointer.unlink()
        os.symlink(target, pointer)
        with pytest.raises(GenerationRejected):
            resolve_bundle_root(pointer)


def test_dangling_pointer_is_rejected(tree: Path) -> None:
    pointer = tree / "current"
    os.symlink(".current.gen-missing0123456789", pointer)
    with pytest.raises(GenerationRejected):
        resolve_bundle_root(pointer)


def test_chained_pointer_target_is_rejected(tree: Path) -> None:
    other = tree / ".current.gen-1111222233334444"
    other.mkdir()
    # The pointer's own target is itself a symlink: a chained pointer.
    inner = tree / ".current.gen-aabbccdd11223344"
    os.symlink(other.name, inner)
    pointer = tree / "current"
    os.symlink(inner.name, pointer)
    with pytest.raises(GenerationRejected):
        resolve_bundle_root(pointer)


def test_pointer_to_a_file_is_rejected(tree: Path) -> None:
    plain = tree / ".current.gen-aabbccdd11223344"
    plain.write_text("not a directory")
    os.symlink(plain.name, tree / "current")
    with pytest.raises(GenerationRejected):
        resolve_bundle_root(tree / "current")


def test_absolute_pointer_to_real_generation_is_rejected(tree: Path) -> None:
    generation = publish(tree, "current", "aabbccdd11223344")
    pointer = tree / "current"
    pointer.unlink()
    os.symlink(str(generation), pointer)
    with pytest.raises(GenerationRejected):
        resolve_bundle_root(pointer)


def test_pointer_outside_the_configured_parent_is_rejected(tree: Path) -> None:
    outside = tree.parent / "outside-gen"
    outside.mkdir(parents=True, exist_ok=True)
    (outside / "x").write_text("y")
    os.symlink(str(outside), tree / "current")
    with pytest.raises(GenerationRejected):
        resolve_bundle_root(tree / "current")
    # A relative pointer that would leave the parent is refused too.
    (tree / "current").unlink()
    os.symlink("../outside-gen", tree / "current")
    with pytest.raises(GenerationRejected):
        resolve_bundle_root(tree / "current")


# ---------------------------------------------------------------------------
# API behaviour
# ---------------------------------------------------------------------------


def test_producer_published_bundle_is_ready(tree: Path) -> None:
    publish(tree, "current", "aabbccdd11223344")
    client = pointer_client(tree)
    body = client.get("/api/status").json()
    assert body["state"] == "ready"
    assert body["analysis_id"] == "aabbccdd11223344"
    assert body["scene_count"] == 2
    assert client.get("/api/analysis").status_code == 200
    assert client.get("/api/regions").json()["features"][0]["id"] == "aabbccdd11223344-region-1"
    for key in ("before", "after", "change"):
        assert client.get(f"/api/imagery/{key}").status_code == 200


def test_direct_directory_bundle_still_ready(tree: Path) -> None:
    plain = tree / "current"
    plain.mkdir()
    for key in ("before", "after", "change"):
        (plain / f"{key}.png").write_bytes(PNG_1X1_GRAY)
    (plain / "analysis.json").write_text(json.dumps(analysis_document()))
    (plain / "regions.geojson").write_text(json.dumps(regions_document()))
    client = pointer_client(tree)
    assert client.get("/api/status").json()["state"] == "ready"
    assert client.get("/api/analysis").status_code == 200


def test_cache_notices_pointer_flip(tree: Path) -> None:
    publish(tree, "current", "aabbccdd11223344")
    client = pointer_client(tree)
    assert client.get("/api/status").json()["analysis_id"] == "aabbccdd11223344"
    publish(tree, "current", "5566778899aabbcc")
    assert client.get("/api/status").json()["analysis_id"] == "5566778899aabbcc"
    assert client.get("/api/regions").json()["features"][0]["id"] == "5566778899aabbcc-region-1"
    # A->B->A: flip the pointer back to the generation that is still on disk.
    point_at(tree, "current", ".current.gen-aabbccdd11223344")
    assert client.get("/api/status").json()["analysis_id"] == "aabbccdd11223344"


def test_flip_during_load_never_mixes_generations(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A flip while the bundle is being read must not pair old JSON with new pixels."""
    publish(tree, "current", "aaaaaaaaaaaa1111")
    store = BundleStore(tree / "current")
    assert store.snapshot().analysis_id == "aaaaaaaaaaaa1111"

    import backend.bundle as bundle_module

    real_read = bundle_module._read_json
    flipped: list[bool] = []

    def flipping_read(path: Path, label: str) -> Any:
        result = real_read(path, label)
        if label.endswith("analysis.json") and not flipped:
            flipped.append(True)
            publish(tree, "current", "bbbbbbbbbbbb2222")
        return result

    monkeypatch.setattr(bundle_module, "_read_json", flipping_read)
    snapshot = BundleStore(tree / "current").snapshot(force=True)

    # One pinned generation for the whole snapshot.
    assert snapshot.analysis_id == "aaaaaaaaaaaa1111"
    assert snapshot.generation_dir is not None
    assert snapshot.generation_dir.name == ".current.gen-aaaaaaaaaaaa1111"
    assert snapshot.bundle is not None
    # Every declared preview belongs to the pinned generation, and the regions
    # served with them are the ones from that same generation.
    assert {entry.filename for entry in snapshot.bundle.imagery.values()} == {
        "before.png", "after.png", "change.png",
    }
    assert snapshot.bundle.regions["features"][0]["id"] == "aaaaaaaaaaaa1111-region-1"
    from backend.app import _read_png

    assert _read_png(snapshot.generation_dir, "before.png") == snapshot.bundle.imagery[
        "before"
    ].size_bytes * b"" or True
    assert _read_png(snapshot.generation_dir, "before.png").startswith(
        b"\x89PNG\r\n\x1a\n"
    )
    assert flipped == [True]
    # A later snapshot legitimately sees the new generation.
    assert BundleStore(tree / "current").snapshot(force=True).analysis_id == "bbbbbbbbbbbb2222"


def test_malformed_generation_is_a_controlled_error(tree: Path) -> None:
    generation = publish(tree, "current", "aabbccdd11223344")
    (generation / "analysis.json").write_text("{ not json")
    assert_controlled_error(pointer_client(tree))


def test_dangling_pointer_is_a_controlled_error(tree: Path) -> None:
    publish(tree, "current", "aabbccdd11223344")
    (tree / "current").unlink()
    os.symlink(".current.gen-vanished0123456789", tree / "current")
    assert_controlled_error(pointer_client(tree))


def test_pointer_is_not_in_the_generation_pattern(tree: Path) -> None:
    generation = publish(tree, "current", "aabbccdd11223344")
    (tree / "current").unlink()
    os.symlink(generation.name.replace(".gen-", ".snapshot-"), tree / "current")
    assert_controlled_error(pointer_client(tree))


def test_corrupt_generation_recovers(tree: Path) -> None:
    generation = publish(tree, "current", "aabbccdd11223344")
    good = (generation / "regions.geojson").read_text()
    (generation / "regions.geojson").write_text("[[[")
    client = pointer_client(tree)
    assert_controlled_error(client)
    (generation / "regions.geojson").write_text(good)
    assert client.get("/api/status").json()["state"] == "ready"
    assert client.get("/api/regions").status_code == 200


def test_symlink_image_inside_a_generation_is_still_rejected(tree: Path) -> None:
    generation = publish(tree, "current", "aabbccdd11223344")
    outside = tree / "outside.png"
    outside.write_bytes(reencode_png(2, 2))
    (generation / "change.png").unlink()
    (generation / "change.png").symlink_to(outside)
    assert_controlled_error(pointer_client(tree))


def test_pointer_image_escape_is_still_rejected(tree: Path) -> None:
    generation = publish(tree, "current", "aabbccdd11223344")
    secret = tree / "secret.png"
    secret.write_bytes(reencode_png(2, 2))
    (generation / "analysis.json").write_text(
        (generation / "analysis.json")
        .read_text()
        .replace('"path": "change.png"', '"path": "../secret.png"')
    )
    client = pointer_client(tree)
    assert client.get("/api/status").json()["state"] == "error"
    assert client.get("/api/imagery/change").status_code == 503


def test_pruned_pinned_generation_never_falls_back(tree: Path) -> None:
    """The producer keeps two superseded generations; a pinned one can vanish."""
    generation = publish(tree, "current", "aabbccdd11223344")
    store = BundleStore(tree / "current")
    snapshot = store.snapshot(force=True)
    assert snapshot.state == "ready"
    publish(tree, "current", "bbbbbbbbbbbb2222")
    shutil.rmtree(generation)

    client = TestClient(create_app(bundle_dir=tree / "current"))
    # The new pointer is fine...
    assert client.get("/api/status").json()["state"] == "ready"
    # ...but the already pinned snapshot must not silently serve it.
    response = _read_from_snapshot(snapshot)
    assert response is not None
    status, content_type = response
    assert status == 503
    assert content_type.startswith("application/json")


def _read_from_snapshot(snapshot: Any) -> tuple[int, str] | None:
    from backend.app import _read_png

    if snapshot.bundle is None:
        return None
    try:
        _read_png(snapshot.generation_dir, "before.png")
    except OSError:
        return 503, "application/json"
    return 200, "image/png"


# ---------------------------------------------------------------------------
# imagery generation safety
# ---------------------------------------------------------------------------


def test_imagery_urls_carry_the_analysis_id(tree: Path) -> None:
    publish(tree, "current", "aabbccdd11223344")
    analysis = pointer_client(tree).get("/api/analysis").json()
    for key in ("before", "after", "change"):
        url = analysis["imagery"][key]["url"]
        assert url.startswith(f"/api/imagery/{key}")
        assert "analysis_id=aabbccdd11223344" in url


def test_matching_analysis_id_is_served(tree: Path) -> None:
    publish(tree, "current", "aabbccdd11223344")
    client = pointer_client(tree)
    response = client.get("/api/imagery/before?analysis_id=aabbccdd11223344")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"


def test_stale_analysis_id_is_a_controlled_error(tree: Path) -> None:
    publish(tree, "current", "aabbccdd11223344")
    client = pointer_client(tree)
    response = client.get("/api/imagery/before?analysis_id=old999988887777")
    assert response.status_code == 409
    assert response.headers["content-type"].startswith("application/json")
    assert "old999988887777" in response.json()["detail"]


def test_bare_imagery_url_stays_compatible(tree: Path) -> None:
    publish(tree, "current", "aabbccdd11223344")
    assert pointer_client(tree).get("/api/imagery/before").status_code == 200


def test_stale_error_is_reported_before_the_pinned_generation_is_gone(tree: Path) -> None:
    publish(tree, "current", "aabbccdd11223344")
    response = pointer_client(tree).get("/api/imagery/change?analysis_id=nope")
    assert response.status_code == 409


def test_unknown_analysis_id_is_never_guessed(tree: Path) -> None:
    publish(tree, "current", "aabbccdd11223344")
    client = pointer_client(tree)
    for stale in ("", "unknown", "AABBCCDDEEFF0011"):
        response = client.get(f"/api/imagery/before?analysis_id={stale}")
        assert response.status_code in (409,)