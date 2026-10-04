"""Geometry-aware planning: choose useful spatial/date coverage, not every scene."""
from collections import defaultdict
from math import floor, ceil

from pyproj import Transformer
from shapely.geometry import box, mapping, shape
from shapely.ops import unary_union, transform

from .models import comparable_group_key

AREA_TRANSFORM = Transformer.from_crs("EPSG:4326", "EPSG:6933", always_xy=True)


def area_km2(geom):
    if geom.is_empty:
        return 0.0
    if geom.geom_type == "GeometryCollection":
        return sum(area_km2(g) for g in geom.geoms)
    return transform(AREA_TRANSFORM.transform, geom.segmentize(0.01)).area / 1e6


def repeated_footprint(observations, aoi, depth=3):
    """Exact catalogue-footprint overlap across unique dates; not valid-pixel coverage."""
    by_date = defaultdict(list)
    for obs in observations:
        by_date[obs.timestamp.date()].append(shape(obs.footprint).intersection(aoi))
    levels = [shape({"type": "GeometryCollection", "geometries": []}) for _ in range(depth)]
    for geoms in by_date.values():
        daily = unary_union(geoms)
        for k in range(depth-1, 0, -1):
            levels[k] = levels[k].union(levels[k-1].intersection(daily))
        levels[0] = levels[0].union(daily)
    return levels[-1]


def plan_acquisitions(observations, aoi_geometry, *, budget=12, local_ids=(),
                      min_dates=3, cell_degrees=0.25, truncated=False):
    if budget < 1 or min_dates < 2 or cell_degrees <= 0:
        raise ValueError("Positive budget/cell size and at least two dates required")
    aoi = shape(aoi_geometry)
    if not aoi.is_valid or aoi.is_empty or aoi.geom_type not in ("Polygon", "MultiPolygon"):
        raise ValueError("AOI must be a valid WGS84 polygon")
    if aoi.bounds[2]-aoi.bounds[0] > 180:
        raise ValueError("Antimeridian AOIs require splitting before planning")
    # Unique source IDs per sensor. Different sensors are separate measurements.
    records = list({(o.sensor, o.source_record_id): o for o in observations}.values())
    local_ids = set(local_ids)
    groups, rejects = defaultdict(list), {}
    for obs in records:
        if obs.sensor != "RADARSAT-2":
            reason = "Supporting sensor; excluded from primary RS2 acquisition plan"
        elif not obs.comparison_ready:
            reason = "Missing acquisition geometry metadata; review before comparison"
        elif obs.orderable is not True and obs.source_record_id not in local_ids:
            reason = "Not confirmed orderable"
        elif not shape(obs.footprint).intersects(aoi):
            reason = "Outside AOI"
        else:
            groups[comparable_group_key(obs)].append(obs)
            continue
        rejects[(obs.sensor, obs.source_record_id)] = reason

    minx, miny, maxx, maxy = aoi.bounds
    cells = {}
    for x in range(floor(minx/cell_degrees), ceil(maxx/cell_degrees)):
        for y in range(floor(miny/cell_degrees), ceil(maxy/cell_degrees)):
            geom = box(x*cell_degrees, y*cell_degrees, (x+1)*cell_degrees, (y+1)*cell_degrees).intersection(aoi)
            if not geom.is_empty and geom.area > 0:
                cells[(x,y)] = (geom.representative_point(), area_km2(geom))
    samples, targets = {}, {}
    for key, members in groups.items():
        dates = defaultdict(set)
        for obs in members:
            geom=shape(obs.footprint)
            covered = {c for c, (point, _) in cells.items() if geom.covers(point)}
            samples[(obs.sensor, obs.source_record_id)] = covered
            for cell in covered:
                dates[cell].add(obs.timestamp.date())
        targets[key] = {c for c, d in dates.items() if len(d) >= min_dates}

    def benefit(n):
        return (n/min_dates)**2 if n <= min_dates else 1 + 0.15*(n-min_dates)

    selected, achieved = [], defaultdict(set)
    candidates = [(key,o) for key,members in groups.items() for o in members]
    while candidates:
        ranked = []
        new_count = sum(o.source_record_id not in local_ids for _,o in selected)
        for key, obs in candidates:
            if obs.source_record_id not in local_ids and new_count >= budget:
                continue
            day = obs.timestamp.date()
            gain = sum(cells[c][1]*(benefit(len(achieved[(key,c)])+1)-benefit(len(achieved[(key,c)])))
                       for c in samples[(obs.sensor,obs.source_record_id)] & targets[key]
                       if day not in achieved[(key,c)])
            if gain > 0:
                cost = 1 if obs.source_record_id in local_ids else max((obs.size_bytes or 5_500_000_000)/5_500_000_000, 0.01)
                ranked.append((gain/cost, obs.timestamp.isoformat(), obs.source_record_id, key, obs))
        if not ranked:
            break
        _, _, _, key, obs = max(ranked, key=lambda r: r[:3])
        selected.append((key,obs))
        candidates.remove((key,obs))
        for cell in samples[(obs.sensor,obs.source_record_id)]:
            achieved[(key,cell)].add(obs.timestamp.date())

    chosen = {(o.sensor,o.source_record_id) for _,o in selected}
    decisions = []
    for obs in records:
        rid = (obs.sensor,obs.source_record_id)
        key = comparable_group_key(obs)
        chosen_here = rid in chosen
        decision = ("reuse" if obs.source_record_id in local_ids else "acquire") if chosen_here else "reject"
        reason = "Adds distinct-date coverage to a comparable spatial stack" if chosen_here else rejects.get(
            rid, "No additional repeat-date spatial coverage, insufficient history, or scene budget reached")
        decisions.append({"sensor": obs.sensor, "source_record_id": obs.source_record_id,
                          "comparable_group_key": key, "plan_decision": decision, "plan_reason": reason})

    summaries, available_repeat, selected_repeat = [], [], []
    for key, members in groups.items():
        timestamps = sorted({o.timestamp.date() for o in members})
        gaps = [(b-a).days for a,b in zip(timestamps,timestamps[1:])]
        chosen_members = [o for k,o in selected if k == key]
        avail = repeated_footprint(members,aoi,min_dates)
        planned = repeated_footprint(chosen_members,aoi,min_dates)
        available_repeat.append(avail)
        selected_repeat.append(planned)
        summaries.append({"comparable_group_key": key, "source_record_ids": [o.source_record_id for o in members],
                          "unique_dates": len(timestamps), "temporal_span_days": round(sum(gaps),2),
                          "max_temporal_gap_days": round(max(gaps, default=0),2),
                          "t4_window_score": sum(g <= 60 for g in gaps)/len(gaps) if gaps else 0,
                          "t4_window_score_note": "Fraction of distinct-date gaps <=60 days; acquisition opportunity, not scientific confidence",
                          "available_repeat_area_km2": area_km2(avail),
                          "selected_repeat_area_km2": area_km2(planned)})
    union_all = unary_union([shape(o.footprint).intersection(aoi) for o in records if o.sensor == "RADARSAT-2"])
    selected_geom = unary_union(selected_repeat)
    total_area = area_km2(aoi)
    new = [o for _,o in selected if o.source_record_id not in local_ids]
    result={"schema_version": 1, "primary_source": "RADARSAT-2", "aoi": aoi_geometry,
            "catalogue_truncated": truncated, "selection_budget_new_scenes": budget,
            "min_distinct_dates": min_dates, "observations": [o.to_dict() for o in records],
            "groups": summaries, "decisions": decisions,
            "coverage": {"aoi_area_km2": total_area,
                         "any_rs2_footprint_area_km2": area_km2(union_all),
                         "any_rs2_footprint_fraction": min(area_km2(union_all)/total_area,1.0),
                         "available_repeat_area_km2": area_km2(unary_union(available_repeat)),
                         "selected_repeat_area_km2": area_km2(selected_geom),
                         "selected_repeat_fraction": min(area_km2(selected_geom)/total_area,1.0),
                         "uncovered_aoi": mapping(aoi.difference(union_all)),
                         "selected_repeat_footprint": mapping(selected_geom),
                         "limitation": "Catalogue outlines only; geocoding, registration, and valid-pixel overlap remain processing gates",
                         "selection_sampling_degrees": cell_degrees},
            "storage": {"estimated_new_raw_bytes": sum(o.size_bytes or 0 for o in new),
                        "unknown_size_new_scenes": sum(o.size_bytes is None for o in new),
                        "reused_scenes": sum(o.source_record_id in local_ids for _,o in selected)}}
    if min_dates==3:
        result['coverage']['selected_three_date_area_km2']=result['coverage']['selected_repeat_area_km2']
        result['coverage']['selected_three_date_fraction']=result['coverage']['selected_repeat_fraction']
    return result


