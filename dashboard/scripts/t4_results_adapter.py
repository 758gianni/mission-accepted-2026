#!/usr/bin/env python3
"""Adapt pipeline_4date T4 evaluation output into the dashboard t4_results.json.

Reads `persistent_candidates_t4_evaluation.json` produced by pipeline_4date.py
and emits the shape consumed by build_contract.py --t4-results.

Verdict mapping:
  SURVIVED_AND_CONFIRMED  -> confirmed
  WEAKENED_OR_TRANSIENT   -> weakened
  OUTSIDE_T4_SWATH        -> outside_swath
  anything else           -> pending

Pure stdlib. Does not touch raw SAR or the scientific pipeline.

Usage:
    python t4_results_adapter.py \
        --pipeline-output /home/overlord/hackathon/rs2-analysis/data/derived/exploration_4date \
        --out /tmp/t4_results.json [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

VERDICT_MAP = {
    "SURVIVED_AND_CONFIRMED": "confirmed",
    "PERSISTENT_MODERATE": "confirmed",
    "WEAKENED_OR_TRANSIENT": "weakened",
    "OUTSIDE_T4_SWATH": "outside_swath",
}

DEFAULT_PIPELINE_OUTPUT = Path("/home/overlord/hackathon/rs2-analysis/data/derived/exploration_4date")
EVAL_RELPATH = Path("change/persistent_candidates_t4_evaluation.json")


def _first(mapping: dict | None) -> float | None:
    if not mapping:
        return None
    for value in mapping.values():
        return value
    return None


def adapt(pipeline_output: Path, fallback_status: str = "outside_swath") -> dict:
    eval_path = pipeline_output / EVAL_RELPATH
    if not eval_path.exists():
        raise FileNotFoundError(
            f"T4 evaluation not found at {eval_path}. "
            "Run pipeline_4date.py first, or pass --pipeline-output."
        )
    payload = json.loads(eval_path.read_text())

    regions: dict[str, dict] = {}
    for cand in payload.get("candidates", []):
        region_id = str(cand.get("id"))
        verdict = cand.get("t4_verdict")
        status = VERDICT_MAP.get(verdict, fallback_status if verdict == "OUTSIDE_T4_SWATH" else "pending")
        if verdict is None:
            status = "pending"

        t4_powers = cand.get("t4_powers") or {}
        t4_deltas = cand.get("t4_deltas_db") or {}
        observation = None
        if t4_powers:
            scene = next(iter(t4_powers))
            delta_key = next(iter(t4_deltas), None)
            observation = (
                f"{scene} mean power {t4_powers[scene]:.5f}"
                + (f", {delta_key} {t4_deltas[delta_key]:+.2f} dB" if delta_key else "")
            )

        regions[region_id] = {
            "t4_status": status,
            "t4_mean_power": _first(t4_powers),
            "t4_mean_signed_db": _first(t4_deltas),
            "t4_observation": observation,
            "t4_verdict": verdict,
            "covered_in_T4": bool(cand.get("covered_in_T4")),
        }

    acquisition = {
        "id": "T4",
        "date": "2024-12-21",
        "iso": "2024-12-21T11:42:43Z",
        "beam": "XF0W2",
        "polarization": "HH",
        "orbit": "Ascending",
        "role": "persistence validation",
    }

    return {
        "regions": regions,
        "acquisition": acquisition,
        "summary": {
            "total_tested": payload.get("total_tested"),
            "covered_in_t4": payload.get("covered_in_t4"),
            "survived_confirmed": payload.get("survived_confirmed"),
            "weakened_transient": payload.get("weakened_transient"),
            "outside_t4_swath": payload.get("outside_t4_swath"),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pipeline-output", type=Path, default=DEFAULT_PIPELINE_OUTPUT)
    ap.add_argument("--out", type=Path, default=Path("/tmp/t4_results.json"))
    ap.add_argument("--fallback-status", default="outside_swath")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    result = adapt(args.pipeline_output, args.fallback_status)
    summary = result["summary"]
    confirmed = [k for k, v in result["regions"].items() if v["t4_status"] == "confirmed"]

    print(f"regions adapted: {len(result['regions'])}")
    print(f"summary: {summary}")
    print(f"confirmed region ids: {sorted(confirmed, key=int)}")

    if args.dry_run:
        print("dry-run: no file written")
        return 0

    args.out.write_text(json.dumps(result, indent=2))
    print(f"written: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
