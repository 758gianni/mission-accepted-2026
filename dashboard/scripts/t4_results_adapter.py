#!/usr/bin/env python3
"""Adapt RADARSAT-2 4-date pipeline output into dashboard T4 validation results.

The 4-date change-detection pipeline (``pipeline_4date.py``) writes its derived
per-region analysis as JSON/GeoJSON files into a directory.  The dashboard
frontend data contract is assembled by ``build_contract.py --t4-results FILE``,
which expects a single JSON document shaped like::

    {
      "regions": {
        "<region_id>": {
          "t4_status": "confirmed" | "weakened" | "outside_swath" | "pending",
          "t4_mean_power": number | null,
          "t4_mean_signed_db": number | null,
          "t4_observation": string | null
        }
      },
      "acquisition": {
        "id": "T4",
        "date": "YYYY-MM-DD",
        "iso": "...",
        "beam": "XF0W2",
        "polarization": "HH",
        "orbit": "Ascending",
        "role": "..."
      }
    }

When ``t4_status`` is ``confirmed`` for a candidate region whose ``class_id`` is
``1`` (persistent), ``build_contract.py`` reclassifies that feature to
``temporal_class`` ``"t4_validated_persistent"``.  This adapter therefore only
has to translate the pipeline's own field naming into the contract's naming --
it never makes the validation decision itself.

Scope: this script only ever reads *derived* analysis artifacts (JSON, GeoJSON,
metrics).  It must never be pointed at raw SAR archives.

Usage::

    python3 dashboard/scripts/t4_results_adapter.py \\
        --pipeline-output data/derived/exploration_4date \\
        --out build/contract/t4_results.json

    python3 dashboard/scripts/t4_results_adapter.py --dry-run

Exit codes: ``0`` success (including dry run), ``2`` unusable pipeline input,
``3`` bad command line.

Because the pipeline's exact field names are only known at integration time,
region records are located through alias tables (:data:`_ID_KEYS`,
:data:`_STATUS_KEYS`, ...).  Anything that cannot be recognised is reported
rather than guessed.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, Final

__all__ = [
    "AdapterError",
    "PipelineOutputError",
    "build_t4_results",
    "main",
    "summarize_results",
    "write_results",
]

DEFAULT_PIPELINE_OUTPUT: Final[Path] = Path(
    "/home/overlord/hackathon/rs2-analysis/data/derived/exploration_4date"
)
DEFAULT_FALLBACK_STATUS: Final[str] = "outside_swath"

STATUS_CONFIRMED: Final[str] = "confirmed"
STATUS_WEAKENED: Final[str] = "weakened"
STATUS_OUTSIDE_SWATH: Final[str] = "outside_swath"
STATUS_PENDING: Final[str] = "pending"

T4_STATUSES: Final[tuple[str, ...]] = (
    STATUS_CONFIRMED,
    STATUS_WEAKENED,
    STATUS_OUTSIDE_SWATH,
    STATUS_PENDING,
)

REGION_FIELDS: Final[tuple[str, ...]] = (
    "t4_status",
    "t4_mean_power",
    "t4_mean_signed_db",
    "t4_observation",
)

DEFAULT_ACQUISITION: Final[dict[str, str]] = {
    "id": "T4",
    "date": "",
    "iso": "",
    "beam": "XF0W2",
    "polarization": "HH",
    "orbit": "Ascending",
    "role": (
        "T4 validation acquisition: fourth date used to test whether a detected "
        "change persists, so confirmed persistent regions can be reclassified as "
        "t4_validated_persistent."
    ),
}

# --- field alias tables -----------------------------------------------------
# T4-specific spellings come first so an explicit T4 field always wins over a
# bare per-date field that happens to sit next to it.
_ID_KEYS: Final[tuple[str, ...]] = (
    "region_id",
    "regionId",
    "candidate_id",
    "feature_id",
    "region",
    "id",
    "name",
)
_FEATURE_ID_KEYS: Final[tuple[str, ...]] = ("id", "feature_id") + _ID_KEYS
_STATUS_KEYS: Final[tuple[str, ...]] = (
    "t4_status",
    "change_survives_t4",
    "survives_t4",
    "t4_survives",
    "survives",
    "validation_status",
    "t4_validation",
    "status",
)
_COVERAGE_KEYS: Final[tuple[str, ...]] = (
    "within_swath",
    "swath_covered",
    "t4_covered",
    "covered",
    "in_swath",
    "has_coverage",
    "coverage",
)
_POWER_KEYS: Final[tuple[str, ...]] = (
    "t4_mean_power",
    "mean_power_t4",
    "mean_power",
    "t4_power",
    "power_t4",
)
_DB_KEYS: Final[tuple[str, ...]] = (
    "t4_mean_signed_db",
    "mean_signed_db_t4",
    "t4_signed_db",
    "signed_db_t4",
    "mean_signed_db",
    "signed_db",
    "mean_delta_db_t4",
    "delta_db_t4",
    "t4_delta_db",
    "mean_delta_db",
    "delta_db",
)
_OBSERVATION_KEYS: Final[tuple[str, ...]] = (
    "t4_observation",
    "observation_t4",
    "observation",
    "observation_note",
    "note",
    "notes",
    "comment",
)

# --- status vocabulary ------------------------------------------------------
_CONFIRMED_TOKENS: Final[frozenset[str]] = frozenset(
    {
        "confirmed",
        "confirm",
        "confirmed_persistent",
        "validated",
        "validated_persistent",
        "t4_validated_persistent",
        "persistent",
        "survives",
        "survived",
        "survives_t4",
        "true",
        "yes",
        "pass",
        "passed",
        "strong",
    }
)
_WEAKENED_TOKENS: Final[frozenset[str]] = frozenset(
    {
        "weakened",
        "weaken",
        "weakened_t4",
        "attenuated",
        "weaker",
        "faded",
        "decayed",
        "marginal",
        "partial",
        "false",
        "no",
        "fail",
        "failed",
        "rejected",
        "not_confirmed",
        "unconfirmed",
    }
)
_OUTSIDE_SWATH_TOKENS: Final[frozenset[str]] = frozenset(
    {
        "outside_swath",
        "outside",
        "off_swath",
        "offswath",
        "out_of_swath",
        "not_covered",
        "uncovered",
        "no_coverage",
        "nocoverage",
        "offscene",
        "off_scene",
    }
)
_PENDING_TOKENS: Final[frozenset[str]] = frozenset(
    {
        "pending",
        "unknown",
        "undetermined",
        "inconclusive",
        "unevaluated",
        "not_evaluated",
        "none",
        "null",
        "n/a",
        "na",
        "",
    }
)

# --- acquisition aliases ----------------------------------------------------
_ACQ_BEAM_KEYS: Final[tuple[str, ...]] = ("beam", "beam_mode", "beammode", "mode")
_ACQ_POL_KEYS: Final[tuple[str, ...]] = (
    "polarization",
    "polarisation",
    "pol",
    "polarization_mode",
)
_ACQ_ORBIT_KEYS: Final[tuple[str, ...]] = (
    "orbit",
    "orbit_direction",
    "orbitType",
    "direction",
    "ascending_descending",
)
_ACQ_DATE_KEYS: Final[tuple[str, ...]] = ("date", "acquisition_date", "acq_date", "day")
_ACQ_ISO_KEYS: Final[tuple[str, ...]] = ("iso", "iso_timestamp", "timestamp", "datetime")
_ACQ_ROLE_KEYS: Final[tuple[str, ...]] = ("role", "purpose")


class AdapterError(Exception):
    """Base class for adapter failures that should not show a traceback."""


class PipelineOutputError(AdapterError):
    """The pipeline output directory is missing or holds no usable regions."""


# --- small helpers ----------------------------------------------------------
def _first_key(mapping: Mapping[str, Any], keys: Sequence[str]) -> tuple[str, Any] | None:
    """Return the first ``(key, value)`` in ``keys`` present with a non-None value."""
    for key in keys:
        if key in mapping:
            value = mapping[key]
            if value is not None:
                return key, value
    return None


def _coerce_number(value: Any) -> int | float | None:
    """Coerce a JSON scalar to a finite number, or ``None``."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else number


