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


@dataclass(frozen=True)
class ResolvedRoot:
    """Where the bundle for one snapshot actually lives."""

    path: Path
    is_pointer: bool
    generation: str | None = None


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
        return ResolvedRoot(path=configured, is_pointer=False)

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
    return ResolvedRoot(path=generation, is_pointer=True, generation=target)


__all__ = [
    "GenerationRejected",
    "ResolvedRoot",
    "generation_pattern",
    "resolve_bundle_root",
]