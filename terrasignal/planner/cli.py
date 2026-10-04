"""Read-only catalogue discovery and reproducible acquisition planning."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

from .coverage import plan_acquisitions, render_coverage_report
from .eodms import search
from .models import Observation
from terrasignal.catalogue import Catalogue

BOUNDARY_SOURCE = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_110m_admin_0_countries.geojson"


def boundary(country):
    """Public-domain Natural Earth outline; coarse national AOI, not a survey boundary."""
    with urlopen(BOUNDARY_SOURCE, timeout=60) as response:
        data = json.load(response)
    for f in data["features"]:
        if f["properties"].get("ADMIN", "").lower() == country.lower():
            return f["geometry"]
    raise ValueError("Country name not found in Natural Earth ADMIN field")


def local_records(raw_dir):
    """Read stable record IDs from completed CRC journals; never touch source ZIPs."""
    ids = set()
    for journal in Path(raw_dir).glob("orders-*.json"):
        data = json.loads(journal.read_text())
        if data.get("state") == "complete":
            for rid, files in data.get("archives", {}).items():
                if files and all(f.get("crc_verified") and Path(f["path"]).is_file() and
                                 Path(f["path"]).stat().st_size == f["bytes"] for f in files):
                    ids.add(rid)
    # Prototype T3 pre-dates the order journal but has independently recorded verification.
    if (Path(raw_dir)/"VERIFIED-PRODUCT.md").exists() and any(Path(raw_dir).glob("*20250107_114653*.zip")):
        ids.add("32252532")
    return ids


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aoi", type=Path, help="WGS84 GeoJSON geometry or Feature")
    parser.add_argument("--country", help="Natural Earth ADMIN name")
    parser.add_argument("--records", type=Path, help="Replay normalized observations; no network")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--beam")
    parser.add_argument("--orbit", choices=("Ascending", "Descending"))
    parser.add_argument("--relative-orbit", type=int)
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--budget", type=int, default=12)
    parser.add_argument("--raw-dir", type=Path)
    args = parser.parse_args()
    if not (args.aoi or args.country):
        parser.error("--aoi or --country required")
    if args.aoi:
        aoi = json.loads(args.aoi.read_text())
        aoi = aoi.get("geometry", aoi)
    else:
        aoi = boundary(args.country)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out/"aoi.geojson").write_text(json.dumps(aoi))
    if args.records:
        saved = json.loads(args.records.read_text())
        observations = [Observation(**o) for o in saved["observations"]]
        truncated = saved["catalogue_truncated"]
    else:
        if not args.env_file:
            parser.error("Live search requires --env-file")
        observations, truncated = search(aoi, args.env_file, beam=args.beam,
                                          start=args.start, end=args.end, limit=args.limit)
        (args.out/"observations.json").write_text(json.dumps({"observations": [o.to_dict() for o in observations],
                "catalogue_truncated": truncated, "boundary_source": BOUNDARY_SOURCE if args.country else str(args.aoi)}, indent=2))
    subset = observations
    if args.orbit:
        subset = [o for o in subset if o.orbit_direction == args.orbit]
    if args.relative_orbit is not None:
        subset = [o for o in subset if o.relative_orbit == args.relative_orbit]
    if args.start and args.end:
        start = datetime.strptime(args.start, "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
        end = datetime.strptime(args.end, "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
        subset = [o for o in subset if start <= o.timestamp <= end]
    plan = plan_acquisitions(subset, aoi, budget=args.budget, truncated=truncated,
                             local_ids=local_records(args.raw_dir) if args.raw_dir else ())
    plan["search_scope"] = {"beam":args.beam,"orbit":args.orbit,"relative_orbit":args.relative_orbit,
                            "start":args.start,"end":args.end,"unfiltered_observation_count":len(observations)}
    (args.out/"plan.json").write_text(json.dumps(plan, indent=2))
    (args.out/"coverage_report.md").write_text(render_coverage_report(plan))
    catalogue=Catalogue(args.out/'acquisition-catalogue.sqlite')
    catalogue.register_observations(observations)
    if args.raw_dir: catalogue.import_journals(args.raw_dir)
    catalogue.close()
    print(json.dumps({"observations": len(observations), "groups": len(plan["groups"]),
                      "decisions": {k: sum(d["plan_decision"] == k for d in plan["decisions"]) for k in ("acquire", "reuse", "reject")},
                      "coverage": {k:v for k,v in plan["coverage"].items() if isinstance(v,(int,float))},
                      "storage": plan["storage"], "catalogue_truncated": truncated}, indent=2))


if __name__ == "__main__":
    main()