def _coerce_text(value: Any) -> str | None:
    """Coerce a JSON scalar to a non-empty string, or ``None``."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (str, int, float)):
        text = str(value).strip()
    else:
        return None
    return text or None


def normalize_status(value: Any) -> str | None:
    """Map an arbitrary pipeline status onto one of :data:`T4_STATUSES`.

    Booleans are read as a survival verdict, which is how a pipeline typically
    records ``change_survives_t4``: ``True`` means the change held, ``False``
    means it did not.  An empty or blank token means "not evaluated" and maps to
    ``pending``.  Returns ``None`` for a non-scalar value or a token outside
    every vocabulary, so callers can tell "absent/unrecognised" apart from an
    explicit ``pending`` rather than guessing.
    """
    if isinstance(value, bool):
        return STATUS_CONFIRMED if value else STATUS_WEAKENED
    if value is None or not isinstance(value, (str, int, float)):
        return None
    token = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    if token in _CONFIRMED_TOKENS:
        return STATUS_CONFIRMED
    if token in _WEAKENED_TOKENS:
        return STATUS_WEAKENED
    if token in _OUTSIDE_SWATH_TOKENS:
        return STATUS_OUTSIDE_SWATH
    if token in _PENDING_TOKENS:
        return STATUS_PENDING
    return None


def _looks_like_region(mapping: Mapping[str, Any]) -> bool:
    """True when ``mapping`` carries at least one T4/region-ish field."""
    keys = set(mapping)
    for group in (
        _STATUS_KEYS,
        _COVERAGE_KEYS,
        _POWER_KEYS,
        _DB_KEYS,
        _OBSERVATION_KEYS,
        _ID_KEYS,
        ("region_id", "regionId", "candidate_id"),
    ):
        if keys.intersection(group):
            return True
    return False


def _id_containers(mapping: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Ordered mappings to search for a region id (record level, then properties)."""
    containers = [mapping]
    properties = mapping.get("properties")
    if isinstance(properties, Mapping):
        containers.append(properties)
    return containers


