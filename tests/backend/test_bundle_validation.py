"""Bundle validation tests: malformed/incomplete bundles must never be served."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from fastapi.testclient import TestClient

from backend.app import create_app
from conftest import analysis_document, regions_document, write_bundle


def _client_for(tmp_path: Path, analysis: dict[str, Any] | None, regions: Any = None) -> TestClient:
    bundle = write_bundle(tmp_path, analysis=analysis, regions=regions)
    return TestClient(create_app(bundle_dir=bundle))


def assert_error_state(client: TestClient) -> None:
    status = client.get("/api/status")
    assert status.status_code == 200
    body = status.json()
    assert body["state"] == "error"
    assert body["analysis_id"] is None
    assert body["scene_count"] == 0
    assert body["message"]
    for path in ("/api/analysis", "/api/regions", "/api/regions/synthetic-region-1"):
        assert client.get(path).status_code == 503, path
    for key in ("before", "after", "change"):
        assert client.get(f"/api/imagery/{key}").status_code == 503, key


def mutate(change: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    document = analysis_document()
    change(document)
    return document


def test_valid_bundle_is_ready(client: TestClient) -> None:
    assert client.get("/api/status").json()["state"] == "ready"


def test_malformed_analysis_json(tmp_path: Path) -> None:
    bundle = write_bundle(tmp_path)
    (bundle / "analysis.json").write_text("{not json at all")
    assert_error_state(TestClient(create_app(bundle_dir=bundle)))


def test_malformed_regions_geojson(tmp_path: Path) -> None:
    bundle = write_bundle(tmp_path)
    (bundle / "regions.geojson").write_text("[[[")
    assert_error_state(TestClient(create_app(bundle_dir=bundle)))


def test_missing_analysis_file(tmp_path: Path) -> None:
    bundle = write_bundle(tmp_path)
    (bundle / "analysis.json").unlink()
    assert_error_state(TestClient(create_app(bundle_dir=bundle)))


def test_missing_regions_file(tmp_path: Path) -> None:
    bundle = write_bundle(tmp_path)
    (bundle / "regions.geojson").unlink()
    assert_error_state(TestClient(create_app(bundle_dir=bundle)))


def test_missing_declared_png(tmp_path: Path) -> None:
    bundle = write_bundle(tmp_path)
    (bundle / "change.png").unlink()
    assert_error_state(TestClient(create_app(bundle_dir=bundle)))


def test_imagery_path_is_a_directory(tmp_path: Path) -> None:
    bundle = write_bundle(tmp_path)
    (bundle / "change.png").unlink()
    (bundle / "change.png").mkdir()
    assert_error_state(TestClient(create_app(bundle_dir=bundle)))


def test_incomplete_imagery_key(tmp_path: Path) -> None:
    document = mutate(lambda d: d["imagery"].pop("change"))
    assert_error_state(_client_for(tmp_path, document))


def test_extra_imagery_key_rejected(tmp_path: Path) -> None:
    document = mutate(
        lambda d: d["imagery"].update(
            {"diff": {"path": "change.png", "bounds": d["bbox"], "label": "x"}}
        )
    )
    assert_error_state(_client_for(tmp_path, document))


def test_wrong_schema_version(tmp_path: Path) -> None:
    assert_error_state(_client_for(tmp_path, mutate(lambda d: d.update(schema_version=2))))
    assert_error_state(_client_for(tmp_path, mutate(lambda d: d.update(schema_version="1"))))
    assert_error_state(_client_for(tmp_path, mutate(lambda d: d.pop("schema_version"))))


def test_scene_count_mismatch(tmp_path: Path) -> None:
    assert_error_state(_client_for(tmp_path, mutate(lambda d: d["metrics"].update(scene_count=3))))


def test_region_count_mismatch(tmp_path: Path) -> None:
    assert_error_state(
        _client_for(tmp_path, mutate(lambda d: d["metrics"].update(region_count=7)))
    )


def test_single_date_not_allowed(tmp_path: Path) -> None:
    assert_error_state(_client_for(tmp_path, mutate(lambda d: d["scenes"].pop())))


def test_empty_scenes_rejected(tmp_path: Path) -> None:
    assert_error_state(
        _client_for(tmp_path, mutate(lambda d: (d.__setitem__("scenes", []), d["metrics"].update(scene_count=0))))
    )


def test_duplicate_acquisition_dates_rejected(tmp_path: Path) -> None:
    assert_error_state(
        _client_for(
            tmp_path,
            mutate(lambda d: d["scenes"][1].update(acquired_at=d["scenes"][0]["acquired_at"])),
        )
    )


def test_non_utc_date_rejected(tmp_path: Path) -> None:
    assert_error_state(
        _client_for(tmp_path, mutate(lambda d: d["scenes"][0].update(acquired_at="2026-01-15T10:00:00+02:00")))
    )
    assert_error_state(
        _client_for(tmp_path, mutate(lambda d: d["scenes"][0].update(acquired_at="2026-01-15")))
    )
    assert_error_state(
        _client_for(tmp_path, mutate(lambda d: d["scenes"][0].update(acquired_at="not-a-date")))
    )


def test_nan_metric_rejected(tmp_path: Path) -> None:
    analysis = analysis_document()
    analysis["metrics"]["total_changed_area_ha"] = float("nan")
    bundle = write_bundle(tmp_path, analysis=analysis)
    assert_error_state(TestClient(create_app(bundle_dir=bundle)))


def test_infinite_metric_rejected(tmp_path: Path) -> None:
    analysis = analysis_document()
    analysis["metrics"]["total_changed_area_ha"] = float("inf")
    bundle = write_bundle(tmp_path, analysis=analysis)
    assert_error_state(TestClient(create_app(bundle_dir=bundle)))


def test_nan_geometry_coordinate_rejected(tmp_path: Path) -> None:
    regions = regions_document()
    regions["features"][0]["geometry"]["coordinates"][0][1][0] = float("nan")
    bundle = write_bundle(tmp_path, regions=regions)
    assert_error_state(TestClient(create_app(bundle_dir=bundle)))


def test_nan_region_property_rejected(tmp_path: Path) -> None:
    regions = regions_document()
    regions["features"][0]["properties"]["area_ha"] = float("nan")
    bundle = write_bundle(tmp_path, regions=regions)
    assert_error_state(TestClient(create_app(bundle_dir=bundle)))


def test_non_finite_json_literal_is_rejected(tmp_path: Path) -> None:
    bundle = write_bundle(tmp_path)
    raw = (bundle / "analysis.json").read_text()
    (bundle / "analysis.json").write_text(
        raw.replace('"total_changed_area_ha": 4.5', '"total_changed_area_ha": NaN')
    )
    assert_error_state(TestClient(create_app(bundle_dir=bundle)))


def test_negative_area_rejected(tmp_path: Path) -> None:
    regions = regions_document()
    regions["features"][0]["properties"]["area_ha"] = -1.0
    assert_error_state(_client_for(tmp_path, None, regions))


def test_valid_fraction_out_of_range_rejected(tmp_path: Path) -> None:
    regions = regions_document()
    regions["features"][0]["properties"]["time_series"][0]["valid_fraction"] = 1.4
    assert_error_state(_client_for(tmp_path, None, regions))


def test_persistence_status_must_be_permitted(tmp_path: Path) -> None:
    regions = regions_document()
    regions["features"][0]["properties"]["persistence"]["status"] = "maybe"
    assert_error_state(_client_for(tmp_path, None, regions))


def test_null_two_date_metrics_allowed(tmp_path: Path) -> None:
    regions = regions_document()
    regions["features"][0]["properties"]["historical_anomaly"] = None
    regions["features"][0]["properties"]["persistence"]["rate"] = None
    bundle = write_bundle(tmp_path, regions=regions)
    client = TestClient(create_app(bundle_dir=bundle))
    assert client.get("/api/status").json()["state"] == "ready"


def test_missing_two_date_metrics_rejected(tmp_path: Path) -> None:
    regions = regions_document()
    del regions["features"][0]["properties"]["historical_anomaly"]
    assert_error_state(_client_for(tmp_path, None, regions))


def test_null_persistence_rate_requires_observed_counts(tmp_path: Path) -> None:
    regions = regions_document()
    regions["features"][0]["properties"]["persistence"]["status"] = "observed"
    assert_error_state(_client_for(tmp_path, None, regions))


def test_feature_id_region_id_mismatch_rejected(tmp_path: Path) -> None:
    regions = regions_document()
    regions["features"][0]["id"] = "other-id"
    assert_error_state(_client_for(tmp_path, None, regions))


def test_duplicate_region_ids_rejected(tmp_path: Path) -> None:
    regions = regions_document()
    regions["features"][1]["id"] = "synthetic-region-1"
    regions["features"][1]["properties"]["region_id"] = "synthetic-region-1"
    assert_error_state(_client_for(tmp_path, None, regions))


def test_demo_region_id_must_exist(tmp_path: Path) -> None:
    assert_error_state(_client_for(tmp_path, mutate(lambda d: d.update(demo_region_id="nope"))))
    document = analysis_document()
    document["demo_region_id"] = None
    bundle = write_bundle(tmp_path, analysis=document)
    assert TestClient(create_app(bundle_dir=bundle)).get("/api/status").json()["state"] == "ready"


def test_bbox_out_of_lon_lat_range_rejected(tmp_path: Path) -> None:
    assert_error_state(_client_for(tmp_path, mutate(lambda d: d.update(bbox=[-200.0, -3.5, -60.2, -3.2]))))
    assert_error_state(_client_for(tmp_path, mutate(lambda d: d.update(bbox=[-60.5, -95.0, -60.2, -3.2]))))
    assert_error_state(_client_for(tmp_path, mutate(lambda d: d.update(bbox=[-60.5, -3.5, -60.2, 95.0]))))


def test_bbox_must_be_ordered_and_four_values(tmp_path: Path) -> None:
    assert_error_state(_client_for(tmp_path, mutate(lambda d: d.update(bbox=[-60.2, -3.5, -60.5, -3.2]))))
    assert_error_state(_client_for(tmp_path, mutate(lambda d: d.update(bbox=[-60.5, -3.2, -60.2, -3.5]))))
    assert_error_state(_client_for(tmp_path, mutate(lambda d: d.update(bbox=[-60.5, -3.5, -60.2]))))


def test_imagery_bounds_invalid_rejected(tmp_path: Path) -> None:
    assert_error_state(
        _client_for(
            tmp_path,
            mutate(lambda d: d["imagery"]["before"].update(bounds=[-60.5, -3.5, -60.6, -3.2])),
        )
    )


def test_polygon_coordinate_out_of_range_rejected(tmp_path: Path) -> None:
    regions = regions_document()
    regions["features"][0]["geometry"]["coordinates"][0][0] = [-190.0, -3.4]
    assert_error_state(_client_for(tmp_path, None, regions))


def test_unclosed_polygon_ring_rejected(tmp_path: Path) -> None:
    regions = regions_document()
    regions["features"][0]["geometry"]["coordinates"][0].pop()
    assert_error_state(_client_for(tmp_path, None, regions))


def test_too_few_polygon_positions_rejected(tmp_path: Path) -> None:
    regions = regions_document()
    regions["features"][0]["geometry"]["coordinates"] = [[[-60.4, -3.4], [-60.4, -3.4]]]
    assert_error_state(_client_for(tmp_path, None, regions))


def test_non_numeric_coordinate_rejected(tmp_path: Path) -> None:
    regions = regions_document()
    regions["features"][0]["geometry"]["coordinates"][0][0] = ["-60.40", -3.40]
    assert_error_state(_client_for(tmp_path, None, regions))


def test_unsupported_geometry_type_rejected(tmp_path: Path) -> None:
    regions = regions_document()
    regions["features"][0]["geometry"] = {"type": "Point", "coordinates": [-60.4, -3.4]}
    assert_error_state(_client_for(tmp_path, None, regions))


def test_imagery_path_traversal_rejected(tmp_path: Path) -> None:
    assert_error_state(
        _client_for(tmp_path, mutate(lambda d: d["imagery"]["before"].update(path="../outside.png")))
    )


def test_imagery_absolute_path_rejected(tmp_path: Path) -> None:
    assert_error_state(
        _client_for(tmp_path, mutate(lambda d: d["imagery"]["before"].update(path="/etc/hostname")))
    )


def test_imagery_nested_path_rejected(tmp_path: Path) -> None:
    assert_error_state(
        _client_for(tmp_path, mutate(lambda d: d["imagery"]["before"].update(path="sub/before.png")))
    )


def test_imagery_non_png_extension_rejected(tmp_path: Path) -> None:
    bundle = write_bundle(tmp_path)
    (bundle / "before.png").unlink()
    (bundle / "before.jpeg").write_bytes(b"not a png")
    assert_error_state(
        _client_for(tmp_path, mutate(lambda d: d["imagery"]["before"].update(path="before.jpeg")))
    )


def test_imagery_path_pointing_at_non_image_rejected(tmp_path: Path) -> None:
    bundle = write_bundle(tmp_path)
    (bundle / "before.png").unlink()
    (bundle / "before.png").write_text("plain text, definitely not a png")
    assert_error_state(TestClient(create_app(bundle_dir=bundle)))


def test_imagery_path_must_match_key(tmp_path: Path) -> None:
    assert_error_state(
        _client_for(tmp_path, mutate(lambda d: d["imagery"]["before"].update(path="after.png")))
    )


def test_method_quantity_must_be_permitted(tmp_path: Path) -> None:
    assert_error_state(
        _client_for(tmp_path, mutate(lambda d: d["method"].update(quantity="amplitude")))
    )


def test_method_units_must_be_db(tmp_path: Path) -> None:
    assert_error_state(_client_for(tmp_path, mutate(lambda d: d["method"].update(units="linear"))))


def test_change_definition_must_match_contract(tmp_path: Path) -> None:
    assert_error_state(
        _client_for(
            tmp_path, mutate(lambda d: d["method"].update(change_definition="after/before"))
        )
    )


def test_registration_shape_required(tmp_path: Path) -> None:
    assert_error_state(_client_for(tmp_path, mutate(lambda d: d["method"]["registration"].pop("status"))))
    document = analysis_document()
    document["method"]["registration"]["residual_pixels"] = None
    bundle = write_bundle(tmp_path, analysis=document)
    assert TestClient(create_app(bundle_dir=bundle)).get("/api/status").json()["state"] == "ready"


def test_preprocessing_must_be_list_of_strings(tmp_path: Path) -> None:
    assert_error_state(
        _client_for(tmp_path, mutate(lambda d: d["method"].update(preprocessing="none")))
    )


def test_negative_changed_area_rejected(tmp_path: Path) -> None:
    assert_error_state(
        _client_for(tmp_path, mutate(lambda d: d["metrics"].update(total_changed_area_ha=-1.0)))
    )


def test_time_series_dates_must_be_ascending_and_unique(tmp_path: Path) -> None:
    regions = regions_document()
    series = regions["features"][0]["properties"]["time_series"]
    series.reverse()
    assert_error_state(_client_for(tmp_path, None, regions))


def test_time_series_dates_must_be_utc(tmp_path: Path) -> None:
    regions = regions_document()
    regions["features"][0]["properties"]["time_series"][0]["acquired_at"] = "2026-01-15T12:00:00+02:00"
    assert_error_state(_client_for(tmp_path, None, regions))


def test_empty_feature_collection_allowed(tmp_path: Path) -> None:
    document = analysis_document()
    document["metrics"]["region_count"] = 0
    document["demo_region_id"] = None
    document["metrics"]["total_changed_area_ha"] = 0.0
    bundle = write_bundle(tmp_path, analysis=document, regions={"type": "FeatureCollection", "features": []})
    client = TestClient(create_app(bundle_dir=bundle))
    assert client.get("/api/status").json()["state"] == "ready"
    assert client.get("/api/regions").json() == {"type": "FeatureCollection", "features": []}
    assert client.get("/api/regions/anything").status_code == 404


def test_missing_required_analysis_field_rejected(tmp_path: Path) -> None:
    for field in ("analysis_id", "title", "bbox", "scenes", "method", "metrics", "imagery"):
        assert_error_state(_client_for(tmp_path, mutate(lambda d, f=field: d.pop(f)))), field


def test_regions_must_be_feature_collection(tmp_path: Path) -> None:
    assert_error_state(_client_for(tmp_path, None, {"type": "Feature", "features": []}))


def test_error_message_is_stable_and_actionable(tmp_path: Path) -> None:
    client = _client_for(tmp_path, mutate(lambda d: d.update(schema_version=99)))
    body = client.get("/api/status").json()
    assert body["state"] == "error"
    assert "schema_version" in body["message"]


def test_recovery_after_bundle_becomes_valid(tmp_path: Path) -> None:
    bundle = write_bundle(tmp_path, analysis=mutate(lambda d: d.update(schema_version=99)))
    client = TestClient(create_app(bundle_dir=bundle))
    assert client.get("/api/status").json()["state"] == "error"
    (bundle / "analysis.json").write_text(json.dumps(analysis_document()))
    assert client.get("/api/status").json()["state"] == "ready"
    assert client.get("/api/analysis").status_code == 200


# ---------------------------------------------------------------------------
# analysis_area_ha
# ---------------------------------------------------------------------------


def test_analysis_area_ha_is_required(tmp_path: Path) -> None:
    assert_error_state(
        _client_for(tmp_path, mutate(lambda d: d["metrics"].pop("analysis_area_ha")))
    )


def test_analysis_area_ha_must_equal_valid_plus_not_evaluable(tmp_path: Path) -> None:
    assert_error_state(
        _client_for(tmp_path, mutate(lambda d: d["metrics"].update(analysis_area_ha=999.0)))
    )
    assert_error_state(
        _client_for(tmp_path, mutate(lambda d: d["metrics"].update(analysis_area_ha=-1.0)))
    )


def test_analysis_area_ha_allows_documented_tolerance(tmp_path: Path) -> None:
    # 0.009 ha is inside the 0.01 ha tolerance and must be accepted.
    document = mutate(lambda d: d["metrics"].update(analysis_area_ha=102.009))
    bundle = write_bundle(tmp_path, analysis=document)
    assert TestClient(create_app(bundle_dir=bundle)).get("/api/status").json()["state"] == "ready"


def test_analysis_area_ha_beyond_tolerance_rejected(tmp_path: Path) -> None:
    assert_error_state(
        _client_for(tmp_path, mutate(lambda d: d["metrics"].update(analysis_area_ha=102.02)))
    )


def test_total_changed_area_must_equal_sum_of_regions(tmp_path: Path) -> None:
    # The two fixture regions total 1.25 + 3.25 = 4.5 ha.
    assert_error_state(
        _client_for(tmp_path, mutate(lambda d: d["metrics"].update(total_changed_area_ha=5.0)))
    )


def test_total_changed_area_tolerates_rounding_in_region_sum(tmp_path: Path) -> None:
    document = mutate(lambda d: d["metrics"].update(total_changed_area_ha=4.505))
    bundle = write_bundle(tmp_path, analysis=document)
    assert TestClient(create_app(bundle_dir=bundle)).get("/api/status").json()["state"] == "ready"


def test_total_changed_area_must_not_exceed_valid_area(tmp_path: Path) -> None:
    def shrink(d: dict[str, Any]) -> None:
        d["metrics"].update(valid_area_ha=4.0, not_evaluable_area_ha=98.0, analysis_area_ha=102.0)

    assert_error_state(_client_for(tmp_path, mutate(shrink)))


def test_total_changed_area_at_valid_area_boundary_is_accepted(tmp_path: Path) -> None:
    def shrink(d: dict[str, Any]) -> None:
        d["metrics"].update(valid_area_ha=4.5, not_evaluable_area_ha=97.5, analysis_area_ha=102.0)

    bundle = write_bundle(tmp_path, analysis=mutate(shrink))
    assert TestClient(create_app(bundle_dir=bundle)).get("/api/status").json()["state"] == "ready"


# ---------------------------------------------------------------------------
# imagery bounds must equal analysis.bbox
# ---------------------------------------------------------------------------


def test_imagery_bounds_must_equal_analysis_bbox(tmp_path: Path) -> None:
    def shrink(d: dict[str, Any]) -> None:
        d["imagery"]["change"].update(bounds=[-60.4, -3.4, -60.3, -3.3])

    assert_error_state(_client_for(tmp_path, mutate(shrink)))


def test_all_three_imagery_bounds_must_equal_analysis_bbox(tmp_path: Path) -> None:
    for key in ("before", "after", "change"):
        def shift(d: dict[str, Any], key: str = key) -> None:
            west, south, east, north = d["bbox"]
            d["imagery"][key].update(bounds=[west + 0.01, south, east, north])

        assert_error_state(_client_for(tmp_path, mutate(shift))), key


# ---------------------------------------------------------------------------
# baseline_at / observation_interval semantics
# ---------------------------------------------------------------------------


def test_baseline_at_is_required(tmp_path: Path) -> None:
    regions = regions_document()
    del regions["features"][0]["properties"]["baseline_at"]
    assert_error_state(_client_for(tmp_path, None, regions))


def test_legacy_last_observed_unchanged_at_is_rejected(tmp_path: Path) -> None:
    regions = regions_document()
    props = regions["features"][0]["properties"]
    props["last_observed_unchanged_at"] = props.pop("baseline_at")
    assert_error_state(_client_for(tmp_path, None, regions))


def test_observation_interval_is_required(tmp_path: Path) -> None:
    regions = regions_document()
    del regions["features"][0]["properties"]["observation_interval"]
    assert_error_state(_client_for(tmp_path, None, regions))


def test_legacy_onset_interval_is_rejected(tmp_path: Path) -> None:
    regions = regions_document()
    props = regions["features"][0]["properties"]
    props["onset_interval"] = props.pop("observation_interval")
    assert_error_state(_client_for(tmp_path, None, regions))


def test_baseline_at_must_precede_detected_at(tmp_path: Path) -> None:
    regions = regions_document()
    regions["features"][0]["properties"]["baseline_at"] = "2026-03-20T10:00:00Z"
    assert_error_state(_client_for(tmp_path, None, regions))


def test_baseline_at_must_be_a_scene_acquisition(tmp_path: Path) -> None:
    regions = regions_document()
    regions["features"][0]["properties"]["baseline_at"] = "2026-02-01T10:00:00Z"
    assert_error_state(_client_for(tmp_path, None, regions))


def test_detected_at_must_be_a_scene_acquisition(tmp_path: Path) -> None:
    regions = regions_document()
    regions["features"][0]["properties"]["detected_at"] = "2026-03-21T10:00:00Z"
    assert_error_state(_client_for(tmp_path, None, regions))


def test_observation_interval_bounds_must_be_scene_acquisitions(tmp_path: Path) -> None:
    for key in ("start", "end"):
        regions = regions_document()
        regions["features"][0]["properties"]["observation_interval"][key] = "2026-06-01T10:00:00Z"
        assert_error_state(_client_for(tmp_path, None, regions)), key


def test_observation_interval_must_contain_the_observations(tmp_path: Path) -> None:
    regions = regions_document()
    interval = regions["features"][0]["properties"]["observation_interval"]
    interval["end"] = "2026-01-15T10:00:00Z"
    assert_error_state(_client_for(tmp_path, None, regions))


def test_acquisition_times_must_be_utc(tmp_path: Path) -> None:
    regions = regions_document()
    regions["features"][0]["properties"]["baseline_at"] = "2026-01-15T12:00:00+02:00"
    assert_error_state(_client_for(tmp_path, None, regions))


def test_equal_offset_spelling_of_a_scene_time_is_accepted(tmp_path: Path) -> None:
    regions = regions_document()
    regions["features"][0]["properties"]["baseline_at"] = "2026-01-15T10:00:00+00:00"
    bundle = write_bundle(tmp_path, regions=regions)
    assert TestClient(create_app(bundle_dir=bundle)).get("/api/status").json()["state"] == "ready"


# ---------------------------------------------------------------------------
# change_db vs magnitude_db are different statistics
# ---------------------------------------------------------------------------


def test_magnitude_and_signed_median_are_served_independently(tmp_path: Path) -> None:
    regions = regions_document()
    props = regions["features"][0]["properties"]
    # median(|dB|) is not |median(dB)|; both are valid and are not reconciled.
    props["change_db"] = -1.0
    props["magnitude_db"] = 3.0
    bundle = write_bundle(tmp_path, regions=regions)
    client = TestClient(create_app(bundle_dir=bundle))
    assert client.get("/api/status").json()["state"] == "ready"
    served = client.get("/api/regions/synthetic-region-1").json()["properties"]
    assert served["change_db"] == -1.0
    assert served["magnitude_db"] == 3.0


def test_magnitude_db_must_not_be_negative(tmp_path: Path) -> None:
    regions = regions_document()
    regions["features"][0]["properties"]["magnitude_db"] = -2.0
    assert_error_state(_client_for(tmp_path, None, regions))


def test_positive_and_negative_changes_both_valid(tmp_path: Path) -> None:
    regions = regions_document()
    regions["features"][0]["properties"]["change_db"] = 2.5
    regions["features"][0]["properties"]["magnitude_db"] = 2.5
    bundle = write_bundle(tmp_path, regions=regions)
    assert TestClient(create_app(bundle_dir=bundle)).get("/api/status").json()["state"] == "ready"
