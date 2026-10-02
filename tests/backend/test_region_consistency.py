"""Cross-field consistency rules for regions, scenes and method parameters.

These tests encode the review findings: persistence that agrees with the real
acquisition timeline, priority scores that agree with their own formula,
per-region threshold and minimum-area gates, region dates that map onto actual
acquisitions, and catalog links that are ordinary web URLs.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Callable

from fastapi.testclient import TestClient

from backend.app import create_app
from conftest import (
    analysis_document,
    multi_scene_regions,
    regions_document,
    write_bundle,
    write_multi_scene_bundle,
)


def assert_error(client: TestClient) -> None:
    body = client.get("/api/status").json()
    assert body["state"] == "error", body["message"]
    assert client.get("/api/regions").status_code == 503


def assert_ready(client: TestClient) -> None:
    assert client.get("/api/status").json()["state"] == "ready"


def regions_with(change: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    document = regions_document()
    change(document["features"][0]["properties"])
    return document


def analysis_with(change: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    document = analysis_document()
    change(document)
    return document


def bundle_client(tmp_path: Path, analysis=None, regions=None) -> TestClient:
    bundle = write_bundle(tmp_path, analysis=analysis, regions=regions)
    return TestClient(create_app(bundle_dir=bundle))


def multi_scene_client(tmp_path: Path, regions=None) -> TestClient:
    bundle = write_multi_scene_bundle(tmp_path)
    if regions is not None:
        (bundle / "regions.geojson").write_text(json.dumps(regions, indent=2))
    return TestClient(create_app(bundle_dir=bundle))


# ---------------------------------------------------------------------------
# 1. persistence must agree with the acquisition timeline
# ---------------------------------------------------------------------------


def test_two_date_bundle_is_ready_with_not_evaluable(client: TestClient) -> None:
    assert_ready(client)
    for feature in client.get("/api/regions").json()["features"]:
        assert feature["properties"]["persistence"] == {
            "status": "not_evaluable",
            "observations_after_detection": 0,
            "changed_observations": 0,
            "rate": None,
        }


def test_two_date_bundle_rejects_observed_persistence(tmp_path: Path) -> None:
    document = regions_with(
        lambda p: p["persistence"].update(
            status="observed",
            observations_after_detection=1,
            changed_observations=1,
            rate=1.0,
        )
    )
    assert_error(bundle_client(tmp_path, regions=document))


def test_three_scene_observed_persistence_is_accepted(tmp_path: Path) -> None:
    assert_ready(multi_scene_client(tmp_path))
    properties = TestClient(
        create_app(bundle_dir=write_multi_scene_bundle(tmp_path))
    ).get("/api/regions/synthetic-region-3").json()["properties"]
    assert properties["persistence"] == {
        "status": "observed",
        "observations_after_detection": 1,
        "changed_observations": 1,
        "rate": 1.0,
    }


def test_observed_requires_at_least_one_post_detection_acquisition(tmp_path: Path) -> None:
    document = multi_scene_regions()
    persistence = document["features"][0]["properties"]["persistence"]
    persistence["observations_after_detection"] = 0
    persistence["changed_observations"] = 0
    persistence["rate"] = None
    assert_error(multi_scene_client(tmp_path, regions=document))


def test_observations_cannot_exceed_later_acquisitions(tmp_path: Path) -> None:
    document = multi_scene_regions()
    # Detection is at the second of four acquisitions, so two follow.
    document["features"][0]["properties"]["persistence"].update(
        observations_after_detection=3, changed_observations=3, rate=1.0
    )
    assert_error(multi_scene_client(tmp_path, regions=document))


def test_not_evaluable_requires_zero_counts(tmp_path: Path) -> None:
    document = multi_scene_regions()
    document["features"][0]["properties"]["persistence"].update(
        status="not_evaluable", observations_after_detection=0, rate=None
    )
    assert_error(multi_scene_client(tmp_path, regions=document))


def test_changed_observations_cannot_exceed_observations(tmp_path: Path) -> None:
    document = multi_scene_regions()
    document["features"][0]["properties"]["persistence"].update(
        observations_after_detection=1, changed_observations=2, rate=2.0
    )
    assert_error(multi_scene_client(tmp_path, regions=document))


def test_rate_must_equal_changed_over_observations(tmp_path: Path) -> None:
    document = multi_scene_regions()
    document["features"][0]["properties"]["persistence"].update(rate=0.5)
    assert_error(multi_scene_client(tmp_path, regions=document))


def test_rate_must_be_null_when_not_evaluable_even_with_counts(tmp_path: Path) -> None:
    document = multi_scene_regions()
    document["features"][0]["properties"]["persistence"].update(
        status="not_evaluable",
        observations_after_detection=1,
        changed_observations=1,
        rate=1.0,
    )
    assert_error(multi_scene_client(tmp_path, regions=document))


def test_zero_changed_observations_with_observed_status_is_valid(tmp_path: Path) -> None:
    document = multi_scene_regions()
    document["features"][0]["properties"]["persistence"].update(
        observations_after_detection=1, changed_observations=0, rate=0.0
    )
    assert_ready(multi_scene_client(tmp_path, regions=document))


def rate_case(tmp_path: Path, rate: float, observations: int = 2, changed: int = 1):
    # Detected at the second of four acquisitions, so two acquisitions follow
    # and 1 changed of 2 gives a true rate of 0.5.
    document = multi_scene_regions()
    document["features"][0]["properties"]["persistence"].update(
        observations_after_detection=observations, changed_observations=changed, rate=rate
    )
    return multi_scene_client(tmp_path, regions=document)


def test_exact_rate_is_accepted(tmp_path: Path) -> None:
    assert_ready(rate_case(tmp_path, rate=0.5))


def test_rate_within_documented_tolerance_is_accepted(tmp_path: Path) -> None:
    assert_ready(rate_case(tmp_path, rate=0.4995))


def test_rate_beyond_documented_tolerance_rejected(tmp_path: Path) -> None:
    assert_error(rate_case(tmp_path, rate=0.48))


def test_observations_cannot_exceed_later_acquisitions_by_one(tmp_path: Path) -> None:
    assert_error(rate_case(tmp_path, rate=1.0, observations=3, changed=3))


# ---------------------------------------------------------------------------
# 2. priority_score must equal magnitude_db * sqrt(area_ha)
# ---------------------------------------------------------------------------


def test_priority_score_matches_formula(client: TestClient) -> None:
    for feature in client.get("/api/regions").json()["features"]:
        properties = feature["properties"]
        expected = properties["magnitude_db"] * math.sqrt(properties["area_ha"])
        assert math.isclose(
            properties["priority_score"], expected, rel_tol=1e-3, abs_tol=0.01
        )


def test_priority_score_mismatch_rejected(tmp_path: Path) -> None:
    assert_error(
        bundle_client(
            tmp_path, regions=regions_with(lambda p: p.update(priority_score=99.0))
        )
    )


def test_priority_score_zero_rejected(tmp_path: Path) -> None:
    assert_error(
        bundle_client(
            tmp_path, regions=regions_with(lambda p: p.update(priority_score=0.0))
        )
    )


def test_priority_score_follows_area_changes(tmp_path: Path) -> None:
    # Doubling the area must double the score, since the formula uses
    # sqrt(area_ha); a stale score is a consistency failure.
    def grow(p: dict[str, Any]) -> None:
        p["area_ha"] = 5.0
        p["priority_score"] = p["priority_score"]

    assert_error(bundle_client(tmp_path, regions=regions_with(grow)))


# ---------------------------------------------------------------------------
# 3. threshold and minimum area gates
# ---------------------------------------------------------------------------


def test_threshold_must_be_strictly_positive(tmp_path: Path) -> None:
    for value in (0.0, -1.0, -0.5):
        document = analysis_with(lambda d, v=value: d["method"].update(threshold_db=v))
        assert_error(bundle_client(tmp_path, analysis=document))


def test_region_magnitude_below_threshold_rejected(tmp_path: Path) -> None:
    assert_error(bundle_client(tmp_path, regions=regions_with(lambda p: p.update(magnitude_db=0.5))))


def set_magnitude(properties: dict[str, Any], magnitude: float) -> None:
    properties["magnitude_db"] = magnitude
    properties["priority_score"] = magnitude * math.sqrt(properties["area_ha"])


def test_region_magnitude_equal_to_threshold_is_accepted(tmp_path: Path) -> None:
    assert_ready(
        bundle_client(tmp_path, regions=regions_with(lambda p: set_magnitude(p, 1.0)))
    )


def test_threshold_within_tolerance_of_magnitude_is_accepted(tmp_path: Path) -> None:
    assert_ready(
        bundle_client(
            tmp_path, regions=regions_with(lambda p: set_magnitude(p, 0.999))
        )
    )


def test_magnitude_within_tolerance_below_threshold_rejected(tmp_path: Path) -> None:
    assert_error(
        bundle_client(tmp_path, regions=regions_with(lambda p: set_magnitude(p, 0.98)))
    )


def test_stale_priority_score_after_magnitude_change_rejected(tmp_path: Path) -> None:
    assert_error(
        bundle_client(
            tmp_path,
            regions=regions_with(lambda p: p.update(magnitude_db=3.0)),
        )
    )


def test_cancelling_signed_median_does_not_fail_validation(tmp_path: Path) -> None:
    # A region can have a near-zero signed median while median(|dB|) is large.
    # That is a real, reportable situation, not a contract violation.
    document = regions_with(lambda p: p.update(change_db=0.0))
    assert_ready(bundle_client(tmp_path, regions=document))


def test_region_area_below_minimum_rejected(tmp_path: Path) -> None:
    assert_error(
        bundle_client(
            tmp_path,
            analysis=analysis_with(lambda d: d["method"].update(minimum_area_ha=2.0)),
        )
    )


def test_region_area_equal_to_minimum_is_accepted(tmp_path: Path) -> None:
    assert_ready(
        bundle_client(
            tmp_path,
            analysis=analysis_with(lambda d: d["method"].update(minimum_area_ha=1.25)),
        )
    )


def test_region_area_within_tolerance_below_minimum_is_accepted(tmp_path: Path) -> None:
    assert_ready(
        bundle_client(
            tmp_path,
            analysis=analysis_with(lambda d: d["method"].update(minimum_area_ha=1.26)),
        )
    )


# ---------------------------------------------------------------------------
# 4. region times, time series and scenes map onto real acquisitions
# ---------------------------------------------------------------------------


def test_duplicate_scene_ids_rejected(tmp_path: Path) -> None:
    document = analysis_with(lambda d: d["scenes"][1].update(id=d["scenes"][0]["id"]))
    assert_error(bundle_client(tmp_path, analysis=document))


def test_duplicate_scene_timestamps_rejected(tmp_path: Path) -> None:
    document = analysis_with(
        lambda d: d["scenes"][1].update(acquired_at=d["scenes"][0]["acquired_at"])
    )
    assert_error(bundle_client(tmp_path, analysis=document))


def test_scenes_must_be_sorted_by_acquisition(tmp_path: Path) -> None:
    document = analysis_with(lambda d: d["scenes"].reverse())
    assert_error(bundle_client(tmp_path, analysis=document))


def test_time_series_must_map_to_scene_acquisitions(tmp_path: Path) -> None:
    def retime(p: dict[str, Any]) -> None:
        p["time_series"][0]["acquired_at"] = "2026-01-16T10:00:00Z"

    assert_error(bundle_client(tmp_path, regions=regions_with(retime)))


def test_time_series_must_not_be_empty(tmp_path: Path) -> None:
    assert_error(bundle_client(tmp_path, regions=regions_with(lambda p: p.update(time_series=[]))))


def test_missing_time_series_is_rejected_not_fabricated(tmp_path: Path) -> None:
    def drop(p: dict[str, Any]) -> None:
        del p["time_series"]

    assert_error(bundle_client(tmp_path, regions=regions_with(drop)))


def test_time_series_entries_are_never_invented(client: TestClient) -> None:
    for feature in client.get("/api/regions").json()["features"]:
        stamps = [point["acquired_at"] for point in feature["properties"]["time_series"]]
        assert stamps == sorted(stamps)
        assert len(stamps) == len(set(stamps))


def test_sparse_time_series_is_served_verbatim(tmp_path: Path) -> None:
    # A region observed at only two of three acquisitions stays sparse; the API
    # must serve it as-is rather than padding the gaps.
    document = multi_scene_regions()
    document["features"][0]["properties"]["time_series"] = [
        {
            "acquired_at": "2026-01-15T10:00:00Z",
            "mean_backscatter_db": -8.0,
            "change_from_baseline_db": None,
            "valid_fraction": 1.0,
        },
        {
            "acquired_at": "2026-06-10T10:00:00Z",
            "mean_backscatter_db": -10.75,
            "change_from_baseline_db": -2.75,
            "valid_fraction": 1.0,
        },
    ]
    client = multi_scene_client(tmp_path, regions=document)
    assert_ready(client)
    served = client.get("/api/regions/synthetic-region-3").json()["properties"]["time_series"]
    assert len(served) == 2


def test_interval_must_bracket_baseline_and_detection(tmp_path: Path) -> None:
    def widen(p: dict[str, Any]) -> None:
        p["observation_interval"] = {
            "start": "2026-03-20T10:00:00Z",
            "end": "2026-03-20T10:00:00Z",
        }

    assert_error(bundle_client(tmp_path, regions=regions_with(widen)))


# ---------------------------------------------------------------------------
# 5. catalog_url must be an ordinary web URL
# ---------------------------------------------------------------------------


def test_javascript_catalog_url_rejected(tmp_path: Path) -> None:
    document = analysis_with(
        lambda d: d["scenes"][0].update(catalog_url="javascript:alert(1)")
    )
    assert_error(bundle_client(tmp_path, analysis=document))


def test_data_catalog_url_rejected(tmp_path: Path) -> None:
    document = analysis_with(
        lambda d: d["scenes"][0].update(catalog_url="data:text/html,<script>x</script>")
    )
    assert_error(bundle_client(tmp_path, analysis=document))


def test_file_catalog_url_rejected(tmp_path: Path) -> None:
    document = analysis_with(
        lambda d: d["scenes"][0].update(catalog_url="file:///etc/passwd")
    )
    assert_error(bundle_client(tmp_path, analysis=document))


def test_relative_catalog_url_rejected(tmp_path: Path) -> None:
    document = analysis_with(
        lambda d: d["scenes"][0].update(catalog_url="/catalog/scene")
    )
    assert_error(bundle_client(tmp_path, analysis=document))


def test_http_and_https_catalog_urls_accepted(tmp_path: Path) -> None:
    for url in ("http://example.invalid/catalog", "https://example.invalid/catalog"):
        document = analysis_with(lambda d, u=url: d["scenes"][0].update(catalog_url=u))
        assert_ready(bundle_client(tmp_path, analysis=document))


# ---------------------------------------------------------------------------
# text handling: neutral JSON, no HTML sanitisation theatre
# ---------------------------------------------------------------------------


def test_html_like_text_is_served_as_json_not_html(tmp_path: Path) -> None:
    document = analysis_with(
        lambda d: d.update(title="<script>alert(1)</script> synthetic title")
    )
    client = bundle_client(tmp_path, analysis=document)
    assert_ready(client)
    response = client.get("/api/analysis")
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers.get("x-content-type-options") == "nosniff"
    assert response.json()["title"] == "<script>alert(1)</script> synthetic title"


def test_html_like_text_in_region_properties_is_not_rewritten(tmp_path: Path) -> None:
    document = regions_with(
        lambda p: p.update(explanation="<b>deforestation</b> suspected & reported")
    )
    client = bundle_client(tmp_path, regions=document)
    assert_ready(client)
    served = client.get("/api/regions/synthetic-region-1").json()["properties"]
    assert served["explanation"] == "<b>deforestation</b> suspected & reported"


def test_openapi_declares_no_html_content_types(client: TestClient) -> None:
    document = client.get("/openapi.json").json()
    for path in ("/api/analysis", "/api/regions", "/api/regions/{region_id}"):
        for method, operation in document["paths"][path].items():
            for response in operation["responses"].values():
                assert "text/html" not in response.get("content", {})