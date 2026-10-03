"""Shared test fixtures for the ForestWatch backend API tests.

All fixtures created here are SYNTHETIC and exist only for tests. They are
written into pytest ``tmp_path`` directories and are never committed to
``data/processed`` or any production/demo location.
"""

from __future__ import annotations

import copy
import json
import struct
import sys
import zlib
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

# Repository root on sys.path so the suite runs under any pytest configuration
# (or none). Shared project configuration such as pyproject.toml is owned by
# the team lead and is not modified here.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app import create_app
from backend.pngcheck import PNG_SIGNATURE

def _build_png(width: int, height: int) -> bytes:
    """Build a small, genuinely valid greyscale PNG deterministically."""

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    raw = b"".join(
        b"\x00" + bytes([(x + y) % 256 for x in range(width)]) for y in range(height)
    )
    return (
        PNG_SIGNATURE
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


PNG_1X1_GRAY = _build_png(1, 1)

SCHEMA_VERSION = 1

BASE_ANALYSIS: dict[str, Any] = {
    "schema_version": SCHEMA_VERSION,
    "analysis_id": "synthetic-analysis-0001",
    "title": "Synthetic test analysis (NOT real data)",
    "bbox": [-60.5, -3.5, -60.2, -3.2],
    "scenes": [
        {
            "id": "SYNTHETIC-BEFORE",
            "acquired_at": "2026-01-15T10:00:00Z",
            "polarization": "VV",
            "beam_mode": "IW",
            "orbit_direction": "ASCENDING",
            "relative_orbit": 12345,
            "product_type": "GRD",
            "source_collection": "SYNTHETIC-COLLECTION",
            "catalog_url": "https://example.invalid/catalog/SYNTHETIC-BEFORE",
        },
        {
            "id": "SYNTHETIC-AFTER",
            "acquired_at": "2026-03-20T10:00:00Z",
            "polarization": "VV",
            "beam_mode": "IW",
            "orbit_direction": "ASCENDING",
            "relative_orbit": 12345,
            "product_type": "GRD",
            "source_collection": "SYNTHETIC-COLLECTION",
            "catalog_url": "https://example.invalid/catalog/SYNTHETIC-AFTER",
        },
    ],
    "method": {
        "quantity": "sigma0",
        "units": "dB",
        "change_definition": "10*log10(after/before)",
        "threshold_db": 1.0,
        "minimum_area_ha": 1.0,
        "speckle_filter": "none (synthetic fixture)",
        "registration": {"status": "synthetic", "residual_pixels": 0.0},
        "preprocessing": ["synthetic-thermal-noise", "synthetic-unit-amplitude"],
    },
    "metrics": {
        "region_count": 2,
        "total_changed_area_ha": 4.5,
        "valid_area_ha": 100.0,
        "not_evaluable_area_ha": 2.0,
        "analysis_area_ha": 102.0,
        "scene_count": 2,
    },
    "imagery": {
        "before": {
            "path": "before.png",
            "bounds": [-60.5, -3.5, -60.2, -3.2],
            "label": "Synthetic before (2026-01-15)",
        },
        "after": {
            "path": "after.png",
            "bounds": [-60.5, -3.5, -60.2, -3.2],
            "label": "Synthetic after (2026-03-20)",
        },
        "change": {
            "path": "change.png",
            "bounds": [-60.5, -3.5, -60.2, -3.2],
            "label": "Synthetic change",
        },
    },
    "demo_region_id": "synthetic-region-1",
    "limitations": [
        "Synthetic fixture data; not derived from any real satellite acquisition."
    ],
}

BASE_REGIONS: dict[str, Any] = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "id": "synthetic-region-1",
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [-60.40, -3.40],
                        [-60.39, -3.40],
                        [-60.39, -3.39],
                        [-60.40, -3.39],
                        [-60.40, -3.40],
                    ]
                ],
            },
            "properties": {
                "region_id": "synthetic-region-1",
                "area_ha": 1.25,
                "change_db": -2.75,
                "magnitude_db": 2.75,
                "detected_at": "2026-03-20T10:00:00Z",
                "baseline_at": "2026-01-15T10:00:00Z",
                "observation_interval": {
                    "start": "2026-01-15T10:00:00Z",
                    "end": "2026-03-20T10:00:00Z",
                },
                "priority_score": 3.074593469062211,
                "priority_units": "dB sqrt(ha)",
                "priority_formula": "magnitude_db * sqrt(area_ha)",
                "persistence": {
                    "status": "not_evaluable",
                    "observations_after_detection": 0,
                    "changed_observations": 0,
                    "rate": None,
                },
                "historical_anomaly": None,
                "explanation": (
                    "Synthetic fixture region: magnitude_db and area_ha only; "
                    "persistence and historical anomaly are unavailable, not zero."
                ),
                "time_series": [
                    {
                        "acquired_at": "2026-01-15T10:00:00Z",
                        "mean_backscatter_db": -8.0,
                        "change_from_baseline_db": None,
                        "valid_fraction": 0.99,
                    },
                    {
                        "acquired_at": "2026-03-20T10:00:00Z",
                        "mean_backscatter_db": -10.75,
                        "change_from_baseline_db": -2.75,
                        "valid_fraction": 0.98,
                    },
                ],
            },
        },
        {
            "type": "Feature",
            "id": "synthetic-region-2",
            "geometry": {
                "type": "MultiPolygon",
                "coordinates": [
                    [
                        [
                            [-60.30, -3.30],
                            [-60.29, -3.30],
                            [-60.29, -3.29],
                            [-60.30, -3.29],
                            [-60.30, -3.30],
                        ]
                    ],
                    [
                        [
                            [-60.25, -3.25],
                            [-60.24, -3.25],
                            [-60.24, -3.24],
                            [-60.25, -3.24],
                            [-60.25, -3.25],
                        ]
                    ],
                ],
            },
            "properties": {
                "region_id": "synthetic-region-2",
                "area_ha": 3.25,
                "change_db": -1.5,
                "magnitude_db": 1.5,
                "detected_at": "2026-03-20T10:00:00Z",
                "baseline_at": "2026-01-15T10:00:00Z",
                "observation_interval": {
                    "start": "2026-01-15T10:00:00Z",
                    "end": "2026-03-20T10:00:00Z",
                },
                "priority_score": 2.7041634565979917,
                "priority_units": "dB sqrt(ha)",
                "priority_formula": "magnitude_db * sqrt(area_ha)",
                "persistence": {
                    "status": "not_evaluable",
                    "observations_after_detection": 0,
                    "changed_observations": 0,
                    "rate": None,
                },
                "historical_anomaly": None,
                "explanation": "Synthetic fixture region.",
                "time_series": [
                    {
                        "acquired_at": "2026-01-15T10:00:00Z",
                        "mean_backscatter_db": -9.0,
                        "change_from_baseline_db": None,
                        "valid_fraction": 1.0,
                    },
                    {
                        "acquired_at": "2026-03-20T10:00:00Z",
                        "mean_backscatter_db": -10.5,
                        "change_from_baseline_db": -1.5,
                        "valid_fraction": 1.0,
                    },
                ],
            },
        },
    ],
}


