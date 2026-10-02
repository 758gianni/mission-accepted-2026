"""Loading and caching of the on-disk result bundle.

Three states are distinguished, because the frontend needs to tell them
apart:

``awaiting_analysis``
    No bundle has been produced yet. Data endpoints answer ``404``.
``ready``
    A bundle exists and satisfies the contract. Data endpoints answer ``200``.
``error``
    A bundle exists but is malformed, incomplete or inconsistent. Data
    endpoints answer ``503`` so a client can distinguish "not produced yet"
    from "produced but broken" without any fabricated values.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .validation import (
    ANALYSIS_FILENAME,
    REGIONS_FILENAME,
    BundleValidationError,
    ValidatedBundle,
    build_validated_bundle,
)

BundleState = Literal["awaiting_analysis", "ready", "error"]

AWAITING_MESSAGE = (
    "No analysis bundle has been produced yet. Waiting for the first analysis run."
)
ERROR_MESSAGE = (
    "The analysis bundle is present but does not satisfy the result bundle contract, "
    "so no values are served."
)


@dataclass(frozen=True)
class BundleSnapshot:
    """Immutable view of the bundle at one point in time."""

    state: BundleState
    message: str
    analysis_id: str | None = None
    scene_count: int = 0
    bundle: ValidatedBundle | None = None

    @property
    def is_ready(self) -> bool:
        return self.state == "ready"


def _read_json(path: Path, label: str) -> Any:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise BundleValidationError(label, "required file is missing") from None
    except (OSError, UnicodeDecodeError):
        raise BundleValidationError(label, "required file could not be read") from None
    try:
        return json.loads(raw, parse_constant=_reject_constant)
    except ValueError:
        raise BundleValidationError(label, "is not valid JSON") from None


def _reject_constant(name: str) -> Any:
    raise ValueError(f"non-finite JSON constant: {name}")


def load_bundle(bundle_dir: Path) -> BundleSnapshot:
    """Load and validate the bundle at ``bundle_dir``.

    Never raises for bundle problems: problems are reported as an ``error``
    snapshot with a sanitized message.
    """
    try:
        return _load(bundle_dir)
    except BundleValidationError as error:
        detail = f" ({error.label})" if error.label else ""
        return BundleSnapshot(state="error", message=f"{ERROR_MESSAGE}{detail}")


def _load(bundle_dir: Path) -> BundleSnapshot:
    if bundle_dir.is_symlink():
        raise BundleValidationError("bundle", "bundle directory must not be a symbolic link")
    if not bundle_dir.exists():
        return BundleSnapshot(state="awaiting_analysis", message=AWAITING_MESSAGE)
    if not bundle_dir.is_dir():
        raise BundleValidationError("bundle", "bundle path is not a directory")

    analysis_path = bundle_dir / ANALYSIS_FILENAME
    regions_path = bundle_dir / REGIONS_FILENAME
    if not analysis_path.exists() and not regions_path.exists():
        # The directory exists but nothing has been published into it yet.
        return BundleSnapshot(state="awaiting_analysis", message=AWAITING_MESSAGE)

    analysis_document = _read_json(analysis_path, ANALYSIS_FILENAME)
    regions_document = _read_json(regions_path, REGIONS_FILENAME)
    validated = build_validated_bundle(analysis_document, regions_document, bundle_dir)

    message = "Result bundle is available."
    if validated.warnings:
        message = f"Result bundle is available. {validated.warnings[0]}"
    return BundleSnapshot(
        state="ready",
        message=message,
        analysis_id=str(validated.analysis["analysis_id"]),
        scene_count=validated.scene_count,
        bundle=validated,
    )


class BundleStore:
    """Thread-safe, reloading view of the bundle directory.

    Each snapshot is validated once and cached. The cache is revalidated when
    the directory contents change (per-file size, mtime and inode, or a
    newly appearing/missing directory), which is what allows a new analysis
    run to be picked up without restarting the service.
    """

    def __init__(self, bundle_dir: Path) -> None:
        self._bundle_dir = Path(bundle_dir)
        self._lock = threading.Lock()
        self._snapshot: BundleSnapshot | None = None
        self._signature: tuple[Any, ...] | None = None

    @property
    def bundle_dir(self) -> Path:
        return self._bundle_dir

    def _current_signature(self) -> tuple[Any, ...]:
        directory = self._bundle_dir
        try:
            dir_stat = directory.lstat()
            entries: list[tuple[Any, ...]] = []
            for entry in sorted(directory.iterdir(), key=lambda item: item.name):
                try:
                    entry_stat = entry.lstat()
                except OSError:
                    entries.append((entry.name, "unreadable"))
                    continue
                entries.append(
                    (entry.name, entry_stat.st_mtime_ns, entry_stat.st_size, entry_stat.st_ino)
                )
            directory_signature: tuple[Any, ...] = (
                "present",
                dir_stat.st_mtime_ns,
                tuple(entries),
            )
        except FileNotFoundError:
            directory_signature = ("absent",)
        except OSError:
            directory_signature = ("unreadable",)
        return (str(directory), directory_signature)

    def snapshot(self, *, force: bool = False) -> BundleSnapshot:
        signature = self._current_signature()
        with self._lock:
            if not force and self._snapshot is not None and signature == self._signature:
                return self._snapshot
            snapshot = load_bundle(self._bundle_dir)
            self._snapshot = snapshot
            self._signature = signature
            return snapshot


__all__ = [
    "AWAITING_MESSAGE",
    "BundleSnapshot",
    "BundleState",
    "BundleStore",
    "ERROR_MESSAGE",
    "load_bundle",
]