def render_coverage_report(plan):
    c, s = plan["coverage"], plan["storage"]
    lines = ["# TerraSignal acquisition plan", "", "Primary signal: RADARSAT-2 Tropical Forests.",
             f"Catalogue truncated: {plan['catalogue_truncated']}.",
             f"AOI: {c['aoi_area_km2']:,.0f} km². Any RS2 footprint: {c['any_rs2_footprint_fraction']:.2%}.",
             f"Selected {plan['min_distinct_dates']}-date footprint: {c['selected_repeat_area_km2']:,.0f} km² ({c['selected_repeat_fraction']:.2%}).",
             f"Estimated new raw: {s['estimated_new_raw_bytes']/1e9:.2f} GB; unknown-size scenes: {s['unknown_size_new_scenes']}.",
             "", "## Selection and provenance", "", "| Record | Sensor | Decision | Reason |", "|---|---|---|---|"]
    lines.extend(f"| {d['source_record_id']} | {d['sensor']} | {d['plan_decision']} | {d['plan_reason']} |" for d in plan["decisions"])
    lines += ["", "## Temporal groups and gaps", ""]
    for g in plan["groups"]:
        lines.append(f"- `{g['comparable_group_key']}`: {g['unique_dates']} dates, {g['temporal_span_days']:.0f} days, largest gap {g['max_temporal_gap_days']:.0f} days; repeat footprint {g['available_repeat_area_km2']:,.0f} km².")
    lines += ["", "## Next action", "Review the ranked compatible regional subset before any new acquisition. Extend temporal search where repeat coverage is missing; do not blend incompatible sensors to manufacture history.",
              "", "## Scientific limits", c["limitation"],
              "Spatial selection uses sampled 0.25° cells; reported footprint intersections use polygon geometry. A temporal gap score is not a confidence percentage. Three dates demonstrate return/persistence, not recurring annual seasonality."]
    return "\n".join(lines)+"\n"
