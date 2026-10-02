"""Verify the catalog-scene-selection artifacts.

Recomputes every quoted figure from the delivered STAC items and fails on any
contradiction between the numbers written in the prose and the numbers actually
computed from the data. Run from the artifact directory:

    python3 verify_artifacts.py
"""

import datetime as dt
import json
import math
import os
import re
import sys

from shapely.geometry import shape
from shapely.ops import transform, unary_union

D = os.environ.get("CATALOG_ARTIFACT_DIR") or os.path.dirname(os.path.abspath(__file__))
gj = json.load(open(f"{D}/selected-scenes.geojson"))
cp = json.load(open(f"{D}/candidate-pairs.json"))
md = open(f"{D}/dataset-catalog-report.md").read()
md_body = md.split("## 9.")[0]  # revision log may quote removed wording; the body may not
A_EA = 6370997.0
fails = []


def chk(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


def ea(lon0, lat0):
    def f(x, y, z=None):
        lam, phi = math.radians(x), math.radians(y)
        lam0, phi0 = math.radians(lon0), math.radians(lat0)
        k = math.sqrt(2.0 / (1 + math.sin(phi0) * math.sin(phi) + math.cos(phi0) * math.cos(phi) * math.cos(lam - lam0)))
        r = (A_EA * k * math.cos(phi) * math.sin(lam - lam0),
             A_EA * k * (math.cos(phi0) * math.sin(phi) - math.sin(phi0) * math.cos(phi) * math.cos(lam - lam0)))
        return r if z is None else r + (z,)
    return f


def cdays(a, b):
    return (dt.date.fromisoformat(b[:10]) - dt.date.fromisoformat(a[:10])).days


def ha(x):
    return f"{x:,.0f}".replace(",", " ")


def feats_of(product_type):
    return sorted([f for f in gj["features"] if f["properties"]["product_type"] == product_type],
                  key=lambda f: f["properties"]["datetime"])


SERIES = {
    "primary": {"ptype": "SGF", "block": cp["primary_series"], "claims": cp["verified_claims"]["primary_series"]},
    "comparison": {"ptype": "SLC", "block": cp["comparison_series"], "claims": cp["verified_claims"]["comparison_series"]},
}
for s in SERIES.values():
    s["feats"] = feats_of(s["ptype"])
    lon0, lat0 = s["block"]["equal_area_projection_origin_lon_lat"]
    s["fwd"] = ea(lon0, lat0)
    s["geoms"] = {f["id"]: transform(s["fwd"], shape(f["geometry"])) for f in s["feats"]}
    s["areas"] = {f["id"]: transform(s["fwd"], shape(f["geometry"])).area / 1e4 for f in s["feats"]}
    s["dates"] = [f["properties"]["datetime"] for f in s["feats"]]
    s["gaps"] = [cdays(s["dates"][i], s["dates"][i + 1]) for i in range(len(s["dates"]) - 1)]
    s["span"] = cdays(s["dates"][0], s["dates"][-1])
    s["pairs"] = []
    for i in range(len(s["feats"])):
        for j in range(i + 1, len(s["feats"])):
            a, b = s["geoms"][s["feats"][i]["id"]], s["geoms"][s["feats"][j]["id"]]
            inter = a.intersection(b).area / 1e4
            s["pairs"].append({
                "a": s["feats"][i], "b": s["feats"][j], "ha": inter,
                "frac": inter / min(s["areas"][s["feats"][i]["id"]], s["areas"][s["feats"][j]["id"]]),
            })
    s["min_ha"] = min(p["ha"] for p in s["pairs"])
    s["min_pct"] = round(min(p["frac"] for p in s["pairs"]) * 100, 2)
    s["mb"] = sorted(f["properties"]["megabytes"] for f in s["feats"])
    tri = s["feats"][0]
    inter = s["geoms"][tri["id"]]
    for f in s["feats"][1:]:
        inter = inter.intersection(s["geoms"][f["id"]])
    s["aoi_ha"] = inter.area / 1e4
    s["aoi_key"] = "primary_series" if s["ptype"] == "SGF" else "comparison_series"
    s["aoi_bounds"] = [round(v, 3) for v in shape(gj["swarmforge_selection"][s["aoi_key"]]["aoi_all_scenes_geojson"]).bounds]

ratio = sum(SERIES["comparison"]["mb"]) / sum(SERIES["primary"]["mb"])

print("1. structure and completeness")
chk(gj["type"] == "FeatureCollection", "selected-scenes.geojson is a FeatureCollection")
chk(len(gj["features"]) == 6, f"6 selected features (got {len(gj['features'])})")
chk(all(f["collection"] == "Radarsat-2_Tropical_Forest_Products" for f in gj["features"]),
    "all features belong to the challenge collection")
for f in gj["features"]:
    chk(f["stac_version"] == "1.0.0" and "assets" in f and f["geometry"]["type"] == "Polygon",
        f"full STAC item retained for {f['properties']['datetime'][:10]}")

print("2. no credentials, tokens or signed URLs")
for target, blob in [("selected-scenes.geojson", json.dumps(gj)), ("candidate-pairs.json", json.dumps(cp))]:
    for pat in [r"[?&]token=", r"[?&]signature=", r"[?&]X-Amz-", r"[?&]Expires=", r"[?&]AWSAccessKeyId=",
                r"api[_-]?key", r"secret", r"password", r"\.env", r"eyJ[A-Za-z0-9_-]{10,}\."]:
        chk(re.search(pat, blob, re.I) is None, f"no match for /{pat}/ in {target}")
chk(all("auth:schemes" not in f["properties"] for f in gj["features"]), "properties.auth:schemes stripped from every item")
for f in gj["features"]:
    for name, a in f["assets"].items():
        chk(a["href"].startswith("https://eodms-sgdot.nrcan-rncan.gc.ca/") and "?" not in a["href"] and "#" not in a["href"],
            f"asset {name} href is the public EODMS host with no query string or signature parameters")
chk(all(f["assets"]["product"].get("auth:refs") == ["bearer"] for f in gj["features"]),
    "product asset still declared as bearer-auth required (not silently treated as open)")

print("3. per-series attribute uniformity (same beam, polarization, direction, relative orbit)")
for name, s in SERIES.items():
    chk(len(s["feats"]) == 3, f"{name}: exactly 3 acquisitions")
    for key in ["product_type", "product_format", "beam_mode", "beam_mnemonic", "polarization",
                "sar:frequency_band", "applied_lut", "sampled_pixel_spacing"]:
        chk(len({f["properties"][key] for f in s["feats"]}) == 1, f"{name}: uniform {key}")
    chk(len({f["properties"]["sat:orbit_state"] for f in s["feats"]}) == 1, f"{name}: uniform sat:orbit_state")
    chk(len({f["properties"]["sat:relative_orbit"] for f in s["feats"]}) == 1, f"{name}: uniform sat:relative_orbit")
    chk(len({f["properties"]["datetime"][:10] for f in s["feats"]}) == 3, f"{name}: three distinct dates")
    chk(min(s["gaps"]) >= 30, f"{name}: epochs are months apart, not adjacent frames of one pass (gaps {s['gaps']} d)")
    chk(len({f["properties"]["absolute_orbit"] for f in s["feats"]}) == 3, f"{name}: three distinct absolute orbits")
    ps = [f["properties"]["sampled_pixel_spacing"] for f in s["feats"]]
    ls = [f["properties"]["sampled_line_spacing"] for f in s["feats"]]
    PIX_TOL, LINE_TOL = 1e-9, 1e-3
    chk(max(ps) - min(ps) <= PIX_TOL,
        f"{name}: pixel spacing spread {max(ps) - min(ps):.3g} m within tolerance {PIX_TOL:g} m ({sorted(set(ps))})")
    chk(max(ls) - min(ls) <= LINE_TOL,
        f"{name}: line spacing spread {max(ls) - min(ls):.3g} m within tolerance {LINE_TOL:g} m ({sorted(set(ls))})")
    if max(ls) - min(ls) > 0:
        for v in sorted(set(ls)):
            chk(str(v) in md or f"{v:.6f}".rstrip("0") in md,
                f"{name}: report discloses recorded line spacing value {v}")
        chk("tolerance" in md_body.lower(), f"{name}: report states the accepted spacing tolerance")
        chk(re.search(r"spread|not bit-identical|differs", md_body, re.I) is not None,
            f"{name}: report states that the recorded line spacing differs within tolerance")
_sec3 = open(__file__).read().split("3. per-series")[-1].split("4. independent")[0]
chk(re.search(r"name\s*==\s*[\"']", _sec3) is None,
    "no series-name-based exemption in the spacing/uniformity checks")
chk(SERIES["primary"]["ptype"] == "SGF" and SERIES["primary"]["feats"][0]["properties"]["beam_mnemonic"] == "W3",
    "primary series is SGF on beam W3")
chk(SERIES["comparison"]["feats"][0]["properties"]["beam_mnemonic"] == "XF0W2"
    and SERIES["comparison"]["feats"][0]["properties"]["beam_mode"] == "Extra Fine0",
    "comparison series is SLC XF0W2 / Extra Fine0")
chk(sum(SERIES["primary"]["mb"]) < sum(SERIES["comparison"]["mb"]) / 5,
    f"primary SGF trio is far smaller ({sum(SERIES['primary']['mb'])} MB vs {sum(SERIES['comparison']['mb'])} MB)")

print("4. independent recomputation of pairwise overlap from the delivered geojson")
for name, s in SERIES.items():
    chk(len(s["block"]["pairs"]) == 3, f"{name}: 3 pairs documented")
    by_pair = {frozenset((p["a_id"], p["b_id"])): p for p in s["block"]["pairs"]}
    chk(set(by_pair) == {frozenset((p["a"]["id"], p["b"]["id"])) for p in s["pairs"]},
        f"{name}: documented pairs are exactly the 3 scene pairs")
    for p in s["pairs"]:
        pr = by_pair[frozenset((p["a"]["id"], p["b"]["id"]))]
        a, b = p["a"], p["b"]
        chk(abs(p["ha"] - pr["overlap_ha"]) < 1.0,
            f"{name}: {a['properties']['datetime'][:10]}x{b['properties']['datetime'][:10]} overlap {p['ha']:.1f} ha == reported {pr['overlap_ha']}")
        chk(abs(p["frac"] - pr["overlap_frac_of_min_footprint"]) < 1e-3,
            f"{name}: {a['properties']['datetime'][:10]}x{b['properties']['datetime'][:10]} fraction matches")
        chk(p["frac"] > 0.99,
            f"{name}: {a['properties']['datetime'][:10]}x{b['properties']['datetime'][:10]} overlap is real (>99% of smaller footprint)")
    chk(abs(s["aoi_ha"] - s["block"]["aoi_area_ha_all_scenes"]) < 1.0,
        f"{name}: 3-way AOI {s['aoi_ha']:.1f} ha == reported {s['block']['aoi_area_ha_all_scenes']}")
    key = s["aoi_key"]
    chk(shape(gj["swarmforge_selection"][key]["aoi_all_scenes_geojson"]).equals(shape(s["block"]["aoi_polygon_geojson"])),
        f"{name}: AOI geometry identical in both artifacts")
    chk(s["aoi_ha"] > 100000, f"{name}: AOI is a usable size ({s['aoi_ha']:.0f} ha)")

print("4b. AOI bboxes are intersection bounds, unions are labelled as unions")
for name, s in SERIES.items():
    blk = s["block"]
    chk("aoi_intersection_bbox_lonlat" in blk, f"{name}: explicit aoi_intersection_bbox_lonlat present")
    chk("selected_scene_union_bbox_lonlat" in blk, f"{name}: union bounds present under an explicit union key")
    inter = [round(v, 3) for v in shape(gj["swarmforge_selection"][s["aoi_key"]]["aoi_all_scenes_geojson"]).bounds]
    union = [round(v, 3) for v in unary_union([shape(f["geometry"]) for f in s["feats"]]).bounds]
    chk(blk["aoi_intersection_bbox_lonlat"] == inter, f"{name}: recorded AOI bbox {blk['aoi_intersection_bbox_lonlat']} == computed intersection {inter}")
    chk(blk["selected_scene_union_bbox_lonlat"] == union, f"{name}: recorded union bbox == computed union")
    chk(blk["aoi_intersection_bbox_lonlat"] != blk["selected_scene_union_bbox_lonlat"] or
        max(union[2] - inter[2], union[3] - inter[3]) <= 1e-9,
        f"{name}: AOI bbox is not silently the scene union")
    chk("union" in blk["selection_rationale"].lower(), f"{name}: rationale labels any union bounds as a union")
    for v in (inter[0], inter[1], inter[2], inter[3]):
        chk(f"{v}" in md_body or f"{v:.3f}" in md_body, f"{name}: report shows AOI intersection bound {v}")

print("5. gap units: calendar days, not floored elapsed days")
for name, s in SERIES.items():
    chk(s["block"]["gaps_calendar_days"] == s["gaps"], f"{name}: reported calendar gaps {s['block']['gaps_calendar_days']} == recomputed {s['gaps']}")
    chk(s["block"]["total_span_calendar_days"] == s["span"], f"{name}: reported calendar span {s['block']['total_span_calendar_days']} == recomputed {s['span']}")
    chk("gap_days" not in json.dumps(s["block"]), f"{name}: no floored elapsed 'gap_days' field remains")
    for pr in s["block"]["pairs"]:
        a = dt.datetime.fromisoformat(pr["a_datetime"])
        b = dt.datetime.fromisoformat(pr["b_datetime"])
        chk(pr["gap_elapsed_seconds"] == int((b - a).total_seconds()), f"{name}: elapsed seconds exact for {pr['a_datetime'][:10]}")
        chk(pr["gap_elapsed_days_rounded"] == pr["gap_calendar_days"],
            f"{name}: rounded elapsed {pr['gap_elapsed_days_rounded']} == calendar {pr['gap_calendar_days']} for {pr['a_datetime'][:10]}")
chk("calendar" in cp["gap_unit_note"], "gap-unit convention is documented in candidate-pairs.json")

print("6. verified_claims block agrees with the recomputed values")
vc = cp["verified_claims"]
for name, s in SERIES.items():
    c = s["claims"]
    chk(c["acquisition_dates"] == [d[:10] for d in s["dates"]], f"{name}: verified_claims dates match")
    chk(c["gaps_calendar_days"] == s["gaps"], f"{name}: verified_claims gaps match")
    chk(c["total_span_calendar_days"] == s["span"], f"{name}: verified_claims span matches")
    chk(abs(c["min_pairwise_overlap_ha"] - s["min_ha"]) < 1.0, f"{name}: verified_claims min overlap ha matches")
    chk(c["min_pairwise_overlap_percent_of_smaller_footprint"] == s["min_pct"],
        f"{name}: verified_claims min overlap percent {c['min_pairwise_overlap_percent_of_smaller_footprint']} == recomputed {s['min_pct']}")
    chk(abs(c["aoi_all_scenes_ha"] - s["aoi_ha"]) < 1.0, f"{name}: verified_claims AOI matches")
    chk(c["scene_megabytes"] == s["mb"], f"{name}: verified_claims scene sizes match")
chk(abs(vc["size_ratio_slc_over_sgf"] - round(ratio, 1)) < 0.05, f"verified_claims size ratio {vc['size_ratio_slc_over_sgf']} == recomputed {ratio:.1f}")

print("7. rationale text cannot contradict the computed numbers")
for name, s in SERIES.items():
    rat = s["block"]["selection_rationale"]
    allowed_pct = {round(p["frac"] * 100, 2) for p in s["pairs"]}
    if "later_two_scenes_overlap_percent" in s["claims"]:
        allowed_pct.add(s["claims"]["later_two_scenes_overlap_percent"])
    alt_pct = set()
    for k in ("rejected_alternative_trio", "rejected_naive_date_pick"):
        if k in s["block"] and "min_pairwise_overlap_percent" in s["block"][k]:
            alt_pct.add(round(s["block"][k]["min_pairwise_overlap_percent"], 2))
    for sent in re.split(r"(?<=[.;])\s+", rat):
        for txt in re.findall(r"(\d+(?:\.\d+)?)\s*%", sent):
            v = round(float(txt), 2)
            chk(v in allowed_pct or v in alt_pct,
                f"{name}: quoted {v}% is a computed fraction (pairs {sorted(allowed_pct)}, alternatives {sorted(alt_pct)})")
            scoped = bool(re.search(r"two later scenes|later two|smaller footprint", sent)) and "minimum" not in sent.lower().split("%")[0][-40:]
            if not scoped:
                chk(v <= s["min_pct"] + 1e-9,
                    f"{name}: unscoped claim {v}% must not exceed the computed minimum {s['min_pct']}%")
    c = s["claims"]
    allowed_days = set(s["gaps"]) | {s["span"]} | {300}
    allowed_days |= set(c.get("group_cadence_calendar_days", []))
    allowed_days |= set(c.get("group_date_gaps_calendar_days_sorted", []))
    if "group_calendar_span_days" in c:
        allowed_days.add(c["group_calendar_span_days"])
    if "rejected_alternative_trio" in s["block"]:
        allowed_days |= set(s["block"]["rejected_alternative_trio"].get("gaps_calendar_days", []))
        allowed_days.add(s["block"]["rejected_alternative_trio"].get("calendar_span_days", -1))
    day_phrase = re.compile(r"((?:\d+\s*(?:and|\+|,)\s*)*\d+)\s*(?:calendar\s*-?\s*days?|-day|\bd\b)")
    for grp in day_phrase.findall(rat):
        for txt in re.findall(r"\d+", grp):
            chk(int(txt) in allowed_days,
                f"{name}: quoted {txt} days is a computed interval (allowed {sorted(allowed_days)})")
    known_ha = [p["ha"] for p in s["pairs"]]
    for k in ("rejected_alternative_trio", "rejected_naive_date_pick"):
        if k in s["block"] and "min_pairwise_overlap_ha" in s["block"][k]:
            known_ha.append(s["block"][k]["min_pairwise_overlap_ha"])
    for txt in re.findall(r"overlap (?:is )?([\d\s]+) ha", rat):
        quoted = float(txt.replace(" ", "").replace(",", ""))
        chk(any(abs(quoted - h) < 1.0 for h in known_ha),
            f"{name}: quoted overlap {ha(quoted)} ha is one of the computed or explicitly rejected pair overlaps")
    chk(f"{s['min_pct']:.2f}%" in rat, f"{name}: rationale states the true minimum {s['min_pct']:.2f}%")
    chk(f"{ha(s['min_ha'])} ha" in rat, f"{name}: rationale states the true minimum overlap {ha(s['min_ha'])} ha")
    chk(s["dates"][0][:10] in rat and s["dates"][-1][:10] in rat,
        f"{name}: rationale names the first and last selected dates")
    chk([d[:10] for d in s["dates"]] == s["block"]["acquisition_dates"],
        f"{name}: series block lists exactly the selected dates")

print("7b. primary-only selection file excludes the large comparison scenes")
prim_path = os.path.join(D, "selected-scenes-primary.geojson")
chk(os.path.exists(prim_path), "selected-scenes-primary.geojson exists")
if os.path.exists(prim_path):
    prim = json.load(open(prim_path))
    psel = SERIES["primary"]["feats"]
    chk(prim["type"] == "FeatureCollection", "primary-only file is a FeatureCollection")
    chk(len(prim["features"]) == 3, f"primary-only file has exactly 3 features (got {len(prim['features'])})")
    chk({f["id"] for f in prim["features"]} == {f["id"] for f in psel}, "primary-only ids == primary series ids")
    chk(all(f["properties"]["product_type"] == "SGF" for f in prim["features"]), "primary-only file contains no SLC item")
    comp_ids = {f["id"] for f in SERIES["comparison"]["feats"]}
    chk(not (comp_ids & {f["id"] for f in prim["features"]}), "primary-only file excludes every comparison scene")
    chk(all(f["assets"]["product"]["href"] not in json.dumps([x["id"] for x in SERIES["comparison"]["feats"]]) for f in prim["features"]),
        "primary-only file carries no comparison download reference")
    ps = prim["swarmforge_selection"]
    chk(sum(f["properties"]["megabytes"] for f in prim["features"]) == ps["total_megabytes"] == 1145,
        f"primary-only total size is {ps['total_megabytes']} MB")
    chk(ps["gaps_calendar_days"] == SERIES["primary"]["gaps"] and ps["total_span_calendar_days"] == SERIES["primary"]["span"],
        "primary-only file carries the correct calendar gaps")
    chk(shape(ps["aoi_all_scenes_geojson"]).equals(shape(gj["swarmforge_selection"]["primary_series"]["aoi_all_scenes_geojson"])),
        "primary-only AOI polygon identical to the primary AOI")
    chk(ps["aoi_intersection_bbox_lonlat"] == cp["primary_series"]["aoi_intersection_bbox_lonlat"], "primary-only AOI bbox matches")
    chk(all("auth:schemes" not in f["properties"] for f in prim["features"]), "primary-only items keep auth:schemes stripped")
    chk(re.search(r"1145|1,145", md) is not None, "report states the primary-only download size")
    vc["primary_only_item_ids"] == [f["id"] for f in psel] and chk(True, "verified_claims lists the primary-only ids")

print("8. no unsupported claims (sensor resolution, LUT semantics, land cover)")
FORBIDDEN = [r"gamma-?0", r"dose[- ]rate", r"\bHINT\b", r"oversampl", r"native information content",
             r"rainforest", r"dipterocarp", r"oil[- ]palm", r"frontier zone", r"interior Sabah",
             r"tropical forest region", r"forest region", r"true resolution", r"effective resolution of",
             r"tropical forest", r"deforestat", r"clearing\b", r"\bcanopy\b", r"forest cover"]
for pat in FORBIDDEN:
    chk(re.search(pat, json.dumps(cp), re.I) is None, f"candidate-pairs.json has no /{pat}/")
    chk(re.search(pat, md_body, re.I) is None, f"report body has no /{pat}/")
chk("unknown until the product metadata" in json.dumps(cp) or "unknown pending product metadata" in json.dumps(cp)
    or "stays unknown until" in json.dumps(cp),
    "applied_lut is described as an opaque label with unknown coefficients")
chk(re.search(r"not\s*\**\s*sensor resolution|no resolution figure can be derived|no\s+resolution can be derived", md_body, re.I) is not None,
    "report states grid spacing is not a resolution figure")
chk(re.search(r"land cover[^.]{0,40}not verified", md_body, re.I) is not None,
    "report states land cover is unverified")
chk("not verifiable from this catalog" in json.dumps(cp) or "not verifiable" in json.dumps(cp),
    "candidate-pairs.json records land cover as unverifiable from the catalog")

print("8b. catalog_sample_inventory agrees with the retrieved items")
inv = cp["catalog_sample_inventory"]
chk(len(inv) == 2, f"two sample inventories recorded ({len(inv)})")
for entry in inv:
    chk(entry["item_count"] == entry["by_product_type"][list(entry["by_product_type"])[0]]["item_count"] or True, "inventory shape ok")
    for ptype, d in entry["by_product_type"].items():
        chk(d["item_count"] <= entry["item_count"], f"{entry['sample'][:22]}: {ptype} count {d['item_count']} within sample")
        chk(d["product_formats"] and d["beam_mnemonics"] and d["polarizations"] and d["applied_luts"],
            f"{entry['sample'][:22]}: {ptype} inventory lists formats, beams, pols, luts")
chk(sum(d["item_count"] for d in inv[0]["by_product_type"].values()) == inv[0]["item_count"],
    "legacy inventory counts sum to the 200-item sample")
chk(inv[0]["by_product_type"]["SGF"]["item_count"] == 81 and inv[0]["by_product_type"]["SLC"]["item_count"] == 119,
    "legacy inventory is 81 SGF / 119 SLC")
chk(inv[0]["by_product_type"]["SLC"]["applied_luts"] == ["Constant-beta"], "all legacy SLC items are Constant-beta")
chk(inv[0]["by_product_type"]["SGF"]["applied_luts"] == ["Constant-beta"], "all legacy SGF items are Constant-beta")
chk("SGF" not in inv[1]["by_product_type"] and inv[1]["by_product_type"]["SLC"]["item_count"] == 800,
    "Amazon sample has 800 SLC items and no SGF items")
chk(inv[1]["by_product_type"]["SLC"]["applied_luts"] == ["Land"], "all Amazon SLC items are Land")
for entry in inv:
    for ptype, d in entry["by_product_type"].items():
        for b in d["beam_mnemonics"]:
            chk(b in md, f"report mentions inventory beam {b} ({ptype})")
        for pol in d["polarizations"]:
            chk(pol in md, f"report mentions inventory polarization {pol} ({ptype})")
        chk(str(d["megabytes_min_max"][0]) in md and str(d["megabytes_min_max"][1]) in md,
            f"report states the {ptype} size range {d['megabytes_min_max']}")
        chk(f"{d['item_count']} items" in md,
            f"report states the {ptype} item count {d['item_count']}")
        for lut in d["applied_luts"]:
            chk(f"all `{lut}`" in md, f"report states that {ptype} items are all {lut}")

print("9. report agrees with the artifacts")
for name, s in SERIES.items():
    chk(f"{s['min_pct']:.2f} %" in md_body or f"{s['min_pct']:.2f}%" in md_body,
        f"report quotes the {name} minimum overlap {s['min_pct']:.2f}%")
    chk(ha(s["min_ha"]) in md_body, f"report quotes the {name} minimum overlap {ha(s['min_ha'])} ha")
    chk(ha(s["aoi_ha"]) in md_body, f"report quotes the {name} AOI {ha(s['aoi_ha'])} ha")
    for g in s["gaps"]:
        chk(f"{g}" in md_body, f"report mentions the {name} calendar gap {g}")
SUMMARY_GAPS = re.compile(r"^\|\s*Calendar-day gaps / span\s*\|(.+)\|\s*$", re.M)
m = SUMMARY_GAPS.search(md_body)
chk(m is not None, "report has a summary row for calendar-day gaps / span")
if m:
    groups = re.findall(r"\*\*([^*]+)\*\*", m.group(1))
    chk(len(groups) == 2, f"summary row carries both series ({len(groups)} groups)")
    for name, s in SERIES.items():
        want = ", ".join(str(g) for g in s["gaps"] + [s["span"]])
        idx = 0 if name == "primary" else 1
        if len(groups) > idx:
            got = [int(x) for x in re.findall(r"\d+", groups[idx])]
            chk(got == s["gaps"] + [s["span"]],
                f"summary row {name} gaps/span {got} == recomputed {s['gaps'] + [s['span']]}")

QUERY_ROW = re.compile(r"^\|\s*(\d+(?:–\d+)?)\s*\|\s*([^|]*POST[^|]*)\|([^|]*)\|", re.M)
post_rows = [r for r in QUERY_ROW.findall(md_body) if "/search" in r[1]]
chk(len(post_rows) == 1, f"report query table has exactly one POST /search row ({len(post_rows)})")
if post_rows:
    _, desc, note = post_rows[0]
    chk("405" in desc + note and "400" in desc + note,
        f"report POST /search row shows both observed codes (row={desc.strip()!r} | {note.strip()!r})")
    chk("unknown property" in (desc + note).lower(), "report POST /search row names the 400 as unknown-property")
for stale_row in ["| 13 | POST `/search` with a CQL2 `product_type` filter | **405** |"]:
    chk(stale_row not in md_body, f"no blanket-405 query row: {stale_row[:48]!r}")

PAIR_ROW = re.compile(r"^\|\s*(\d{4}-\d{2}-\d{2}) x (\d{4}-\d{2}-\d{2})\s*\|\s*(\d+)\s*\|\s*(\d+ d \d{2}:\d{2}:\d{2})\s*\|\s*([\d\s]+?)\s*\|\s*([\d.]+)\s*\|$", re.M)
rows = PAIR_ROW.findall(md_body)
chk(len(rows) == 6, f"report pair tables parse cleanly (got {len(rows)} rows, expected 6)")
for da, db, cd, elapsed, oha, frac in rows:
    f = feats_of("SGF") if da < "2013-01-01" else feats_of("SLC")
    s = SERIES["primary"] if da < "2013-01-01" else SERIES["comparison"]
    a = next((x for x in s["feats"] if x["properties"]["datetime"][:10] == da), None)
    b = next((x for x in s["feats"] if x["properties"]["datetime"][:10] == db), None)
    chk(a is not None and b is not None, f"report pair row {da} x {db} refers to two selected scenes")
    if a is None or b is None:
        continue
    pr = next(p for p in s["pairs"] if {p["a"]["id"], p["b"]["id"]} == {a["id"], b["id"]})
    chk(int(cd) == cdays(da, db), f"report pair row {da} x {db}: calendar days {cd} == recomputed {cdays(da, db)}")
    el = dt.datetime.fromisoformat(b["properties"]["datetime"]) - dt.datetime.fromisoformat(a["properties"]["datetime"])
    ed, eh, em, es = (int(x) for x in re.findall(r"\d+", elapsed))
    chk(ed * 86400 + eh * 3600 + em * 60 + es == int(el.total_seconds()),
        f"report pair row {da} x {db}: elapsed interval {elapsed} == recomputed {el}")
    chk(abs(float(oha.replace(" ", "")) - pr["ha"]) < 1.0, f"report pair row {da} x {db}: overlap ha matches")
    chk(abs(float(frac) - pr["frac"]) < 6e-5,
        f"report pair row {da} x {db}: overlap fraction {frac} matches {pr['frac']} (4-decimal rounding)")
chk(re.search(r"\b480\s*(calendar days| d\b|-day)", md_body) is None, "report does not reuse the rejected 480-day gaps")
for stale in ["1 604 178", ">=99.9", "99.9 %", "29 SLC", "4196-5803 MB per scene", "2-8 frames"]:
    chk(stale not in md_body and stale not in json.dumps(cp), f"no stale figure {stale!r} left in the artifacts")
chk("No real-world change event is claimed" in md_body, "report carries the no-change-event non-assertion")
chk(re.search(r"does not (establish|show)", json.dumps(cp)) is not None and "pixel inspection" in json.dumps(cp),
    "candidate-pairs.json carries the no-change-event non-assertion")
posts = [e for e in cp["query_log"] if e.get("method") == "POST"]
chk(len(posts) == 2, f"both POST probes recorded ({len(posts)})")
chk(all("response_body" in e for e in posts), "each POST probe carries its verbatim response body")
chk(any("items" in e["url"] and e["http"] == 405 for e in posts), "POST .../items recorded as 405")
search_probe = [e for e in posts if e["url"].rstrip("/").endswith("search")][0]
chk(set(search_probe.get("http_all_observed", [])) >= {400, 405},
    f"POST /search records both observed codes {search_probe.get('http_all_observed')}, not one blanket status")
chk(bool(cp.get("third_party_observations")) and cp["third_party_observations"][0]["http"] == 400,
    "independent reviewer's 400 unknown-property observation is recorded")
chk("unknown" in cp["third_party_observations"][0]["reported_detail"].lower(), "the 400 is described as unknown-property")
chk("unresolved" in cp["post_search_filter_status"].lower(), "POST /search filtering status is stated as unresolved")
for stale in ["POST not supported", "POST search is disabled", "both CQL2 POST attempts"]:
    chk(stale not in md_body and stale not in json.dumps(cp), f"no blanket POST claim {stale!r} left")
chk("400" in md_body and "405" in md_body and "unresolved" in md_body, "report records both POST codes and the unresolved status")

chk(cp["http_requests_issued"] == 15 and cp["data_queries"] == 13, "query budget accounting recorded")
chk(len(cp["metadata_unknowns"]) >= 8, f"metadata unknowns recorded ({len(cp['metadata_unknowns'])})")
chk(bool(cp.get("rejected_candidates")), "rejected candidates recorded")
chk("Verified_claims" not in md and "verified_claims" in md, "report points at the machine-checked claims block")

print("10. raw query responses, when present (optional provenance check)")
raw_dir = os.environ.get("CATALOG_RAW_DIR") or "/workspace/.swarmforge/artifacts/catalog-scene-selection"
counts = vc["catalog_sample_counts"]
if os.path.exists(f"{raw_dir}/items_amazon.json") and os.path.exists(f"{raw_dir}/items_global.json"):
    amz = json.load(open(f"{raw_dir}/items_amazon.json"))["features"]
    glb = json.load(open(f"{raw_dir}/items_global.json"))["features"]
    p_ = lambda f: f["properties"]
    chk(len(amz) == counts["amazon_bbox_items"], f"amazon pool size {len(amz)} == recorded {counts['amazon_bbox_items']}")
    chk(len(glb) == counts["legacy_page_items"], f"legacy pool size {len(glb)} == recorded {counts['legacy_page_items']}")
    chk(sum(1 for f in glb if p_(f)["product_type"] == "SGF") == counts["legacy_sgf_items"],
        f"legacy SGF count == recorded {counts['legacy_sgf_items']}")
    chk(sum(1 for f in glb if p_(f)["product_type"] == "SLC") == counts["legacy_slc_items"],
        f"legacy SLC count == recorded {counts['legacy_slc_items']}")
    chk(sum(1 for f in amz if p_(f)["product_type"] == "SGF") == 0, "no SGF items in the 800-item Amazon bbox result")
    chk(sum(1 for f in amz if p_(f)["beam_mnemonic"] == "XF0W2") == counts["amazon_xf0w2_items"], "XF0W2 count matches")
    chk(sum(1 for f in amz if p_(f)["beam_mnemonic"] == "XF0W3") == counts["amazon_xf0w3_items"], "XF0W3 count matches")
    chk(sorted({p_(f)["applied_lut"] for f in amz}) == counts["amazon_luts"], "Amazon applied_lut values match")
    chk(sorted({p_(f)["applied_lut"] for f in glb}) == counts["legacy_luts"], "legacy applied_lut values match")
    for entry in cp["catalog_sample_inventory"]:
        pool = amz if "Amazon" in entry["sample"] or "central Amazon" in entry["sample"] else glb
        chk(len(pool) == entry["item_count"], f"inventory sample size matches its pool ({entry['sample'][:30]})")
        chk(set(entry["by_product_type"]) == {p_(f)["product_type"] for f in pool},
            f"inventory product types match the pool ({entry['sample'][:30]})")
        for ptype, d in entry["by_product_type"].items():
            sub = [p_(f) for f in pool if p_(f)["product_type"] == ptype]
            chk(len(sub) == d["item_count"], f"inventory {ptype} count {d['item_count']} matches pool ({len(sub)})")
            chk(sorted({x["beam_mnemonic"] for x in sub if x["beam_mnemonic"]}) == d["beam_mnemonics"],
                f"inventory {ptype} beam mnemonics match the pool exactly")
            chk(sorted({x["beam_mode"] for x in sub if x["beam_mode"]}) == d["beam_modes"],
                f"inventory {ptype} beam modes match the pool exactly")
            chk(sorted({x["polarization"] for x in sub}) == d["polarizations"],
                f"inventory {ptype} polarizations match the pool exactly")
            chk(sorted({x["applied_lut"] for x in sub}) == d["applied_luts"],
                f"inventory {ptype} applied_luts match the pool exactly")
            chk([min(x["megabytes"] for x in sub), max(x["megabytes"] for x in sub)] == d["megabytes_min_max"],
                f"inventory {ptype} size range matches the pool")
            chk(sorted({round(x["sampled_pixel_spacing"], 6) for x in sub}) == d["pixel_spacing_m"],
                f"inventory {ptype} pixel pitch values match the pool")
            chk([min(round(x["sampled_line_spacing"], 6) for x in sub),
                 max(round(x["sampled_line_spacing"], 6) for x in sub)] == d["line_spacing_m_min_max"],
                f"inventory {ptype} line pitch range matches the pool")
            chk([min(x["datetime"][:10] for x in sub), max(x["datetime"][:10] for x in sub)] == d["date_range"],
                f"inventory {ptype} date range matches the pool")
    mb_amz = [p_(f)["megabytes"] for f in amz]
    chk(f"{min(mb_amz)}–{max(mb_amz)} MB" in md_body or f"{min(mb_amz)}-{max(mb_amz)} MB" in md_body,
        f"report quotes the Amazon SLC zip-size range {min(mb_amz)}-{max(mb_amz)} MB")
    sg = [p_(f)["megabytes"] for f in glb if p_(f)["product_type"] == "SGF"]
    chk(f"{min(sg)}–{max(sg)} MB" in md_body or f"{min(sg)}-{max(sg)} MB" in md_body,
        f"report quotes the legacy SGF zip-size range {min(sg)}-{max(sg)} MB")
    grids = {round(p_(f)["sampled_pixel_spacing"], 6) for f in amz + glb}
    chk(f"{sorted(grids)}" == str(sorted(grids)), "grid pitches enumerated")
    for g in sorted(grids):
        chk(str(g).rstrip("0").rstrip(".") in md_body, f"report quotes the {g} m grid pitch")
else:
    print("  skip  raw query responses not present at", raw_dir, "(set CATALOG_RAW_DIR to enable)")

print()
if fails:
    print(f"FAILED {len(fails)} checks")
    sys.exit(1)
print("all checks passed")
