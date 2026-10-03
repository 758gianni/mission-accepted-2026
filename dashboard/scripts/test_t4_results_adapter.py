#!/usr/bin/env python3
"""Unit tests for :mod:`dashboard.scripts.t4_results_adapter`.

Run from the repo root::

    python3 -m unittest dashboard.scripts.test_t4_results_adapter -v

The fixtures are small synthetic stand-ins for the 4-date pipeline's derived
output: a ``regions`` summary plus a candidate region GeoJSON.  No real SAR or
real derived dataset is required or referenced.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = REPO_ROOT / "dashboard" / "scripts"
SCRIPT_PATH = SCRIPT_DIR / "t4_results_adapter.py"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import t4_results_adapter as adapter  # noqa: E402


def _write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _summary(regions: dict[str, dict[str, Any]], **acquisition: Any) -> dict[str, Any]:
    return {
        "acquisition": {
            "id": "T4",
            "date": "2026-02-11",
            "iso": "2026-02-11T14:23:07Z",
            "beam": "XF0W2",
            "polarization": "HH",
            "orbit": "Ascending",
            "role": "validation",
            **acquisition,
        },
        "regions": regions,
    }


class T4ResultsAdapterTest(unittest.TestCase):
    """Behaviour of the adapter against synthetic pipeline output."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.pipeline = self.tmp / "exploration_4date"
        self.pipeline.mkdir()

    def _write_pipeline(self, regions: dict[str, dict[str, Any]], **acquisition: Any) -> None:
        _write_json(self.pipeline / "t4_summary.json", _summary(regions, **acquisition))

    # -- required case 1: confirmed persistent region ------------------------
    def test_confirmed_persistent_region_yields_confirmed_t4_status(self) -> None:
        """A persistent (class_id 1) region validated by T4 emits t4_status=confirmed.

        build_contract.py uses exactly this to reclassify the feature to
        temporal_class "t4_validated_persistent", so the record must carry the
        confirmed status together with the T4 measurements.
        """
        self._write_pipeline(
            {
                "r-001": {
                    # class_id 1 == persistent candidate
                    "class_id": 1,
                    "change_survives_t4": True,
                    "mean_power_t4": 0.0412,
                    "mean_delta_db_t4": -2.75,
                    "note": "change survives at T4",
                }
            }
        )

        results = adapter.build_t4_results(self.pipeline)
        record = results["regions"]["r-001"]

        self.assertEqual(record["t4_status"], adapter.STATUS_CONFIRMED)
        self.assertEqual(record["t4_mean_power"], 0.0412)
        self.assertEqual(record["t4_mean_signed_db"], -2.75)
        self.assertEqual(record["t4_observation"], "change survives at T4")
        self.assertEqual(
            set(record), set(adapter.REGION_FIELDS), "record must match the contract shape"
        )
        self.assertEqual(adapter.validate_results(results), [])

    def test_weakened_region_maps_to_weakened(self) -> None:
        self._write_pipeline(
            {"r-002": {"class_id": 2, "t4_status": "weakened", "t4_mean_signed_db": -0.4}}
        )
        results = adapter.build_t4_results(self.pipeline)
        self.assertEqual(
            results["regions"]["r-002"]["t4_status"], adapter.STATUS_WEAKENED
        )

    # -- required case 2: outside-swath regions keep the fallback ------------
    def test_outside_swath_region_keeps_fallback_status(self) -> None:
        """Regions the T4 swath does not cover get the --fallback-status value."""
        self._write_pipeline(
            {
                "r-001": {"class_id": 1, "t4_status": "confirmed", "t4_mean_power": 0.05},
                "r-002": {"class_id": 1, "within_swath": False},
            }
        )

        results = adapter.build_t4_results(self.pipeline)
        regions = results["regions"]

        self.assertEqual(
            regions["r-002"]["t4_status"],
            "outside_swath",
            "uncovered region must fall back to outside_swath by default",
        )
        self.assertIsNone(regions["r-002"]["t4_mean_power"])
        self.assertEqual(regions["r-001"]["t4_status"], adapter.STATUS_CONFIRMED)

    def test_fallback_status_is_configurable(self) -> None:
        self._write_pipeline({"r-002": {"region_id": "r-002", "covered": False}})
        results = adapter.build_t4_results(self.pipeline, fallback_status="pending")
        self.assertEqual(results["regions"]["r-002"]["t4_status"], "pending")

    def test_roster_region_absent_from_pipeline_output_gets_fallback(self) -> None:
        self._write_pipeline({"r-001": {"class_id": 1, "t4_status": "confirmed"}})
        roster = _write_json(
            self.tmp / "candidate_regions.geojson",
            {
                "type": "FeatureCollection",
                "features": [
                    {"type": "Feature", "id": "r-001", "properties": {"class_id": 1}},
                    {"type": "Feature", "id": "r-002", "properties": {"class_id": 3}},
                ],
            },
        )

        results = adapter.build_t4_results(self.pipeline, roster=roster)
        regions = results["regions"]

        self.assertEqual(sorted(regions), ["r-001", "r-002"])
        self.assertEqual(regions["r-001"]["t4_status"], adapter.STATUS_CONFIRMED)
        self.assertEqual(regions["r-002"]["t4_status"], "outside_swath")

    def test_geojson_and_summary_are_merged_by_region_id(self) -> None:
        """Region identity from GeoJSON and metrics from the summary must join up."""
        _write_json(
            self.pipeline / "candidate_regions_terrain_qa.geojson",
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "id": "region-7",
                        "geometry": {"type": "Polygon", "coordinates": []},
                        "properties": {"class_id": 1},
                    }
                ],
            },
        )
        self._write_pipeline(
            {"region-7": {"t4_status": "confirmed", "t4_mean_power": 0.01, "t4_observation": "ok"}}
        )

        results = adapter.build_t4_results(self.pipeline)
        self.assertEqual(results["regions"]["region-7"]["t4_status"], adapter.STATUS_CONFIRMED)
        self.assertEqual(results["regions"]["region-7"]["t4_mean_power"], 0.01)

    def test_acquisition_metadata_is_carried_through(self) -> None:
        self._write_pipeline({"r-001": {"t4_status": "confirmed"}})
        results = adapter.build_t4_results(self.pipeline)
        acquisition = results["acquisition"]
        self.assertEqual(acquisition["id"], "T4")
        self.assertEqual(acquisition["date"], "2026-02-11")
        self.assertEqual(acquisition["beam"], "XF0W2")
        self.assertEqual(acquisition["polarization"], "HH")
        self.assertEqual(acquisition["orbit"], "Ascending")
        self.assertEqual(
            list(acquisition),
            ["id", "date", "iso", "beam", "polarization", "orbit", "role"],
            "acquisition keys must be in contract order",
        )

    def test_pipeline_metadata_that_differs_from_defaults_wins(self) -> None:
        """Regression: a non-default beam/pol/orbit/role must not be masked by the defaults."""
        self._write_pipeline(
            {"r-001": {"t4_status": "confirmed"}},
            iso="2026-02-11T14:23:07Z",
            beam="XF1W1",
            polarization="HV",
            orbit="Descending",
            role="validation",
        )
        acquisition = adapter.build_t4_results(self.pipeline)["acquisition"]
        self.assertEqual(acquisition["beam"], "XF1W1")
        self.assertEqual(acquisition["polarization"], "HV")
        self.assertEqual(acquisition["orbit"], "Descending")
        self.assertEqual(acquisition["role"], "validation")
        self.assertEqual(acquisition["iso"], "2026-02-11T14:23:07Z")

    def test_acquisition_defaults_apply_when_pipeline_is_silent(self) -> None:
        _write_json(self.pipeline / "t4_summary.json", {"regions": {"r-001": {"t4_status": "confirmed"}}})
        acquisition = adapter.build_t4_results(self.pipeline)["acquisition"]
        self.assertEqual(acquisition["id"], "T4")
        self.assertEqual(acquisition["beam"], "XF0W2")
        self.assertEqual(acquisition["polarization"], "HH")
        self.assertEqual(acquisition["orbit"], "Ascending")
        self.assertTrue(acquisition["role"])
        self.assertEqual(acquisition["date"], "")

    def test_acquisition_overrides_beat_pipeline_metadata(self) -> None:
        self._write_pipeline({"r-001": {"t4_status": "confirmed"}}, beam="XF1W1")
        acquisition = adapter.build_t4_results(
            self.pipeline, acquisition_overrides={"beam": "W2"}
        )["acquisition"]
        self.assertEqual(acquisition["beam"], "W2")

    def test_unknown_status_token_is_not_guessed(self) -> None:
        self._write_pipeline({"r-001": {"t4_status": "spiculated"}})
        results = adapter.build_t4_results(self.pipeline)
        self.assertEqual(results["regions"]["r-001"]["t4_status"], "outside_swath")
        self.assertEqual(adapter.normalize_status("spiculated"), None)

    def test_status_normalisation_vocabulary(self) -> None:
        cases: list[tuple[Any, str | None]] = [
            ("confirmed", adapter.STATUS_CONFIRMED),
            ("SURVIVED", adapter.STATUS_CONFIRMED),
            ("off_swath", adapter.STATUS_OUTSIDE_SWATH),
            ("attenuated", adapter.STATUS_WEAKENED),
            ("pending", adapter.STATUS_PENDING),
            ("", adapter.STATUS_PENDING),
            ("   ", adapter.STATUS_PENDING),
            (True, adapter.STATUS_CONFIRMED),
            (False, adapter.STATUS_WEAKENED),
            (None, None),
            ([], None),
            ({"status": "confirmed"}, None),
        ]
        for token, expected in cases:
            with self.subTest(token=token):
                self.assertEqual(adapter.normalize_status(token), expected)

    def test_non_numeric_metrics_become_null(self) -> None:
        self._write_pipeline(
            {"r-001": {"t4_status": "confirmed", "t4_mean_power": "n/a", "t4_mean_signed_db": True}}
        )
        results = adapter.build_t4_results(self.pipeline)
        record = results["regions"]["r-001"]
        self.assertIsNone(record["t4_mean_power"])
        self.assertIsNone(record["t4_mean_signed_db"])

    # -- required case 3: missing pipeline output ----------------------------
    def test_missing_pipeline_output_raises_clear_error(self) -> None:
        missing = self.tmp / "does_not_exist"
        with self.assertRaises(adapter.PipelineOutputError) as ctx:
            adapter.build_t4_results(missing)

        message = str(ctx.exception)
        self.assertIn(str(missing), message)
        self.assertIn("not found", message)
        self.assertIn("--pipeline-output", message)

    def test_pipeline_directory_without_regions_raises_clear_error(self) -> None:
        _write_json(self.pipeline / "unrelated.json", {"something": "else"})
        with self.assertRaises(adapter.PipelineOutputError) as ctx:
            adapter.build_t4_results(self.pipeline)
        self.assertIn("no region records found", str(ctx.exception))

    def test_missing_roster_raises_clear_error(self) -> None:
        self._write_pipeline({"r-001": {"t4_status": "confirmed"}})
        with self.assertRaises(adapter.PipelineOutputError):
            adapter.build_t4_results(self.pipeline, roster=self.tmp / "absent.geojson")

    def test_malformed_json_is_skipped_and_surfaces_as_clear_error(self) -> None:
        (self.pipeline / "broken.json").write_text("{not json", encoding="utf-8")
        with self.assertRaises(adapter.PipelineOutputError):
            adapter.build_t4_results(self.pipeline)

    def test_malformed_json_does_not_break_a_valid_run(self) -> None:
        (self.pipeline / "broken.json").write_text("{not json", encoding="utf-8")
        self._write_pipeline({"r-001": {"t4_status": "confirmed"}})
        warnings: list[str] = []
        results = adapter.build_t4_results(self.pipeline, warnings=warnings)
        self.assertEqual(results["regions"]["r-001"]["t4_status"], adapter.STATUS_CONFIRMED)
        self.assertEqual(len(warnings), 1)
        self.assertIn("broken.json", warnings[0])
        self.assertNotIn(
            "warnings",
            results,
            "warnings must stay out of the contract document",
        )

    def test_warnings_are_reported_on_stderr_by_the_cli(self) -> None:
        (self.pipeline / "broken.json").write_text("{not json", encoding="utf-8")
        self._write_pipeline({"r-001": {"t4_status": "confirmed"}})
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--pipeline-output",
                str(self.pipeline),
                "--out",
                str(self.tmp / "t4_results.json"),
            ],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("warning:", completed.stderr)
        self.assertIn("broken.json", completed.stderr)

    def test_invalid_fallback_status_is_rejected(self) -> None:
        self._write_pipeline({"r-001": {"t4_status": "confirmed"}})
        with self.assertRaises(adapter.AdapterError):
            adapter.build_t4_results(self.pipeline, fallback_status="banana")

    # -- required case 4: dry run writes nothing -----------------------------
    def test_dry_run_writes_nothing(self) -> None:
        self._write_pipeline(
            {
                "r-001": {"class_id": 1, "t4_status": "confirmed", "t4_mean_power": 0.05},
                "r-002": {"class_id": 2, "within_swath": False},
            }
        )
        out = self.tmp / "build" / "t4_results.json"

        exit_code = adapter.main(
            ["--pipeline-output", str(self.pipeline), "--out", str(out), "--dry-run"]
        )

        self.assertEqual(exit_code, 0)
        self.assertFalse(out.exists(), "--dry-run must not create the output file")
        self.assertFalse(out.parent.exists(), "--dry-run must not create output dirs")
        self.assertEqual(list(self.pipeline.iterdir()), sorted(self.pipeline.iterdir()))

    def test_non_dry_run_writes_the_contract_document(self) -> None:
        self._write_pipeline({"r-001": {"class_id": 1, "t4_status": "confirmed"}})
        out = self.tmp / "build" / "t4_results.json"

        exit_code = adapter.main(
            ["--pipeline-output", str(self.pipeline), "--out", str(out)]
        )

        self.assertEqual(exit_code, 0)
        written = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(
            sorted(written), ["acquisition", "regions"], "top level must match the contract"
        )
        self.assertEqual(written["regions"]["r-001"]["t4_status"], "confirmed")

    def test_main_reports_missing_pipeline_output_with_exit_code_2(self) -> None:
        exit_code = adapter.main(
            [
                "--pipeline-output",
                str(self.tmp / "nope"),
                "--out",
                str(self.tmp / "t4_results.json"),
            ]
        )
        self.assertEqual(exit_code, 2)
        self.assertFalse((self.tmp / "t4_results.json").exists())

    def test_out_is_required_without_dry_run(self) -> None:
        self._write_pipeline({"r-001": {"t4_status": "confirmed"}})
        with self.assertRaises(SystemExit) as ctx:
            adapter.main(["--pipeline-output", str(self.pipeline)])
        self.assertEqual(ctx.exception.code, 2)

    def test_summary_reports_status_counts(self) -> None:
        self._write_pipeline(
            {
                "r-001": {"t4_status": "confirmed"},
                "r-002": {"t4_status": "weakened"},
                "r-003": {"within_swath": False},
            }
        )
        results = adapter.build_t4_results(self.pipeline)
        summary = adapter.summarize_results(results, dry_run=True)
        self.assertIn("3 region(s)", summary)
        self.assertIn("confirmed=1", summary)
        self.assertIn("weakened=1", summary)
        self.assertIn("outside_swath=1", summary)
        self.assertIn("nothing written", summary)

    # -- end-to-end via the module CLI ---------------------------------------
    def test_cli_module_entrypoint_end_to_end(self) -> None:
        self._write_pipeline({"r-001": {"class_id": 1, "t4_status": "confirmed"}})
        out = self.tmp / "t4_results.json"

        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--pipeline-output",
                str(self.pipeline),
                "--out",
                str(out),
            ],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        written = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(written["regions"]["r-001"]["t4_status"], "confirmed")
        self.assertEqual(adapter.validate_results(written), [])


if __name__ == "__main__":
    unittest.main()