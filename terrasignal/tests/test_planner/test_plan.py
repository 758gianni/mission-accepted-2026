from dataclasses import replace

from shapely.geometry import box, mapping

from terrasignal.planner.models import Observation
from terrasignal.planner.coverage import plan_acquisitions


def obs(rid, date, geom=None, **kwargs):
    return Observation(source_record_id=rid, sensor="RADARSAT-2", acquisition_iso=date,
                       footprint=mapping(geom or box(93, 22, 94, 23)), measurement="sigma0_linear_power",
                       beam_mnemonic="XF0W2", polarization="HH", orbit_direction="Ascending",
                       look_direction="Right", relative_orbit=256, processing_level="SLC",
                       incidence_low_deg=31, incidence_high_deg=39, orderable=True, size_bytes=100, **kwargs)


def test_time_and_geography_must_overlap_and_duplicate_date_is_not_validation():
    records = [obs("a", "2024-01-01"), obs("b", "2024-06-01"), obs("c", "2024-12-01"),
               obs("duplicate", "2024-12-01"), obs("distant", "2024-01-02", box(95,22,96,23))]
    result = plan_acquisitions(records, mapping(box(93,22,96,23)), budget=4)
    selected = {r["source_record_id"] for r in result["decisions"] if r["plan_decision"] == "acquire"}
    assert len(selected) == 3
    assert "distant" not in selected
    assert not {"duplicate", "c"} <= selected
    assert result["coverage"]["selected_three_date_area_km2"] > 0
    assert result["coverage"]["selected_three_date_fraction"] < 0.5


def test_local_duplicate_ids_do_not_double_charge_and_missing_geometry_rejected():
    records = [obs("a", "2024-01-01"), obs("b", "2024-06-01"), obs("c", "2024-12-01")]
    result = plan_acquisitions(records + [records[0]], records[0].footprint, budget=3, local_ids={"a"})
    assert len(result["decisions"]) == 3
    assert result["storage"]["estimated_new_raw_bytes"] == 200
    assert any(d["plan_decision"] == "reuse" for d in result["decisions"])
    bad = [replace(r, relative_orbit=None) for r in records]
    result = plan_acquisitions(bad, records[0].footprint, budget=3)
    assert all(d["plan_decision"] == "reject" for d in result["decisions"])


def test_sensor_specific_stacks_and_limit_is_not_coverage_claim():
    records = [obs("a", "2024-01-01"), obs("b", "2024-06-01"), obs("c", "2024-12-01")]
    records += [replace(r, sensor="Sentinel-1", source_record_id="s"+r.source_record_id) for r in records]
    result = plan_acquisitions(records, records[0].footprint, budget=3, truncated=True)
    assert result["catalogue_truncated"] is True
    assert len(result["groups"]) == 1  # Only RS2 supplies primary candidate-generation stacks.
    assert all(d["plan_decision"] == "reject" for d in result["decisions"] if d["sensor"] == "Sentinel-1")
