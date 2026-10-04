"""Render one or more georeferenced power assets on a shared WGS84 display extent."""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.transform import from_bounds
from rasterio.warp import Resampling, reproject


def build_preview(sources: list[Path], bounds: tuple[float, float, float, float], output: Path,
                  width: int = 988, height: int = 1044) -> None:
    west, south, east, north = bounds
    transform = from_bounds(west, south, east, north, width, height)
    mosaic = np.full((height, width), np.nan, dtype="float32")
    for source_path in sources:
        with rasterio.open(source_path) as source:
            warped = np.full_like(mosaic, np.nan)
            reproject(
                source.read(1), warped,
                src_transform=source.transform, src_crs=source.crs,
                src_nodata=source.nodata if source.nodata is not None else np.nan,
                dst_transform=transform, dst_crs="EPSG:4326", dst_nodata=np.nan,
                resampling=Resampling.bilinear,
            )
        mosaic = np.where(np.isfinite(mosaic), mosaic, warped)

    valid = np.isfinite(mosaic) & (mosaic > 0)
    if not valid.any():
        raise ValueError("Input power assets do not cover the requested display extent")
    db = np.full_like(mosaic, np.nan)
    db[valid] = 10 * np.log10(mosaic[valid])
    low, high = np.quantile(db[valid], [0.02, 0.98])
    gray = np.clip((db - low) / max(float(high - low), 1e-6), 0, 1)
    rgb = np.repeat(np.nan_to_num(gray, nan=0)[..., None], 3, axis=2)
    rgb = (25 + rgb * 220).astype("uint8")
    alpha = np.where(valid, 255, 0).astype("uint8")
    rgba = np.dstack([rgb, alpha])
    output.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, mode="RGBA").save(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, action="append", required=True)
    parser.add_argument("--bounds", type=float, nargs=4, required=True, metavar=("WEST", "SOUTH", "EAST", "NORTH"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--width", type=int, default=988)
    parser.add_argument("--height", type=int, default=1044)
    args = parser.parse_args()
    build_preview(args.source, tuple(args.bounds), args.out, args.width, args.height)


if __name__ == "__main__":
    main()
