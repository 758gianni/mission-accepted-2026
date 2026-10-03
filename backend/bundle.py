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
import os
import stat as stat_module
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .generation import (
    GenerationRejected,
    ResolvedRoot,
    RootIdentity,
    resolve_bundle_root,
    root_identity,
)
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
    """Immutable view of the bundle at one point in time.

    ``generation_dir`` is pinned when the snapshot was built and is the only
    directory any file for this snapshot is read from, so a pointer swap part
    way through a load can never pair one generation's metadata with another's
    pixels.
    """

    state: BundleState
    message: str
    analysis_id: str | None = None
    scene_count: int = 0
    bundle: ValidatedBundle | None = None
    generation_dir: Path | None = None
    root_identity: RootIdentity | None = None

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
        return json.loads(
            raw, parse_constant=_reject_constant, parse_float=_finite_float
        )
    except ValueError:
        raise BundleValidationError(label, "is not valid JSON") from None


def _reject_constant(name: str) -> Any:
    """Reject the non-standard NaN and Infinity literals."""
    raise ValueError(f"non-finite JSON constant: {name}")


def _finite_float(raw: str) -> float:
    """Refuse a numeric literal that overflows to infinity.

    ``parse_float`` is the only place every float in the document passes
    through, including numbers under unknown extension keys that no field
    validator ever looks at. Without this guard an overflowing literal such as
    ``1e400`` parses to ``inf``, survives validation, and then fails while the
    response is serialised, turning a broken bundle into a 500 instead of a
    reported error.
    """
    value = float(raw)
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError(f"non-finite JSON number: {raw}")
    return value


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
    except GenerationRejected as error:
        return BundleSnapshot(
            state="error",
            message=f"{ERROR_MESSAGE} ({error.reason})",
        )


def _load(bundle_dir: Path) -> BundleSnapshot:
    resolved: ResolvedRoot | None = resolve_bundle_root(bundle_dir)
    if resolved is None:
        return BundleSnapshot(state="awaiting_analysis", message=AWAITING_MESSAGE)

    # Pinned once. Everything below reads from this one directory.
    root = resolved.path
    analysis_path = root / ANALYSIS_FILENAME
    regions_path = root / REGIONS_FILENAME
    if not analysis_path.exists() and not regions_path.exists():
        # The directory exists but nothing has been published into it yet.
        return BundleSnapshot(state="awaiting_analysis", message=AWAITING_MESSAGE)

    analysis_document = _read_json(analysis_path, ANALYSIS_FILENAME)
    regions_document = _read_json(regions_path, REGIONS_FILENAME)
    validated = build_validated_bundle(analysis_document, regions_document, root)

    return BundleSnapshot(
        state="ready",
        message="Result bundle is available.",
        analysis_id=str(validated.analysis["analysis_id"]),
        scene_count=validated.scene_count,
        bundle=validated,
        generation_dir=root,
        root_identity=resolved.identity or root_identity(root),
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
        """Identify the bundle by the pointer and by what the pointer resolves to.

        A pointer swap is a new symlink inode, and the resolved generation is a
        different directory, so both an A->B flip and a B->A flip back are
        noticed even when the names are reused.
        """
        directory = self._bundle_dir
        try:
            root_stat = directory.lstat()
        except FileNotFoundError:
            return (str(directory), "absent")
        except OSError:
            return (str(directory), "unreadable")

        parts: tuple[Any, ...] = (
            "root",
            root_stat.st_mode,
            root_stat.st_ino,
            root_stat.st_mtime_ns,
        )
        if stat_module.S_ISLNK(root_stat.st_mode):
            try:
                parts = parts + ("pointer", os.readlink(directory))
            except OSError:
                return parts + ("pointer-unreadable",)

        try:
            resolved = resolve_bundle_root(directory)
        except GenerationRejected:
            resolved = None
        if resolved is None:
            return parts + ("unresolved",)

        root = resolved.path
        try:
            entries: list[tuple[Any, ...]] = []
            for entry in sorted(root.iterdir(), key=lambda item: item.name):
                try:
                    entry_stat = entry.lstat()
                except OSError:
                    entries.append((entry.name, "unreadable"))
                    continue
                entries.append(
                    (entry.name, entry_stat.st_mtime_ns, entry_stat.st_size,
                     entry_stat.st_ino, entry_stat.st_mode)
                )
            root_stat = root.lstat()
        except OSError:
            return parts + ("gone",)
        return parts + (
            "resolved",
            root.name,
            root_stat.st_ino,
            root_stat.st_mtime_ns,
            tuple(entries),
        )

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