def resolve_region_id(
    mapping: Mapping[str, Any], *, explicit_id: str | None = None
) -> str | None:
    """Resolve a stable region id from a record or a GeoJSON feature."""
    if explicit_id and explicit_id.strip():
        return explicit_id.strip()
    for position, container in enumerate(_id_containers(mapping)):
        keys = _FEATURE_ID_KEYS if position == 0 else _ID_KEYS
        found = _first_key(container, keys)
        if found is None:
            continue
        text = _coerce_text(found[1])
        if text is not None:
            return text
    return None


# --- region record extraction ----------------------------------------------
class _RegionAccumulator:
    """Collects the best value seen so far for each region id."""

    __slots__ = ("status", "mean_power", "mean_signed_db", "observation", "outside_swath")

    def __init__(self) -> None:
        self.status: str | None = None
        self.mean_power: int | float | None = None
        self.mean_signed_db: int | float | None = None
        self.observation: str | None = None
        self.outside_swath: bool = False

    def absorb(self, mapping: Mapping[str, Any]) -> None:
        coverage = _first_key(mapping, _COVERAGE_KEYS)
        if coverage is not None:
            flag = coverage[1]
            if isinstance(flag, bool):
                if not flag:
                    self.outside_swath = True
            elif isinstance(flag, str):
                token = flag.strip().lower().replace("-", "_").replace(" ", "_")
                if token in _OUTSIDE_SWATH_TOKENS or token in {"false", "no", "0"}:
                    self.outside_swath = True
                elif token in {"true", "yes", "1"}:
                    self.outside_swath = False

        status_field = _first_key(mapping, _STATUS_KEYS)
        if status_field is not None:
            status = normalize_status(status_field[1])
            if status is not None and status != STATUS_PENDING and self.status is None:
                self.status = status
            elif status == STATUS_PENDING and self.status is None:
                self.status = STATUS_PENDING
            if status == STATUS_OUTSIDE_SWATH:
                self.outside_swath = True

        if self.mean_power is None:
            power = _first_key(mapping, _POWER_KEYS)
            if power is not None:
                self.mean_power = _coerce_number(power[1])
        if self.mean_signed_db is None:
            signed_db = _first_key(mapping, _DB_KEYS)
            if signed_db is not None:
                self.mean_signed_db = _coerce_number(signed_db[1])
        if self.observation is None:
            observation = _first_key(mapping, _OBSERVATION_KEYS)
            if observation is not None:
                self.observation = _coerce_text(observation[1])


