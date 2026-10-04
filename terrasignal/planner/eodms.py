"""Read-only, bounded catalogue search. No order submission or downloading."""
import ast
import contextlib
import io
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from shapely.geometry import shape

from .models import RS2_TROPICAL_COLLECTION
from .rs2_adapter import normalize_record

RESULT_FIELDS = ["Start Date", "Position", "Product Type", "Polarization", "Orbit Direction",
                 "Relative Orbit", "SIP Size (MB)", "Incidence Angle (Low)", "Incidence Angle (High)",
                 "Look Orientation", "Spatial Resolution"]


def credentials(path):
    values = {}
    for line in Path(path).read_text().splitlines():
        key, sep, value = line.strip().removeprefix("export ").partition("=")
        if sep and key.strip() in ("EODMS_USERNAME", "EODMS_PASSWORD"):
            value = value.strip()
            values[key.strip()] = ast.literal_eval(value) if value.startswith(('"', "'")) else value
    if set(values) != {"EODMS_USERNAME", "EODMS_PASSWORD"}:
        raise ValueError("EODMS credentials missing from local env file")
    return values


def search(aoi, env_file, *, beam=None, start=None, end=None, limit=5000, page_size=500):
    """Paginate until a short page or cap; persist normalized allowlisted data only."""
    from eodms_rapi import EODMSRAPI

    creds = credentials(env_file)
    logging.getLogger("eodms_rapi").setLevel(logging.CRITICAL)
    filters = {"Product Type": ("=", ["SLC"])}
    if beam:
        filters["Position"] = ("=", [beam])
    observations, ids = [], set()
    # The upstream client prints queries; suppress rather than persist raw output/URLs.
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        api = EODMSRAPI(creds["EODMS_USERNAME"], creds["EODMS_PASSWORD"])
        api.stdout_enabled = False
        api.set_query_timeout(90)
        try:
            first = datetime.strptime(start, "%Y%m%d_%H%M%S") if start else datetime(2007,1,1)
            last = datetime.strptime(end, "%Y%m%d_%H%M%S") if end else datetime.now(timezone.utc).replace(tzinfo=None)
            intervals = [(max(first,datetime(y,1,1)), min(last,datetime(y+1,1,1)-timedelta(seconds=1)))
                         for y in range(first.year,last.year+1)]
            truncated = False
            for begin, finish in intervals:
                offset = 0
                dates = [{"start": begin.strftime("%Y%m%d_%H%M%S"), "end": finish.strftime("%Y%m%d_%H%M%S")}]
                count_result = api.search(RS2_TROPICAL_COLLECTION, filters=filters,
                    features=[("Intersects", shape(aoi).wkt)], dates=dates, hit_count=True)
                expected = count_result.get("hitCount") if isinstance(count_result,dict) else None
                # RAPI exposes only 2,000 records per search window. Split dense windows.
                if expected is not None and expected > 2000 and (finish-begin).days > 1:
                    middle = begin + (finish-begin)/2
                    intervals.extend([(begin,middle), (middle+timedelta(seconds=1),finish)])
                    continue
                while len(observations) < limit:
                    api.clear_results()
                    size = min(page_size, limit-len(observations))
                    api.search(RS2_TROPICAL_COLLECTION, filters=filters,
                        features=[("Intersects", shape(aoi).wkt)], dates=dates,
                        result_fields=RESULT_FIELDS, max_results=size, first_result=offset+1)
                    records = api.get_results(form="raw", show_progress=False)
                    if api.err_occurred or not isinstance(records, list) or any("errors" in r for r in records):
                        raise RuntimeError("EODMS catalogue search failed; no raw response persisted")
                    new = 0
                    for record in records:
                        obs = normalize_record(record)
                        if obs.source_record_id not in ids:
                            observations.append(obs)
                            ids.add(obs.source_record_id)
                            new += 1
                    offset += len(records)
                    if len(records) < size or (expected is not None and offset >= expected):
                        truncated |= expected is not None and offset < expected
                        break
                    if not new:
                        raise RuntimeError("RAPI pagination repeated a page; refusing an incomplete coverage claim")
                if len(observations) >= limit:
                    return observations, True
            return observations, truncated
        finally:
            api.close_session()
