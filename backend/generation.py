"""Resolving the configured bundle root, including the producer's pointer.

The producer publishes a finished bundle by renaming it into a generation
directory and then swapping a single relative symlink at the configured root,
so a reader resolves either the old generation or the new one and can never
observe a half-replaced bundle.

That is a good publication scheme and it is supported here, but only for that
exact shape. Support is deliberately narrow:

* only the **configured** root may be a pointer; nothing else is followed;
* the target must be a **relative single basename** - no separators, no ``..``,
  not absolute - so it cannot address anything outside the resolved parent;
* the target must match the producer's generation pattern
  ``.<root name>.gen-<suffix>``;
* the target must itself be a **real directory**, not a symlink, so a chained
  or nested pointer cannot be used to walk somewhere else;
* everything else - arbitrary external targets, dangling links, links that
  escape the parent, wrong names - is refused.

Ordinary direct bundle directories are unaffected and never followed.
"""

from __future__ import annotations

import os
import re
import stat as stat_module
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

#: Separators and traversal that may never appear in a pointer target.
_FORBIDDEN = ("/", os.sep, "\\")


class GenerationRejected(ValueError):
    """The configured root is a pointer this reader will not follow.

    The message names the contract rule that failed and never includes a
    filesystem path or the raw link target.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


#: Identity of a directory: ``(device, inode, entries)`` where entries is the
#: ``(name, size, mtime_ns, inode, mode)`` of everything directly inside it.
#:
#: The inode alone is not enough. Deleting a directory and recreating one at the
#: same path can hand back the same inode, and on this filesystem even
#: ``st_ctime_ns`` repeated across a delete-and-recreate in the same tick, so
#: neither identifies the prune-and-republish case on its own. Folding in the
#: entries' own size, mtime and inode does, because a recreated directory gets
#: freshly written files. It also means a generation modified underneath a pinned
#: snapshot - an entry added, removed, renamed or rewritten - is refused, which
#: is the safe direction to fail.
RootIdentity = tuple[int, int, tuple[tuple[Any, ...], ...]]


@dataclass(frozen=True)
class ResolvedRoot:
    """Where the bundle for one snapshot actually lives."""

    path: Path
    is_pointer: bool
    generation: str | None = None
    identity: RootIdentity | None = None


def directory_entries(path: Path) -> tuple[tuple[Any, ...], ...] | None:
    """Cheap fingerprint of a directory's direct contents, or None if unreadable."""
    try:
        entries = []
        for entry in sorted(path.iterdir(), key=lambda item: item.name):
            try:
                info = entry.lstat()
            except OSError:
                entries.append((entry.name, "unreadable"))
                continue
            entries.append(
                (
                    entry.name,
                    info.st_size,
                    info.st_mtime_ns,
                    info.st_ino,
                    info.st_mode,
                )
            )
        return tuple(entries)
    except OSError:
        return None


def root_identity(path: Path) -> RootIdentity | None:
    """The directory identity used to pin a snapshot, or None if unreadable."""
    try:
        info = os.lstat(path)
    except OSError:
        return None
    entries = directory_entries(path)
    if entries is None:
        return None
    return info.st_dev, info.st_ino, entries


def check_pinned_root(path: Path, expected: RootIdentity | None) -> None:
    """Refuse to use ``path`` unless it is still the directory we validated.

    Cheap: one ``lstat``. Without it a generation directory that was renamed
    away, deleted, or replaced - including replaced by a symlink to somewhere
    outside - would be followed, and the request would serve bytes that never
    belonged to the pinned snapshot. Raises ``OSError`` so the caller answers a
    controlled 503.
    """
    try:
        info = os.lstat(path)
    except OSError:
        raise OSError("the pinned generation is no longer present") from None
    if stat_module.S_ISLNK(info.st_mode):
        raise OSError("the pinned generation was replaced by a symlink")
    if not stat_module.S_ISDIR(info.st_mode):
        raise OSError("the pinned generation is no longer a directory")
    if expected is not None:
        entries = directory_entries(path)
        if (info.st_dev, info.st_ino, entries) != tuple(expected):
            raise OSError(
                "the pinned generation was replaced or modified since it was validated"
            )


def generation_pattern(root_name: str) -> re.Pattern[str]:
    """The producer's generation naming: ``.<root name>.gen-<suffix>``."""
    return re.compile(rf"^\.{re.escape(root_name)}\.gen-[A-Za-z0-9][A-Za-z0-9._-]*$")


def resolve_bundle_root(configured: Path) -> ResolvedRoot | None:
    """Resolve ``configured`` to the directory holding the bundle.

    Returns ``None`` when the configured root does not exist at all, which is
    the awaiting-analysis state. A pointer that exists but is not acceptable is
    a :class:`GenerationRejected`.
    """
    configured = Path(configured)
    try:
        info = configured.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        raise GenerationRejected("the bundle root could not be inspected") from None

    if not stat_module.S_ISLNK(info.st_mode):
        if not stat_module.S_ISDIR(info.st_mode):
            raise GenerationRejected("the bundle root is not a directory")
        return ResolvedRoot(
            path=configured,
            is_pointer=False,
            identity=root_identity(configured),
        )

    return _resolve_pointer(configured)


def _resolve_pointer(configured: Path) -> ResolvedRoot:
    try:
        target = os.readlink(configured)
    except OSError:
        raise GenerationRejected("the bundle root pointer could not be read") from None
    if not target:
        raise GenerationRejected("the bundle root pointer is empty")
    if os.path.isabs(target):
        raise GenerationRejected("the bundle root pointer must be relative")
    if any(sep in target for sep in _FORBIDDEN) or os.sep in target:
        raise GenerationRejected("the bundle root pointer must be a single file name")
    if target in (".", ".."):
        raise GenerationRejected("the bundle root pointer must name a generation")
    if PurePosixPath(target).name != target:
        raise GenerationRejected("the bundle root pointer must be a single file name")
    if not generation_pattern(configured.name).match(target):
        raise GenerationRejected(
            "the bundle root pointer must name a generation directory"
        )

    parent = configured.parent
    try:
        parent_info = parent.lstat()
    except OSError:
        raise GenerationRejected("the bundle root parent could not be inspected") from None
    if not stat_module.S_ISDIR(parent_info.st_mode):
        raise GenerationRejected("the bundle root parent is not a directory")

    generation = parent / target
    try:
        generation_info = generation.lstat()
    except FileNotFoundError:
        raise GenerationRejected("the bundle root pointer does not resolve") from None
    except OSError:
        raise GenerationRejected("the bundle root pointer could not be inspected") from None
    if stat_module.S_ISLNK(generation_info.st_mode):
        raise GenerationRejected("the bundle root pointer must not point at a symlink")
    if not stat_module.S_ISDIR(generation_info.st_mode):
        raise GenerationRejected("the bundle root pointer does not name a directory")
    if generation.parent != parent or generation.name != target:
        raise GenerationRejected("the bundle root pointer escapes its parent")
    return ResolvedRoot(
        path=generation,
        is_pointer=True,
        generation=target,
        identity=root_identity(generation),
    )


__all__ = [
    "GenerationRejected",
    "ResolvedRoot",
    "RootIdentity",
    "check_pinned_root",
    "directory_entries",
    "generation_pattern",
    "resolve_bundle_root",
    "root_identity",
]