def _iter_region_sources(
    document: Any,
) -> Iterator[tuple[str | None, Mapping[str, Any]]]:
    """Yield ``(explicit_id_or_None, region_record)`` pairs from one JSON document.

    Handles GeoJSON feature collections, ``{"regions": {...}}`` summaries,
    ``{"regions": [...]}`` summaries, bare lists of records, and a mapping of
    region id to record.
    """
    if isinstance(document, list):
        for item in document:
            if isinstance(item, Mapping):
                yield None, item
        return

    if not isinstance(document, Mapping):
        return

    features = document.get("features")
    if isinstance(features, list):
        for feature in features:
            if isinstance(feature, Mapping):
                yield None, feature
        return

    regions = document.get("regions")
    if isinstance(regions, Mapping):
        for key, value in regions.items():
            if isinstance(value, Mapping):
                yield str(key), value
        return
    if isinstance(regions, list):
        for value in regions:
            if isinstance(value, Mapping):
                yield None, value
        return

    if _looks_like_region(document):
        yield None, document
        return

    # Last resort: a bare {region_id: record} mapping, only when the records
    # themselves carry region fields (so date tables are not mistaken for one).
    values = [v for v in document.values() if isinstance(v, Mapping)]
    if values and len(values) == len(document):
        if any(_looks_like_region(value) for value in values):
            for value in values:
                yield None, value


def _iter_json_documents(root: Path) -> Iterator[Path]:
    """Yield JSON/GeoJSON files under ``root`` in a stable order."""
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".json", ".geojson"}:
            continue
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        yield path


def _load_document(path: Path, warnings: list[str]) -> Any | None:
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, UnicodeDecodeError) as exc:
        warnings.append(f"skipped unreadable {path}: {exc}")
        return None
    except json.JSONDecodeError as exc:
        warnings.append(f"skipped malformed JSON {path}: {exc}")
        return None


def _collect_acquisition(
    document: Any, acquisition: dict[str, str]
) -> None:
    """Fill unset acquisition fields from a pipeline document, in place.

    First document to supply a field wins, so the resolution order in
    :func:`build_t4_results` decides precedence.
    """
    candidates: list[Mapping[str, Any]] = []
    if isinstance(document, Mapping):
        nested = document.get("acquisition") or document.get("acq")
        if isinstance(nested, Mapping):
            candidates.append(nested)
        candidates.append(document)
    elif isinstance(document, list):
        candidates.extend(item for item in document if isinstance(item, Mapping))

    for mapping in candidates:
        for field, keys in (
            ("date", _ACQ_DATE_KEYS),
            ("iso", _ACQ_ISO_KEYS),
            ("beam", _ACQ_BEAM_KEYS),
            ("polarization", _ACQ_POL_KEYS),
            ("orbit", _ACQ_ORBIT_KEYS),
            ("role", _ACQ_ROLE_KEYS),
        ):
            if acquisition.get(field):
                continue
            found = _first_key(mapping, keys)
            if found is None:
                continue
            text = _coerce_text(found[1])
            if text is not None:
                acquisition[field] = text


