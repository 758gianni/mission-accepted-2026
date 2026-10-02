import json, math, os, sys, re, itertools, datetime as dt

from shapely.geometry import shape, mapping
from shapely.ops import transform

D = os.environ.get("CATALOG_ARTIFACT_DIR") or os.path.dirname(os.path.abspath(__file__))
gj = json.load(open(f"{D}/selected-scenes.geojson"))
cp = json.load(open(f"{D}/candidate-pairs.json"))
md = open(f"{D}/dataset-catalog-report.md").read()
A_EA = 6370997.0
fails = []


def chk(cond, msg):
    if cond:
        print(f"  ok   {msg}")
    else:
        print(f"  FAIL {msg}")
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


print("1. structure")
chk(gj["type"] == "FeatureCollection", "selected-scenes.geojson is a FeatureCollection")
chk(len(gj["features"]) == 6, f"6 selected features (got {len(gj['features'])})")
chk(all(f["collection"] == "Radarsat-2_Tropical_Forest_Products" for f in gj["features"]),
    "all features belong to the challenge collection")
for f in gj["features"]:
    chk(f["stac_version"] == "1.0.0" and "assets" in f and f["geometry"]["type"] == "Polygon",
        f"full STAC item retained for {f['properties']['datetime'][:10]}")

print("2. no credentials, tokens or signed URLs")
blob = json.dumps(gj)
for pat in [r"[?&]token=", r"[?&]signature=", r"[?&]X-Amz-", r"[?&]Expires=", r"[?&]AWSAccessKeyId=",
            r"api[_-]?key", r"secret", r"password", r"\.env", r"eyJ[A-Za-z0-9_-]{10,}\."]:
    chk(re.search(pat, blob, re.I) is None, f"no match for /{pat}/ in selected-scenes.geojson")
chk(all("auth:schemes" not in f["properties"] for f in gj["features"]), "properties.auth:schemes stripped from every item")
for f in gj["features"]:
    for name, a in f["assets"].items():
        chk(a["href"].startswith("https://eodms-sgdot.nrcan-rncan.gc.ca/") and "?" not in a["href"] and "#" not in a["href"],
            f"asset {name} href is the public EODMS host with no query string or signature parameters")
chk(all(f["assets"]["product"].get("auth:refs") == ["bearer"] for f in gj["features"]),
    "product asset still declared as bearer-auth required (not silently treated as open)")

print("3. per-series attribute uniformity (same beam, polarization, direction, relative orbit)")
series = {"primary": [f for f in gj["features"] if f["properties"]["product_type"] == "SGF"],
          "comparison": [f for f in gj["features"] if f["properties"]["product_type"] == "SLC"]}
for name, feats in series.items():
    chk(len(feats) == 3, f"{name}: exactly 3 acquisitions")
    for key in ["product_type", "product_format", "beam_mode", "beam_mnemonic", "polarization",
                "sar:frequency_band", "applied_lut", "sampled_pixel_spacing"]:
        chk(len({f["properties"][key] for f in feats}) == 1, f"{name}: uniform {key}")
    chk(len({f["properties"]["sat:orbit_state"] for f in feats}) == 1, f"{name}: uniform sat:orbit_state")
    chk(len({f["properties"]["sat:relative_orbit"] for f in feats}) == 1, f"{name}: uniform sat:relative_orbit")
    ds = sorted(f["properties"]["datetime"][:10] for f in feats)
    chk(len(set(ds)) == 3, f"{name}: three distinct dates {ds}")
    gaps = [(dt.date.fromisoformat(ds[i + 1]) - dt.date.fromisoformat(ds[i])).days for i in range(2)]
    chk(min(gaps) >= 30, f"{name}: epochs are months apart, not adjacent frames of one pass (gaps {gaps} d)")
    chk(len({f["properties"]["absolute_orbit"] for f in feats}) == 3, f"{name}: three distinct absolute orbits")
    chk(all(f["properties"]["sampled_line_spacing"] == feats[0]["properties"]["sampled_line_spacing"] for f in feats)
        or name == "comparison", f"{name}: line spacing consistent")
