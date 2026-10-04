"""Opt-in catalogue contract check; no order creation or image download."""
import os
from pathlib import Path

import pytest
from shapely.geometry import box, mapping

from terrasignal.planner.eodms import search


@pytest.mark.skipif(os.getenv("TERRASIGNAL_LIVE") != "1", reason="Set TERRASIGNAL_LIVE=1 for read-only catalogue check")
def test_live_returns_validated_prototype_records():
    env = Path(os.getenv("TERRASIGNAL_ENV", "/home/overlord/hackathon/mission-accepted-2026/.env"))
    if not env.exists():
        pytest.skip("No local credential file")
    observations, truncated = search(mapping(box(93,22,95,24)), env, beam="XF0W2",
        start="20240101_000000", end="20250131_235959", limit=1000)
    assert not truncated
    by_id = {o.source_record_id: o for o in observations}
    for rid in ("32251268", "32251963", "32252532"):
        assert by_id[rid].beam_mnemonic == "XF0W2"
        assert by_id[rid].polarization == "HH"
        assert by_id[rid].relative_orbit == 256
