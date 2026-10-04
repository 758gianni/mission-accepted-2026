import pytest

from terrasignal.planner.rs2_adapter import normalize_record


def record(**extra):
    values = dict(recordId="32251268", isOrderable=True,
                  geometry={"type": "Polygon", "coordinates": [[[93, 22], [94, 22], [94, 23], [93, 23], [93, 22]]]},
                  metadata=[["Start Date", "2024-04-18T11:46:55 +0000"],
                            ["Position", "XF0W2"], ["Product Type", "SLC"],
                            ["Receive Polarization", "H"], ["Transmit Polarization", "H"],
                            ["Orbit Direction", "Ascending"], ["Look Orientation", "Right"],
                            ["Relative Orbit", "98"], ["SIP Size (MB)", "5188"],
                            ["Spatial Resolution", "6"], ["Incidence Angle (Low)", "31"],
                            ["Incidence Angle (High)", "39"]])
    values.update(extra)
    return values


def test_real_rapi_metadata_pairs_and_mib_not_decimal_mb():
    obs = normalize_record(record())
    assert obs.source_record_id == "32251268"
    assert obs.polarization == "HH"
    assert obs.relative_orbit == 98
    assert obs.size_bytes == 5188 * 1024**2
    assert obs.timestamp.isoformat() == "2024-04-18T11:46:55+00:00"
    assert obs.provenance["collection"] == "Radarsat-2_Tropical_Forest_Products"


def test_unknown_size_orderability_and_orbit_remain_unknown():
    r = record(isOrderable=None)
    r["metadata"] = [v for v in r["metadata"] if v[0] not in ("Relative Orbit", "SIP Size (MB)")]
    obs = normalize_record(r)
    assert obs.size_bytes is None and obs.orderable is None
    assert not obs.comparison_ready


def test_bad_geometry_fails_instead_of_silently_selecting():
    with pytest.raises(ValueError):
        normalize_record(record(geometry={"type": "Point", "coordinates": [94, 23]}))


def test_duplicate_related_product_sizes_are_not_added_together():
    r = record()
    r["metadata"] += [["SIP Size (MB)", "5190, 5190, "]]
    assert normalize_record(r).size_bytes == 5190 * 1024**2
    r["metadata"] += [["SIP Size (MB)", "5190, 5000, "]]
    assert normalize_record(r).size_bytes is None