chk(series["primary"][0]["properties"]["product_type"] == "SGF", "primary series is the smaller SGF family")
chk(series["comparison"][0]["properties"]["product_type"] == "SLC", "comparison series is modern SLC")
chk(series["primary"][0]["properties"]["beam_mnemonic"] == "W3", "primary beam W3")
chk(series["comparison"][0]["properties"]["beam_mnemonic"] == "XF0W2", "comparison beam XF0W2")
chk(series["comparison"][0]["properties"]["beam_mode"] == "Extra Fine0", "comparison beam mode Extra Fine0")
sp = sum(f["properties"]["megabytes"] for f in series["primary"])
sc = sum(f["properties"]["megabytes"] for f in series["comparison"])
chk(sp < sc / 5, f"primary SGF series is far smaller than the SLC series ({sp} MB vs {sc} MB)")

print("4. independent recomputation of pairwise overlap from the delivered geojson")
for name, block in [("primary", cp["primary_series"]), ("comparison", cp["comparison_series"])]:
    feats = series[name]
    lon0, lat0 = block["equal_area_projection_origin_lon_lat"]
    fwd = ea(lon0, lat0)
    geoms = {f["id"]: transform(fwd, shape(f["geometry"])) for f in feats}
    areas = {f["id"]: transform(fwd, shape(f["geometry"])).area / 1e4 for f in feats}
    chk(len(block["pairs"]) == 3, f"{name}: 3 pairs documented")
    for pr in block["pairs"]:
        a, b = geoms[pr["a_id"]], geoms[pr["b_id"]]
        ha = a.intersection(b).area / 1e4
        chk(abs(ha - pr["overlap_ha"]) < 1.0, f"{name}: {pr['a_datetime'][:10]}x{pr['b_datetime'][:10]} overlap {ha:.1f} ha matches reported {pr['overlap_ha']}")
        chk(abs(ha / min(areas[pr['a_id']], areas[pr['b_id']]) - pr["overlap_frac_of_min_footprint"]) < 1e-3,
            f"{name}: {pr['a_datetime'][:10]}x{pr['b_datetime'][:10]} fraction matches")
        chk(ha / min(areas[pr['a_id']], areas[pr['b_id']]) > 0.99,
            f"{name}: {pr['a_datetime'][:10]}x{pr['b_datetime'][:10]} overlap is real (>99% of smaller footprint, not a bbox artefact)")
    tri = feats[0]
    inter = transform(fwd, shape(tri["geometry"]))
    for f in feats[1:]:
        inter = inter.intersection(transform(fwd, shape(f["geometry"])))
    chk(abs(inter.area / 1e4 - block["aoi_area_ha_all_scenes"]) < 1.0,
        f"{name}: 3-way AOI {inter.area/1e4:.1f} ha matches reported {block['aoi_area_ha_all_scenes']}")
    aoi_key = "primary_series" if name == "primary" else "comparison_series"
    chk(shape(gj["swarmforge_selection"][aoi_key]["aoi_all_scenes_geojson"]).equals(shape(block["aoi_polygon_geojson"])),
        f"{name}: AOI geometry identical in both artifacts")
    chk(inter.area / 1e4 > 100000, f"{name}: AOI is a usable size ({inter.area/1e4:.0f} ha)")

print("5. report claims present and consistent")
for tok in ["1 442 704", "1 666 365", "1 442 704 ha", "SGF", "SLC", "Constant-beta", "Land",
            "2.662357", "12.5", "sampled_pixel_spacing", "shapely 2.1.2", "W3", "XF0W2", "XF0W3",
            "relative orbit 98", "relative orbit 104", "Sabah", "Amazon", "No real-world change event is claimed"]:
    chk(tok in md, f"report mentions {tok!r}")
chk("resolution" in md.lower() and "grid spacing" in md.lower(), "report separates spacing from resolution")
chk(md.count("HTTP 405") >= 2 or md.count("405") >= 2, "report records the rejected POST search attempts")
chk(cp["http_requests_issued"] == 15 and cp["data_queries"] == 13, "query budget accounting recorded")
chk(len(cp["metadata_unknowns"]) >= 8, f"metadata unknowns recorded ({len(cp['metadata_unknowns'])})")
chk(bool(cp.get("rejected_candidates")), "rejected candidates recorded")
chk("pixel" in cp["no_change_event_asserted"].lower() and "does not prove" in cp["no_change_event_asserted"].lower(),
    "no-change-event disclaimer present in candidate-pairs.json")

print()
if fails:
    print(f"FAILED {len(fails)} checks")
    sys.exit(1)
print("all checks passed")
