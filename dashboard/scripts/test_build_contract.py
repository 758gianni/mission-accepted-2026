import json
import tempfile
import unittest
from pathlib import Path

from dashboard.scripts.build_contract import (
    acquisitions_from_metadata, annotate_tile_support, build_tile_previews, load_t4_results,
    summarize_registration_qa,
)


class T4ContractInputTests(unittest.TestCase):
    def test_normalizes_evaluation_catalogue_without_claiming_uncovered_regions(self):
        evaluation = {
            "total_tested": 3,
            "covered_in_t4": 3,
            "survived_confirmed": 1,
            "weakened_transient": 1,
            "outside_t4_swath": 1,
            "candidates": [
                {"id": 101, "t4_verdict": "SURVIVED_AND_CONFIRMED", "t4_powers": {"T4_N": 0.04}, "t4_deltas_db": {"delta_1_T4_N": -6.1}},
                {"id": 102, "t4_verdict": "WEAKENED_TO_TRANSIENT", "t4_powers": {"T4_S": 0.1}, "t4_deltas_db": {"delta_1_T4_S": -1.2}},
                {"id": 103, "t4_verdict": "OUTSIDE_T4_SWATH"},
                {"id": 104, "t4_verdict": "PERSISTENT_MODERATE", "t4_powers": {"T4_N": 0.06}},
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "t4.json"
            source.write_text(json.dumps(evaluation))

            result = load_t4_results(source)

        self.assertEqual(result["regions"]["101"]["t4_status"], "confirmed")
        self.assertEqual(result["regions"]["101"]["t4_mean_power"], 0.04)
        self.assertEqual(result["regions"]["102"]["t4_status"], "weakened")
        self.assertEqual(result["regions"]["103"]["t4_status"], "outside_coverage")
        self.assertEqual(result["regions"]["104"]["t4_status"], "moderate_support")
        self.assertEqual(result["summary"]["covered_in_t4"], 3)

    def test_observation_timeline_keeps_any_number_of_dates_and_groups_same_day_scenes(self):
        metadata = [
            {"label": f"A{index}", "acquisition": f"2025-01-0{index}T10:00:00Z"}
            for index in range(1, 6)
        ]
        metadata[-1]["acquisition"] = "2025-01-04T10:00:20Z"
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "scenes.json"
            source.write_text(json.dumps(metadata))

            observations = acquisitions_from_metadata(source)

        self.assertEqual(len(observations), 4)
        self.assertEqual(observations[-1]["date"], "2025-01-04")
        self.assertEqual(observations[-1]["source_count"], 2)

    def test_registration_pair_evidence_is_separate_from_candidate_screening(self):
        with tempfile.TemporaryDirectory() as directory:
            registration = Path(directory) / "registration.json"
            screening = Path(directory) / "screening.json"
            registration.write_text(json.dumps({"tile": {"dates": {
                "pass": {"status": "passes_local_residual_checks"},
                "unknown": {"status": "insufficient_registration_evidence"},
            }}}))
            screening.write_text(json.dumps({"candidates_screened": 100, "local_registration_pass": 33,
                "local_registration_status_counts": {"insufficient_registration_evidence": 67},
                "passes_initial_screen": 7}))

            result = summarize_registration_qa(registration, screening)

        self.assertEqual(result["pair_checks"], {"comparisons": 2, "passing": 1, "insufficient": 1, "review_required": 0})
        self.assertEqual(result["candidate_subset"]["screened"], 100)
        self.assertEqual(result["candidate_subset"]["registration_passing"], 33)


class GeospatialTilePreviewTests(unittest.TestCase):
    def test_candidate_tile_support_distinguishes_temporal_observation_and_outside(self):
        import numpy as np
        import rasterio
        from affine import Affine
        from pyproj import Transformer

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "tile.tif"
            with rasterio.open(source, "w", driver="GTiff", width=2, height=2, count=3,
                               dtype="float32", crs="EPSG:32646", transform=Affine(60, 0, 500000, 0, -60, 2500000)) as dataset:
                dataset.write(np.array([[0, 255], [2, 1]], dtype=np.float32), 1)
                dataset.write(np.zeros((2, 2), dtype=np.float32), 2)
                dataset.write(np.array([[3, 1], [0, 2]], dtype=np.float32), 3)
            to_geo = Transformer.from_crs("EPSG:32646", "EPSG:4326", always_xy=True)
            def point_at(col, row):
                return to_geo.transform(500000 + (col + 0.5) * 60, 2500000 - (row + 0.5) * 60)
            points = [point_at(0, 0), point_at(1, 0), point_at(0, 1), (90, 0)]
            features = [{"geometry": {"type": "Point", "coordinates": list(point)}, "properties": {"id": index}}
                        for index, point in enumerate(points)]
            manifest = {"tiles": [{"tile_id": "one", "temporal_asset": "tile.tif"}]}

            totals = annotate_tile_support(features, manifest, root)

        self.assertEqual(totals, {"candidates": 4, "in_tile_grid": 3, "observed": 2, "temporally_classifiable": 1})
        self.assertEqual(features[0]["properties"]["tile_support"]["status"], "temporal_class")
        self.assertEqual(features[1]["properties"]["tile_support"]["status"], "observation_only")
        self.assertEqual(features[2]["properties"]["tile_support"]["status"], "tile_no_observation")
        self.assertEqual(features[3]["properties"]["tile_support"]["status"], "outside_tile_grid")

    def test_builds_transparent_previews_and_correct_geographic_placement_for_every_tile(self):
        import numpy as np
        import rasterio
        from affine import Affine

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "tiles"
            output = Path(directory) / "public" / "tiles"
            root.mkdir()
            entries = []
            for index, (x_origin, date) in enumerate(((500000, "2024-01-01"), (500120, "2024-02-01"))):
                relative = f"temporal/tile-{index}.tif"
                source = root / relative
                source.parent.mkdir(exist_ok=True)
                class_band = np.array([[0, 1], [2, 255]], dtype=np.float32)
                change_band = np.array([[0.0, -3.0], [2.0, np.nan]], dtype=np.float32)
                count_band = np.array([[3, 2], [1, 0]], dtype=np.float32)
                with rasterio.open(source, "w", driver="GTiff", width=2, height=2, count=3,
                                   dtype="float32", crs="EPSG:32646", nodata=np.nan,
                                   transform=Affine(60, 0, x_origin, 0, -60, 2500000)) as dataset:
                    dataset.write(class_band, 1)
                    dataset.write(change_band, 2)
                    dataset.write(count_band, 3)
                entries.append({"tile_id": f"tile-{index}", "temporal_asset": relative,
                                "dates": [date], "crs": "EPSG:32646"})

            tiles, processing = build_tile_previews({"tiles": entries}, root, output)

            self.assertEqual(len(tiles), 2)
            self.assertEqual({tile["tile_id"] for tile in tiles}, {"tile-0", "tile-1"})
            self.assertNotEqual(tiles[0]["corners"], tiles[1]["corners"])
            self.assertEqual(len(tiles[0]["corners"]), 4)
            self.assertTrue((output.parent / tiles[0]["layers"]["temporal_rgb"]).is_file())
            self.assertTrue((output.parent / tiles[0]["layers"]["coverage"]).is_file())
            self.assertAlmostEqual(processing["analysis_valid_area_km2"], 0.0216)
            self.assertAlmostEqual(processing["satellite_coverage_area_km2"], 0.0216)
            self.assertEqual(processing["tile_count"], 2)
            self.assertEqual(processing["acquisition_count"], 2)


if __name__ == "__main__":
    unittest.main()
