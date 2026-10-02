"""API contract tests for the ForestWatch read-only backend."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from conftest import (
    PNG_1X1_GRAY,
    analysis_document,
    png_chunk_count,
    png_size,
    regions_document,
    reencode_png,
    write_bundle,
)

ALLOWED_KEYS = {"before", "after", "change"}


def test_health_is_liveness_and_needs_no_bundle(empty_client: TestClient) -> None:
    response = empty_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "forestwatch-api"}


def test_status_ready_for_valid_bundle(client: TestClient) -> None:
    response = client.get("/api/status")
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "ready"
    assert body["schema_version"] == 1
    assert body["analysis_id"] == "synthetic-analysis-0001"
    assert body["scene_count"] == 2
    assert isinstance(body["message"], str) and body["message"]


def test_status_awaiting_analysis_when_bundle_missing(empty_client: TestClient) -> None:
    response = empty_client.get("/api/status")
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "awaiting_analysis"
    assert body["analysis_id"] is None
    assert body["scene_count"] == 0
    assert body["schema_version"] == 1
    assert body["message"]


def test_status_awaiting_analysis_when_bundle_dir_is_empty(tmp_path: Path) -> None:
    empty = tmp_path / "empty-bundle"
    empty.mkdir()
    response = TestClient(create_app(bundle_dir=empty)).get("/api/status")
    assert response.status_code == 200
    assert response.json()["state"] == "awaiting_analysis"


def test_data_endpoints_404_with_useful_message_when_bundle_missing(
    empty_client: TestClient,
) -> None:
    for path in ("/api/analysis", "/api/regions", "/api/regions/synthetic-region-1"):
        response = empty_client.get(path)
        assert response.status_code == 404, path
        body = response.json()
        assert "detail" in body
        assert body["detail"]
    assert empty_client.get("/api/imagery/before").status_code == 404


def test_analysis_shape_and_imagery_urls(client: TestClient) -> None:
    response = client.get("/api/analysis")
    assert response.status_code == 200
    body = response.json()
    assert set(body["imagery"]) == ALLOWED_KEYS
    for key, entry in body["imagery"].items():
        assert set(entry) == {"path", "bounds", "label", "url"}
        assert entry["url"] == f"/api/imagery/{key}"
    assert body["method"]["change_definition"] == "10*log10(after/before)"
    assert body["metrics"]["scene_count"] == 2
    assert body["limitations"]


def test_regions_returns_geojson_feature_collection(client: TestClient) -> None:
    response = client.get("/api/regions")
    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "FeatureCollection"
    ids = [f["id"] for f in body["features"]]
    assert ids == ["synthetic-region-1", "synthetic-region-2"]
    assert [f["properties"]["region_id"] for f in body["features"]] == ids


def test_region_detail_returns_geojson_feature(client: TestClient) -> None:
    response = client.get("/api/regions/synthetic-region-1")
    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "Feature"
    assert body["id"] == "synthetic-region-1"
    assert body["properties"]["area_ha"] == 1.25
    assert body["geometry"]["type"] == "Polygon"


def test_region_detail_multipolygon(client: TestClient) -> None:
    body = client.get("/api/regions/synthetic-region-2").json()
    assert body["geometry"]["type"] == "MultiPolygon"


def test_unknown_region_id_404(client: TestClient) -> None:
    response = client.get("/api/regions/does-not-exist")
    assert response.status_code == 404
    assert response.json()["detail"]


def test_regions_do_not_expose_duplicate_or_unknown_ids(client: TestClient) -> None:
    body = client.get("/api/regions").json()
    ids = [f["id"] for f in body["features"]]
    assert len(ids) == len(set(ids))
    for feature in body["features"]:
        assert feature["id"] == feature["properties"]["region_id"]


def test_region_id_path_traversal_is_rejected(client: TestClient) -> None:
    # The region id is used as a dictionary key only; nothing on it is ever
    # turned into a filesystem path.
    for bad in ("%2e%2e%2fanalysis", "%2e%2e%2f%2e%2e%2fetc%2fpasswd", "..%2fanalysis.json"):
        response = client.get(f"/api/regions/{bad}")
        assert response.status_code == 404, bad
        assert "secret" not in response.text
        assert "/tmp" not in response.text
    for bad in ("..", "../analysis", "a/../../analysis"):
        response = client.get(f"/api/regions/{bad}")
        assert response.status_code < 500, bad
        assert "secret" not in response.text


def test_null_two_date_metrics_are_preserved_not_zeroed(client: TestClient) -> None:
    props = client.get("/api/regions/synthetic-region-1").json()["properties"]
    assert props["persistence"]["rate"] is None
    assert props["historical_anomaly"] is None
    assert props["persistence"]["status"] == "not_evaluable"
    series = props["time_series"]
    assert series[0]["change_from_baseline_db"] is None
    assert series[1]["change_from_baseline_db"] == -2.75


def test_observed_persistence_region_is_unchanged(client: TestClient) -> None:
    props = client.get("/api/regions/synthetic-region-2").json()["properties"]
    assert props["persistence"] == {
        "status": "observed",
        "observations_after_detection": 2,
        "changed_observations": 2,
        "rate": 1.0,
    }
    assert props["historical_anomaly"] is None
    assert props["priority_units"] == "dB sqrt(ha)"
    assert props["priority_formula"] == "magnitude_db * sqrt(area_ha)"


def test_imagery_serves_png_bytes(client: TestClient) -> None:
    response = client.get("/api/imagery/before")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == PNG_1X1_GRAY
    assert png_size(response.content) == (1, 1)
    assert png_chunk_count(response.content) >= 3


def test_all_three_imagery_keys_served(client: TestClient) -> None:
    for key in ("before", "after", "change"):
        response = client.get(f"/api/imagery/{key}")
        assert response.status_code == 200, key
        assert response.headers["content-type"] == "image/png"
        assert response.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_unknown_imagery_key_404(client: TestClient) -> None:
    assert client.get("/api/imagery/sidecar").status_code == 404
    assert client.get("/api/imagery/../../etc/passwd").status_code in (400, 404)


def test_imagery_only_serves_declared_files(tmp_path: Path) -> None:
    bundle = write_bundle(tmp_path)
    # Extra file inside the bundle that the manifest does not declare.
    (bundle / "notes.txt").write_text("not declared")
    (bundle / "internal-notes.json").write_text('{"secret": "value"}')
    client = TestClient(create_app(bundle_dir=bundle))
    assert client.get("/api/imagery/notes.txt").status_code == 404
    assert client.get("/api/imagery/internal-notes.json").status_code == 404
    assert client.get("/api/imagery/before").status_code == 200


def test_imagery_traversal_cannot_escape_bundle(tmp_path: Path) -> None:
    bundle = write_bundle(tmp_path)
    outside = tmp_path / "outside.png"
    outside.write_bytes(reencode_png(2, 2))
    client = TestClient(create_app(bundle_dir=bundle))
    for bad in (
        "../outside.png",
        "..%2Foutside.png",
        "/etc/passwd",
        "./../../outside.png",
    ):
        response = client.get(f"/api/imagery/{bad}")
        assert response.status_code in (400, 404, 422), bad
        assert b"outside.png" not in response.content or response.status_code != 200
    assert outside.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_symlinked_imagery_is_rejected(tmp_path: Path) -> None:
    bundle = write_bundle(tmp_path)
    target = tmp_path / "target.png"
    target.write_bytes(PNG_1X1_GRAY)
    (bundle / "before.png").unlink()
    (bundle / "before.png").symlink_to(target)
    client = TestClient(create_app(bundle_dir=bundle))
    status = client.get("/api/status").json()["state"]
    assert status == "error"
    response = client.get("/api/imagery/before")
    assert response.status_code == 503
    assert target.read_bytes() == PNG_1X1_GRAY


def test_symlinked_bundle_dir_is_rejected(tmp_path: Path) -> None:
    real = write_bundle(tmp_path)
    link = tmp_path / "linked-bundle"
    link.symlink_to(real)
    client = TestClient(create_app(bundle_dir=link))
    assert client.get("/api/status").json()["state"] == "error"
    assert client.get("/api/analysis").status_code == 503


def test_no_raw_directory_listing(client: TestClient) -> None:
    for path in ("/", "/api", "/api/imagery", "/api/regions/"):
        response = client.get(path)
        assert response.status_code in (200, 404, 307, 405)
        assert "before.png" not in response.text or response.status_code == 200
    listing = client.get("/api/imagery")
    assert listing.status_code in (404, 405)


def test_no_write_or_run_endpoints(client: TestClient) -> None:
    for method in ("post", "put", "patch", "delete"):
        response = getattr(client, method)("/api/analysis")
        assert response.status_code == 405, method
    for path in ("/api/run", "/api/acquire", "/api/process", "/api/jobs"):
        response = client.post(path, json={})
        assert response.status_code == 404, path


def test_cors_allows_localhost_frontend(client: TestClient) -> None:
    response = client.get("/api/status", headers={"Origin": "http://localhost:3000"})
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"
    response = client.get("/api/status", headers={"Origin": "http://localhost:5173"})
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_cors_denies_unknown_origin(client: TestClient) -> None:
    response = client.get(
        "/api/status", headers={"Origin": "https://evil.example.com"}
    )
    assert "access-control-allow-origin" not in response.headers


def test_cors_origin_list_is_configurable(tmp_path: Path) -> None:
    bundle = write_bundle(tmp_path)
    app = create_app(bundle_dir=bundle)
    app.state.cors_origins = ["http://localhost:4173"]
    client = TestClient(app)
    assert client.get("/api/status", headers={"Origin": "http://localhost:4173"}).headers.get(
        "access-control-allow-origin"
    ) == "http://localhost:4173"
    assert "access-control-allow-origin" not in client.get(
        "/api/status", headers={"Origin": "http://localhost:3000"}
    ).headers


def test_responses_never_leak_filesystem_paths(tmp_path: Path) -> None:
    bundle = write_bundle(tmp_path)
    (bundle / "analysis.json").write_text("{ this is not json")
    client = TestClient(create_app(bundle_dir=bundle))
    for path in ("/api/status", "/api/analysis", "/api/regions"):
        body = client.get(path).text
        assert str(tmp_path) not in body
        assert str(bundle) not in body
        assert "/workspace" not in body
        assert "Traceback" not in body
        assert "json.decoder" not in body


def test_bundle_dir_from_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = write_bundle(tmp_path)
    monkeypatch.setenv("FORESTWATCH_BUNDLE_DIR", str(bundle))
    client = TestClient(create_app())
    assert client.get("/api/status").json()["state"] == "ready"
    assert client.get("/api/analysis").status_code == 200


def test_app_module_exports_app() -> None:
    import importlib

    module = importlib.import_module("backend.app")
    assert module.app is not None
    assert module.create_app is not None
    from fastapi import FastAPI

    assert isinstance(module.app, FastAPI)


def test_create_app_accepts_str_path(tmp_path: Path) -> None:
    bundle = write_bundle(tmp_path)
    client = TestClient(create_app(bundle_dir=str(bundle)))
    assert client.get("/api/status").json()["state"] == "ready"


def test_ordering_is_stable_across_requests(client: TestClient) -> None:
    first = [f["id"] for f in client.get("/api/regions").json()["features"]]
    for _ in range(3):
        assert [f["id"] for f in client.get("/api/regions").json()["features"]] == first


def test_content_types_are_json(client: TestClient) -> None:
    for path in ("/api/status", "/api/analysis", "/api/regions"):
        assert client.get(path).headers["content-type"].startswith("application/json")


def test_openapi_available_for_frontend(client: TestClient) -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    for path in ("/health", "/api/status", "/api/analysis", "/api/regions", "/api/imagery/{key}"):
        assert path in paths
    for methods in paths.values():
        assert set(methods) <= {"get", "head", "options"}


def test_bundle_json_is_served_verbatim_without_invention(client: TestClient) -> None:
    body = client.get("/api/analysis").json()
    original = analysis_document()
    original["imagery"] = {
        key: {**value, "url": f"/api/imagery/{key}"}
        for key, value in original["imagery"].items()
    }
    assert body == original


def test_regions_json_is_served_verbatim_without_invention(client: TestClient) -> None:
    assert client.get("/api/regions").json() == regions_document()


def test_analysis_is_not_rewritten_with_absolute_paths(client: TestClient) -> None:
    body = client.get("/api/analysis").json()
    for entry in body["imagery"].values():
        assert not os.path.isabs(entry["path"])
        assert ".." not in entry["path"]


def test_detail_route_prefers_first_matching_feature(client: TestClient) -> None:
    body = client.get("/api/regions/synthetic-region-1").json()
    assert body["properties"]["region_id"] == "synthetic-region-1"


def test_json_content_sniffing_not_required(client: TestClient) -> None:
    response = client.get("/api/regions/synthetic-region-1")
    assert response.headers["content-type"].startswith("application/json")
    json.loads(response.text)


# ---------------------------------------------------------------------------
# adjudicated contract: analysis_area_ha, baseline_at, observation_interval
# ---------------------------------------------------------------------------


def test_analysis_metrics_include_analysis_area(client: TestClient) -> None:
    metrics = client.get("/api/analysis").json()["metrics"]
    assert metrics["analysis_area_ha"] == 102.0
    assert (
        metrics["valid_area_ha"] + metrics["not_evaluable_area_ha"]
        == metrics["analysis_area_ha"]
    )
    assert metrics["total_changed_area_ha"] <= metrics["valid_area_ha"]


def test_region_uses_baseline_at_and_observation_interval(client: TestClient) -> None:
    properties = client.get("/api/regions/synthetic-region-1").json()["properties"]
    assert "last_observed_unchanged_at" not in properties
    assert "onset_interval" not in properties
    assert properties["baseline_at"] == "2026-01-15T10:00:00Z"
    assert properties["observation_interval"] == {
        "start": "2026-01-15T10:00:00Z",
        "end": "2026-03-20T10:00:00Z",
    }
    assert properties["detected_at"] == "2026-03-20T10:00:00Z"


def test_change_db_and_magnitude_db_are_distinct_fields(client: TestClient) -> None:
    properties = client.get("/api/regions/synthetic-region-1").json()["properties"]
    assert properties["change_db"] == -2.75
    assert properties["magnitude_db"] == 2.75


def test_imagery_bounds_equal_analysis_bbox(client: TestClient) -> None:
    analysis = client.get("/api/analysis").json()
    for entry in analysis["imagery"].values():
        assert entry["bounds"] == analysis["bbox"]


def test_no_stability_or_onset_language_in_responses(client: TestClient) -> None:
    body = client.get("/api/regions").text + client.get("/api/analysis").text
    for phrase in ("last_observed_unchanged", "onset_interval", "stable_since"):
        assert phrase not in body


def test_openapi_documents_analysis_area_and_new_region_fields(client: TestClient) -> None:
    components = client.get("/openapi.json").json()["components"]["schemas"]
    assert "analysis_area_ha" in components["MetricsResponse"]["properties"]
    region = components["RegionPropertiesResponse"]["properties"]
    assert "baseline_at" in region
    assert "observation_interval" in region
    assert "last_observed_unchanged_at" not in region
    assert "onset_interval" not in region