def _resolve_region_status(accumulator: _RegionAccumulator, fallback: str) -> str:
    """Pick a region status, preferring an explicit pipeline evaluation.

    A definite evaluation the pipeline made (``confirmed``/``weakened``/
    ``outside_swath``) always wins.  Regions the T4 swath does not cover, and
    regions the pipeline never evaluated, get ``fallback``.
    """
    status = accumulator.status
    if status == STATUS_CONFIRMED or status == STATUS_WEAKENED:
        return status
    if status == STATUS_OUTSIDE_SWATH:
        return STATUS_OUTSIDE_SWATH
    if accumulator.outside_swath:
        return fallback
    if status == STATUS_PENDING:
        return STATUS_PENDING
    return fallback


def build_t4_results(
    pipeline_output: Path,
    *,
    fallback_status: str = DEFAULT_FALLBACK_STATUS,
    roster: Path | None = None,
    acquisition_overrides: Mapping[str, str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Build the ``t4_results`` document from a pipeline output directory.

    Args:
        pipeline_output: Directory holding the 4-date pipeline's derived
            JSON/GeoJSON output.
        fallback_status: Status assigned to regions the T4 swath does not
            cover, or that carry no T4 evaluation at all.
        roster: Optional JSON/GeoJSON listing every candidate region, so regions
            with no T4 coverage still appear in the output.
        acquisition_overrides: Explicit acquisition field values that win over
            anything found in the pipeline output.
        warnings: Optional sink for non-fatal problems (an unreadable or
            malformed file in the pipeline directory).  Skipped files are
            reported here rather than silently dropped.  They are deliberately
            kept out of the returned document so it matches the contract shape
            exactly.

    Returns:
        A JSON-serialisable ``{"regions": {...}, "acquisition": {...}}`` dict.

    Raises:
        PipelineOutputError: If the pipeline output (or roster) is missing, or
            if no region records could be found at all.
        AdapterError: If ``fallback_status`` is not a contract status.
    """
    if fallback_status not in T4_STATUSES:
        raise AdapterError(
            f"invalid fallback status {fallback_status!r}; expected one of "
            f"{', '.join(T4_STATUSES)}"
        )

    pipeline_output = Path(pipeline_output)
    if not pipeline_output.exists():
        raise PipelineOutputError(
            f"pipeline output directory not found: {pipeline_output}. Point "
            "--pipeline-output at the directory pipeline_4date.py writes to."
        )
    if not pipeline_output.is_dir():
        raise PipelineOutputError(
            f"pipeline output path is not a directory: {pipeline_output}"
        )

    warnings = warnings if warnings is not None else []
    documents: list[tuple[Path, Any]] = []
    for path in _iter_json_documents(pipeline_output):
        document = _load_document(path, warnings)
        if document is not None:
            documents.append((path, document))

    roster_ids: list[str] = []
    roster_documents: list[tuple[Path, Any]] = []
    if roster is not None:
        roster_path = Path(roster)
        if not roster_path.exists():
            raise PipelineOutputError(f"roster file not found: {roster_path}")
        document = _load_document(roster_path, warnings)
        if document is None:
            raise PipelineOutputError(f"roster file is not readable JSON: {roster_path}")
        roster_documents.append((roster_path, document))

    accumulators: dict[str, _RegionAccumulator] = {}
    order: list[str] = []

    def _record(region_id: str | None, mapping: Mapping[str, Any]) -> None:
        resolved = resolve_region_id(mapping, explicit_id=region_id)
        if resolved is None:
            if not _looks_like_region(mapping):
                return
            resolved = f"region-{len(order) + 1:04d}"
        accumulator = accumulators.get(resolved)
        if accumulator is None:
            accumulator = _RegionAccumulator()
            accumulators[resolved] = accumulator
            order.append(resolved)
        accumulator.absorb(mapping)

    for path, document in roster_documents:
        for region_id, mapping in _iter_region_sources(document):
            _record(region_id, mapping)

    for path, document in documents:
        for region_id, mapping in _iter_region_sources(document):
            _record(region_id, mapping)

    if not order:
        raise PipelineOutputError(
            f"no region records found in {pipeline_output}"
            + (" or its roster" if roster is not None else "")
            + ". Expected the 4-date pipeline's regions summary or candidate "
            "region GeoJSON."
        )

    # Precedence: pipeline metadata first, then the built-in defaults for
    # anything the pipeline did not describe, then explicit CLI overrides.
    acquisition: dict[str, str] = {}
    for _path, document in documents:
        _collect_acquisition(document, acquisition)
    for key, value in DEFAULT_ACQUISITION.items():
        if not acquisition.get(key):
            acquisition[key] = value
    acquisition["id"] = DEFAULT_ACQUISITION["id"]
    for key, value in (acquisition_overrides or {}).items():
        text = _coerce_text(value)
        if text is not None:
            acquisition[key] = text
    # Emit the contract's field order, and never omit a key.
    acquisition = {key: acquisition.get(key, "") for key in DEFAULT_ACQUISITION}

    regions: dict[str, dict[str, Any]] = {}
    for region_id in order:
        accumulator = accumulators[region_id]
        regions[region_id] = {
            "t4_status": _resolve_region_status(accumulator, fallback_status),
            "t4_mean_power": accumulator.mean_power,
            "t4_mean_signed_db": accumulator.mean_signed_db,
            "t4_observation": accumulator.observation,
        }

    return {"regions": regions, "acquisition": acquisition}


def validate_results(results: Mapping[str, Any]) -> list[str]:
    """Return a list of contract-shape problems; empty means the document is valid."""
    problems: list[str] = []
    regions = results.get("regions")
    if not isinstance(regions, Mapping):
        problems.append("'regions' must be an object keyed by region id")
        return problems
    for region_id, record in regions.items():
        if not isinstance(record, Mapping):
            problems.append(f"{region_id}: region record must be an object")
            continue
        if set(record) != set(REGION_FIELDS):
            problems.append(
                f"{region_id}: fields {sorted(record)} != {sorted(REGION_FIELDS)}"
            )
        status = record.get("t4_status")
        if status not in T4_STATUSES:
            problems.append(f"{region_id}: invalid t4_status {status!r}")
        for field in ("t4_mean_power", "t4_mean_signed_db"):
            value = record.get(field)
            if value is not None and not isinstance(value, (int, float)):
                problems.append(f"{region_id}: {field} must be a number or null")
        observation = record.get("t4_observation")
        if observation is not None and not isinstance(observation, str):
            problems.append(f"{region_id}: t4_observation must be a string or null")
    acquisition = results.get("acquisition")
    if not isinstance(acquisition, Mapping):
        problems.append("'acquisition' must be an object")
    return problems


def summarize_results(
    results: Mapping[str, Any], *, out: Path | None = None, dry_run: bool = False
) -> str:
    """Render a human-readable summary of an adapted results document."""
    regions: Mapping[str, Any] = results.get("regions", {})  # type: ignore[assignment]
    counts = {status: 0 for status in T4_STATUSES}
    for record in regions.values():
        status = record.get("t4_status") if isinstance(record, Mapping) else None
        if status in counts:
            counts[status] += 1
    acquisition = results.get("acquisition", {})
    lines = [
        f"T4 results: {len(regions)} region(s)",
        "  " + "  ".join(f"{status}={counts[status]}" for status in T4_STATUSES),
        "  acquisition: "
        + ", ".join(
            f"{key}={acquisition.get(key)}"  # type: ignore[union-attr]
            for key in ("id", "date", "beam", "polarization", "orbit")
        ),
    ]
    confirmed = [
        region_id
        for region_id, record in regions.items()
        if isinstance(record, Mapping) and record.get("t4_status") == STATUS_CONFIRMED
    ]
    if confirmed:
        shown = ", ".join(confirmed[:5])
        more = "" if len(confirmed) <= 5 else f" (+{len(confirmed) - 5} more)"
        lines.append(f"  t4_validated_persistent candidates: {shown}{more}")
    if dry_run:
        lines.append("  dry run: nothing written")
    elif out is not None:
        lines.append(f"  wrote {out}")
    return "\n".join(lines)


def write_results(results: Mapping[str, Any], out: Path) -> Path:
    """Write ``results`` to ``out`` as pretty-printed JSON, creating parent dirs."""
    out = Path(out)
    problems = validate_results(results)
    if problems:
        raise AdapterError(
            "refusing to write a contract-shaped document with problems:\n  "
            + "\n  ".join(problems)
        )
    if out.parent and not out.parent.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        json.dump(results, handle, indent=2, sort_keys=False)
        handle.write("\n")
    return out


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="t4_results_adapter",
        description=(
            "Adapt pipeline_4date output into the dashboard's t4_results.json "
            "contract shape. Reads derived analysis artifacts only."
        ),
    )
    parser.add_argument(
        "--pipeline-output",
        type=Path,
        default=DEFAULT_PIPELINE_OUTPUT,
        metavar="DIR",
        help="directory of derived pipeline JSON/GeoJSON output "
        f"(default: {DEFAULT_PIPELINE_OUTPUT})",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        metavar="FILE",
        help="path for the t4_results.json to write (required unless --dry-run)",
    )
    parser.add_argument(
        "--roster",
        type=Path,
        default=None,
        metavar="FILE",
        help="optional JSON/GeoJSON listing every candidate region, so regions "
        "with no T4 coverage still appear in the output",
    )
    parser.add_argument(
        "--fallback-status",
        choices=T4_STATUSES,
        default=DEFAULT_FALLBACK_STATUS,
        help="status for regions the T4 swath does not cover "
        f"(default: {DEFAULT_FALLBACK_STATUS})",
    )
    parser.add_argument(
        "--acquisition-date",
        default=None,
        metavar="YYYY-MM-DD",
        help="override the T4 acquisition date",
    )
    parser.add_argument(
        "--acquisition-iso",
        default=None,
        metavar="ISO",
        help="override the T4 acquisition timestamp",
    )
    parser.add_argument(
        "--acquisition-beam",
        default=None,
        metavar="BEAM",
        help="override the T4 beam mode (default: XF0W2)",
    )
    parser.add_argument(
        "--acquisition-polarization",
        default=None,
        metavar="POL",
        help="override the T4 polarization (default: HH)",
    )
    parser.add_argument(
        "--acquisition-orbit",
        default=None,
        metavar="ORBIT",
        help="override the T4 orbit direction (default: Ascending)",
    )
    parser.add_argument(
        "--acquisition-role",
        default=None,
        metavar="TEXT",
        help="override the T4 role description",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the summary without writing anything",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.dry_run and args.out is None:
        parser.error("--out is required unless --dry-run is given")

    overrides = {
        "date": args.acquisition_date,
        "iso": args.acquisition_iso,
        "beam": args.acquisition_beam,
        "polarization": args.acquisition_polarization,
        "orbit": args.acquisition_orbit,
        "role": args.acquisition_role,
    }

    warnings: list[str] = []
    try:
        results = build_t4_results(
            args.pipeline_output,
            fallback_status=args.fallback_status,
            roster=args.roster,
            acquisition_overrides={
                key: value for key, value in overrides.items() if value is not None
            },
            warnings=warnings,
        )
    except AdapterError as exc:
        print(f"t4_results_adapter: error: {exc}", file=sys.stderr)
        return 2

    for warning in warnings:
        print(f"t4_results_adapter: warning: {warning}", file=sys.stderr)

    if args.dry_run:
        print(summarize_results(results, dry_run=True))
        return 0

    try:
        out = write_results(results, args.out)
    except AdapterError as exc:
        print(f"t4_results_adapter: error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"t4_results_adapter: error: could not write {args.out}: {exc}", file=sys.stderr)
        return 2

    print(summarize_results(results, out=out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())