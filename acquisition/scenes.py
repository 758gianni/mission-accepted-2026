"""Scene selection helpers and upstream argument mapping.

The wrapper owns no data model: it only turns a selection file (GeoJSON
FeatureCollection or the upstream download manifest) into the ``--input``
argument the upstream CLI already understands, and builds the upstream
``search``/``download`` argument vectors.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

_ID_KEYS = ("id", "uuid", "item_uuid", "itemUuid", "orderId", "order_id")

BBOX_HELP = "Bounding box as west,south,east,north"
DATETIME_HELP = 'ISO 8601 instant or range, e.g. "2024-01-01/2024-12-31"'


def extract_uuids(payload: Any) -> list[str]:
    """Pull unique item UUIDs out of a FeatureCollection or manifest payload."""
    if isinstance(payload, dict):
        for container_key in ("features", "items"):
            container = payload.get(container_key)
            if isinstance(container, list):
                found: list[str] = []
                for entry in container:
                    if isinstance(entry, dict):
                        found.extend(_ids_from_mapping(entry))
                return _dedupe(found)
        found = _ids_from_mapping(payload)
        if found:
            return _dedupe(found)
    elif isinstance(payload, list):
        found = []
        for entry in payload:
            if isinstance(entry, dict):
                found.extend(_ids_from_mapping(entry))
        return _dedupe(found)
    raise ValueError("no item identifiers found in selection payload")


def _ids_from_mapping(entry: dict[str, Any]) -> list[str]:
    for key in _ID_KEYS:
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return [value.strip()]
    properties = entry.get("properties")
    if isinstance(properties, dict):
        for key in _ID_KEYS:
            value = properties.get(key)
            if isinstance(value, str) and value.strip():
                return [value.strip()]
    return []


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


def load_scenes(path: str | Path) -> list[str]:
    """Read UUIDs from a selection file (``.geojson``/``.json``/``.jsonl``)."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    stripped = text.strip()
    if not stripped:
        raise ValueError(f"Selection file is empty: {path}")
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        entries = []
        for line in stripped.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL in {path}: {exc}") from exc
        payload = entries
    uuids = extract_uuids(payload)
    if not uuids:
        raise ValueError(f"No selected scenes found in {path}")
    return uuids


def validate_bbox(value: str) -> str:
    parts = [part.strip() for part in str(value).split(",")]
    if len(parts) != 4:
        raise ValueError("bbox must be west,south,east,north (4 numbers)")
    try:
        numbers = [float(part) for part in parts]
    except ValueError as exc:
        raise ValueError("bbox must contain 4 numbers") from exc
    if numbers[0] >= numbers[2] or numbers[1] >= numbers[3]:
        raise ValueError("bbox must be ordered west,south,east,north")
    return ",".join(parts)


def validate_datetime(value: str) -> str:
    value = str(value).strip()
    if not value:
        raise ValueError("datetime must not be empty")
    for part in value.split("/"):
        part = part.strip()
        if not part:
            raise ValueError("datetime range must look like 'START/END'")
        date_part = part.replace("T", " ").split(" ")[0]
        try:
            year, month, day = (int(x) for x in date_part.split("-"))
        except ValueError as exc:
            raise ValueError(
                "datetime must be ISO 8601, e.g. '2024-01-01' or "
                "'2024-01-01/2024-12-31'"
            ) from exc
        if not (1 <= month <= 12 and 1 <= day <= 31 and year > 1000):
            raise ValueError("datetime contains an out-of-range date")
    return value


def search_argv(
    *,
    collection: str,
    aoi: str | None,
    bbox: str | None,
    datetime_range: str | None,
    limit: int,
    output: str | None,
    filter_text: str | None,
    env: str,
    anonymous: bool,
) -> list[str]:
    argv = ["search", "--collection", collection]
    if aoi:
        argv += ["--aoi", str(aoi)]
    if bbox:
        argv += ["--bbox", bbox]
    if datetime_range:
        argv += ["--datetime", datetime_range]
    if filter_text:
        argv += ["--filter", filter_text]
    if limit is not None:
        argv += ["--limit", str(limit)]
    if output:
        argv += ["--output", str(output)]
    argv += ["--env", env]
    if anonymous:
        argv.append("--anonymous")
    return argv


def download_argv(
    *,
    collection: str,
    scenes_path: str | Path | None,
    uuids: list[str],
    output_dir: str,
    env: str,
    limit: int,
) -> list[str]:
    argv = ["download", "--collection", collection]
    if scenes_path:
        argv += ["--input", str(scenes_path)]
    else:
        argv += ["--uuid", ",".join(uuids)]
    argv += ["--dl_dir", str(output_dir)]
    if limit is not None:
        argv += ["--limit", str(limit)]
    argv += ["--env", env]
    return argv
