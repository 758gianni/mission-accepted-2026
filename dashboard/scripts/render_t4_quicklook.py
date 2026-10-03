#!/usr/bin/env python3
"""Render a T4 quicklook PNG from the aligned 4-date power rasters.

Requires rasterio + numpy + Pillow (the analysis venv). Writes
frontend/public/data/imagery/T4_quicklook.png for the comparison slot.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image

DEFAULT_ALIGNED = Path("/home/overlord/hackathon/rs2-analysis/data/derived/exploration_4date/aligned")
DEFAULT_OUT = Path(__file__).resolve().parents[2] / "frontend" / "public" / "data" / "imagery" / "T4_quicklook.png"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aligned", type=Path, default=DEFAULT_ALIGNED)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--width", type=int, default=1185)
    args = ap.parse_args()

    arrays = []
    for name in ["T4_N_geolocated_power.tif", "T4_S_geolocated_power.tif"]:
        with rasterio.open(args.aligned / name) as src:
            arrays.append(src.read(1).astype("float32"))
    stack = np.stack(arrays)
    with np.errstate(divide="ignore", invalid="ignore"):
        db = 10.0 * np.log10(np.where(stack > 0, stack, np.nan))
    merged = np.nanmax(db, axis=0)

    valid = merged[np.isfinite(merged)]
    lo, hi = np.percentile(valid, [2, 98])
    norm = np.clip((merged - lo) / max(hi - lo, 1e-6), 0, 1)
    img = (norm * 255).astype("uint8")
    img = np.where(np.isfinite(merged), img, 0)

    h, w = img.shape
    target_w = args.width
    target_h = int(h * (target_w / w))
    im = Image.fromarray(img, mode="L").resize((target_w, target_h), Image.BILINEAR)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    im.save(args.out)
    print(f"wrote {args.out} {im.size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