def analysis_document() -> dict[str, Any]:
    return copy.deepcopy(BASE_ANALYSIS)


def regions_document() -> dict[str, Any]:
    return copy.deepcopy(BASE_REGIONS)


def _png(path: Path) -> None:
    path.write_bytes(PNG_1X1_GRAY)


def write_bundle(
    root: Path,
    analysis: dict[str, Any] | None = None,
    regions: dict[str, Any] | None = None,
    imagery: tuple[str, ...] = ("before", "after", "change"),
) -> Path:
    """Create a synthetic bundle under ``root`` and return the bundle dir."""
    bundle = root / "current"
    bundle.mkdir(parents=True, exist_ok=True)
    analysis = analysis_document() if analysis is None else analysis
    regions = regions_document() if regions is None else regions
    (bundle / "analysis.json").write_text(json.dumps(analysis, indent=2))
    (bundle / "regions.geojson").write_text(json.dumps(regions, indent=2))
    for key in imagery:
        _png(bundle / f"{key}.png")
    return bundle


@pytest.fixture
def bundle_dir(tmp_path: Path) -> Path:
    return write_bundle(tmp_path)


@pytest.fixture
def client(bundle_dir: Path) -> TestClient:
    return TestClient(create_app(bundle_dir=bundle_dir))


@pytest.fixture
def empty_client(tmp_path: Path) -> TestClient:
    missing = tmp_path / "does-not-exist"
    return TestClient(create_app(bundle_dir=missing))


