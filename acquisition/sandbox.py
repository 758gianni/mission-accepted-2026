"""Runtime sandbox for the upstream EODMS CLI.

Upstream behaviours this module exists to neutralise (all verified against
revision 464b94920e7faf28c84a6d31229ef0b2828a1479):

* ``eodms_cli.cli`` group callback calls ``_initialize_cli_logging()``, which
  creates ``log/eodms_cli.log`` **inside the CLI source tree**. Neutralised.
* ``ConfigUtils`` resolves ``~/.eodms/config.ini`` and ``eodms_cli.configure``
  writes a **base64 encoded password** there. ``HOME``/``USERPROFILE`` are
  redirected to a throwaway directory and ``configure`` is removed from the
  Click group for the duration of the call.
* ``eodms.aaa.AAA_API`` writes ``~/.eodms/aaa_creds.<user>.<env>.json``
  containing the access/refresh **tokens**. ``export_vals``/``import_vals`` are
  neutralised so token state is memory-only.

Nothing here ever reads the user's real ``~/.eodms``.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

from . import FORBIDDEN_UPSTREAM_COMMANDS

#: Loggers the upstream CLI and eodms-py write to.
_SILENCED_LOGGERS = ("eodms_cli", "eodms")
_HOME_VARS = ("HOME", "USERPROFILE")


@dataclass(frozen=True)
class Sandbox:
    """Handle for an active sandbox (used by the doctor command in tests)."""

    home: str
    removed_commands: tuple[str, ...]

    @property
    def config_path(self) -> str:
        return os.path.join(self.home, ".eodms", "config.ini")


def _strip_file_handlers() -> list[logging.Handler]:
    removed: list[logging.Handler] = []
    for name in _SILENCED_LOGGERS:
        logger = logging.getLogger(name)
        for handler in list(logger.handlers):
            if isinstance(handler, logging.FileHandler):
                logger.removeHandler(handler)
                removed.append(handler)
    return removed


def _restore_handlers(handlers: list[logging.Handler]) -> None:
    for name in _SILENCED_LOGGERS:
        logger = logging.getLogger(name)
        for handler in handlers:
            if handler not in logger.handlers:
                logger.addHandler(handler)


def _neutralise_file_logging(upstream: Any) -> None:
    upstream._initialize_cli_logging = lambda: None
    for name in _SILENCED_LOGGERS:
        logger = logging.getLogger(name)
        if not logger.handlers:
            logger.addHandler(logging.NullHandler())


def _neutralise_aaa_token_persistence() -> None:
    try:
        from eodms.aaa import AAA_Creds
    except Exception:  # pragma: no cover - eodms-py always present in the venv
        return

    def _memory_only_export(self):  # noqa: ANN001
        return None

    def _memory_only_import(self):  # noqa: ANN001
        return None

    AAA_Creds.export_vals = _memory_only_export
    AAA_Creds.import_vals = _memory_only_import


@contextmanager
def isolated_eodms_environment(upstream: Any) -> Iterator[Sandbox]:
    """Run a block with the upstream CLI stripped of all credential/file paths."""
    saved_env = {var: os.environ.get(var) for var in _HOME_VARS}
    saved_commands = {
        name: upstream.cli.commands.pop(name)
        for name in FORBIDDEN_UPSTREAM_COMMANDS
        if name in upstream.cli.commands
    }
    home = tempfile.mkdtemp(prefix="eodms-wrapper-")
    detached: list[logging.Handler] = []
    try:
        for var in _HOME_VARS:
            os.environ[var] = home
        _neutralise_file_logging(upstream)
        _neutralise_aaa_token_persistence()
        detached = _strip_file_handlers()
        yield Sandbox(home=home, removed_commands=tuple(saved_commands))
        detached.extend(_strip_file_handlers())
    finally:
        _restore_handlers(detached)
        for name, command in saved_commands.items():
            upstream.cli.commands[name] = command
        for var, value in saved_env.items():
            if value is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = value
        shutil.rmtree(home, ignore_errors=True)
