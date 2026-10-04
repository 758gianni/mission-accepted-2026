from datetime import datetime, timezone

import pytest

from terrasignal.planner.models import Observation, comparable_group_key


def observation(**extra):
    values = dict(source_record_id="123", sensor="RADARSAT-2",
                  acquisition_iso="2024-04-18T11:46:55Z",
                  footprint={"type": "Polygon", "coordinates": [[[93, 22], [94, 22], [94, 23], [93, 23], [93, 22]]]},
                  measurement="sigma0_linear_power", beam_mnemonic="XF0W2",
                  polarization="HH", orbit_direction="Ascending", look_direction="Right",
                  relative_orbit=98, processing_level="SLC", incidence_low_deg=31.5,
                  incidence_high_deg=38.7)
    values.update(extra)
    return Observation(**values)


def test_metadata_normalization_does_not_merge_sensors_or_geometry():
    base = observation()
    for changes in [dict(sensor="Sentinel-1"), dict(relative_orbit=99),
                    dict(polarization="VV"), dict(look_direction="Left"),
                    dict(orbit_direction="Descending"), dict(measurement="optical_reflectance")]:
        assert comparable_group_key(base) != comparable_group_key(observation(**changes))
    assert base.timestamp == datetime(2024, 4, 18, 11, 46, 55, tzinfo=timezone.utc)


def test_missing_geometry_is_not_treated_as_known_comparable():
    assert observation(relative_orbit=None).quality_flags
    assert observation(relative_orbit=None).comparison_ready is False


def test_provenance_and_timestamp_required():
    with pytest.raises(ValueError):
        observation(source_record_id="")
    with pytest.raises(ValueError):
        observation(acquisition_iso="not a date")