def png_size(data: bytes) -> tuple[int, int]:
    """Minimal PNG IHDR reader used to assert served image bytes are real PNG."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "served bytes are not a PNG"
    assert data[12:16] == b"IHDR"
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def png_chunk_count(data: bytes) -> int:
    count = 0
    offset = 8
    while offset < len(data):
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        count += 1
        offset += 12 + length
    return count


def reencode_png(width: int = 2, height: int = 2) -> bytes:
    """Build a small valid PNG deterministically (test-only synthetic raster)."""
    return _build_png(width, height)


# ---------------------------------------------------------------------------
# Multi-scene bundle: the only shape in which "observed" persistence can be
# true, because detection must precede at least one later acquisition. Four
# acquisitions so that a fractional persistence rate is reachable.
# ---------------------------------------------------------------------------

MULTI_SCENE_ACQUISITIONS = (
    "2026-01-15T10:00:00Z",
    "2026-03-20T10:00:00Z",
    "2026-06-10T10:00:00Z",
    "2026-08-05T10:00:00Z",
)


def multi_scene_analysis() -> dict[str, Any]:
    document = analysis_document()
    document["analysis_id"] = "synthetic-analysis-0004"
    document["scenes"][1]["id"] = "SYNTHETIC-MIDDLE"
    document["scenes"][1]["catalog_url"] = "https://example.invalid/catalog/SYNTHETIC-MIDDLE"
    document["scenes"].append(
        {
            "id": "SYNTHETIC-LATER",
            "acquired_at": MULTI_SCENE_ACQUISITIONS[2],
            "polarization": "VV",
            "beam_mode": "IW",
            "orbit_direction": "ASCENDING",
            "relative_orbit": 12345,
            "product_type": "GRD",
            "source_collection": "SYNTHETIC-COLLECTION",
            "catalog_url": "https://example.invalid/catalog/SYNTHETIC-LATER",
        }
    )
    document["scenes"].append(
        {
            "id": "SYNTHETIC-LAST",
            "acquired_at": MULTI_SCENE_ACQUISITIONS[3],
            "polarization": "VV",
            "beam_mode": "IW",
            "orbit_direction": "ASCENDING",
            "relative_orbit": 12345,
            "product_type": "GRD",
            "source_collection": "SYNTHETIC-COLLECTION",
            "catalog_url": "https://example.invalid/catalog/SYNTHETIC-LAST",
        }
    )
    document["metrics"].update(region_count=1, total_changed_area_ha=1.25, scene_count=4)
    document["demo_region_id"] = "synthetic-region-3"
    return document


def multi_scene_regions() -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": "synthetic-region-3",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [-60.40, -3.40],
                            [-60.39, -3.40],
                            [-60.39, -3.39],
                            [-60.40, -3.39],
                            [-60.40, -3.40],
                        ]
                    ],
                },
                "properties": {
                    "region_id": "synthetic-region-3",
                    "area_ha": 1.25,
                    "change_db": -2.75,
                    "magnitude_db": 2.75,
                    "detected_at": MULTI_SCENE_ACQUISITIONS[1],
                    "baseline_at": MULTI_SCENE_ACQUISITIONS[0],
                    "observation_interval": {
                        "start": MULTI_SCENE_ACQUISITIONS[0],
                        "end": MULTI_SCENE_ACQUISITIONS[1],
                    },
                    "priority_score": 3.074593469062211,
                    "priority_units": "dB sqrt(ha)",
                    "priority_formula": "magnitude_db * sqrt(area_ha)",
                    "persistence": {
                        "status": "observed",
                        "observations_after_detection": 1,
                        "changed_observations": 1,
                        "rate": 1.0,
                    },
                    "historical_anomaly": None,
                    "explanation": (
                        "Synthetic fixture region with one post-detection acquisition."
                    ),
                    "time_series": [
                        {
                            "acquired_at": stamp,
                            "mean_backscatter_db": value,
                            "change_from_baseline_db": change,
                            "valid_fraction": 1.0,
                        }
                        for stamp, value, change in (
                            (MULTI_SCENE_ACQUISITIONS[0], -8.0, None),
                            (MULTI_SCENE_ACQUISITIONS[1], -10.75, -2.75),
                            (MULTI_SCENE_ACQUISITIONS[2], -10.75, -2.75),
                        )
                    ],
                },
            }
        ],
    }


def write_multi_scene_bundle(root: Path) -> Path:
    bundle = root / "multi-scene"
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "analysis.json").write_text(json.dumps(multi_scene_analysis(), indent=2))
    (bundle / "regions.geojson").write_text(json.dumps(multi_scene_regions(), indent=2))
    for key in ("before", "after", "change"):
        _png(bundle / f"{key}.png")
    return bundle
