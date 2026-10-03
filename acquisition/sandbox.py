"""Runtime sandbox for the upstream EODMS CLI and its pinned dependencies.

Audit of every home-derived path in the pinned sources
(``eodms-cli@464b94920e7faf28c84a6d31229ef0b2828a1479`` plus the pinned
``eodms-py`` / ``py-eodms-rapi`` installs):

======================================================  =====================
path / behaviour                                       source                  mitigation
======================================================  =====================
``~/.eodms/config.ini`` (``USERPROFILE`` first)         eodms_cli.py:122-124   ``_default_config_path`` replaced
``~/.eodms/config.ini`` + rename of                    config_util.py:25-31   ``config_util.os`` shim
``~/.eodms/eodmscli_config.ini``                                               (``os.path.expanduser`` -> task temp dir)
``<cli src>/config.ini`` moved into the config dir     config_util.py:401-422 falls inside the shimmed dir
``<cli src>/log/eodms_cli.log``                         eodms_cli.py:169-191   ``_initialize_cli_logging`` no-op
``<cli src>/log/eodms_cli.log`` handlers               eodms_cli.py:211-309   file handlers detached
``~/.eodms/aaa_creds.<user>.<env>.json`` (+ lock)       eodms/aaa.py:127-188   ``eodms.aaa.os`` shim +
                                                                             export/import neutralised
``~/.eodms/config.ini`` written by ``configure``       eodms_cli.py:398-406   command removed + refused
======================================================  =====================

Nothing else in the pinned dependencies touches the home directory
(``eodms/config.py`` holds service URLs only; ``eodms_rapi`` writes exclusively
to caller-supplied destinations; ``api_logger`` only adds a file handler when a
caller passes ``log_file``).

**No environment variable is modified.** ``HOME``, ``USERPROFILE`` and
``CODEX_HOME`` are left exactly as the user's shell set them, and the user's real
``~/.eodms`` is never opened, renamed or written: each home lookup is redirected,
in-process, to an explicit task-scoped temporary directory that is removed on
exit, and every patched attribute is restored.
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


@dataclass(frozen=True)
class Sandbox:
    """Handle for an active sandbox."""

    root: str
    removed_commands: tuple[str, ...]

    @property
    def home(self) -> str:
        """Task-scoped stand-in for ``~`` used by the patched home lookups."""
        return self.root

    @property
    def eodms_dir(self) -> str:
        return os.path.join(self.root, ".eodms")

    @property
    def config_path(self) -> str:
        return os.path.join(self.eodms_dir, "config.ini")


class _PathModuleShim:
    """``os.path`` proxy whose ``expanduser`` resolves ``~`` into the sandbox."""

    def __init__(self, real_path_module, home: str):
        self._real = real_path_module
        self._home = home

    def expanduser(self, path):
        text = str(path)
        if text == "~":
            return self._home
        if text.startswith("~" + os.sep) or text.startswith("~/"):
            return os.path.join(self._home, text[2:])
        return self._real.expanduser(text)

    def __getattr__(self, name):
        return getattr(self._real, name)


class _OsModuleShim:
    """``os`` proxy that only changes ``path.expanduser``."""

    def __init__(self, real_os_module, home: str):
        self._real = real_os_module
        self.path = _PathModuleShim(real_os_module.path, home)

    def __getattr__(self, name):
        return getattr(self._real, name)


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
    """Stop the group callback from creating ``log/eodms_cli.log``."""
    upstream._initialize_cli_logging = lambda: None
    for name in _SILENCED_LOGGERS:
        logger = logging.getLogger(name)
        if not logger.handlers:
            logger.addHandler(logging.NullHandler())


def _neutralise_aaa_token_persistence(_patch) -> None:
    """Keep AAA access/refresh tokens in memory only (never ``aaa_creds.*.json``)."""
    from eodms.aaa import AAA_Creds

    def _memory_only_export(self):  # noqa: ANN001
        # Tokens stay in memory; register them so they cannot leak into output.
        from .redaction import register_secrets

        register_secrets(getattr(self, "access_token", None), getattr(self, "refresh_token", None))
        return None

    def _memory_only_import(self):  # noqa: ANN001
        return None

    _patch(AAA_Creds, "export_vals", _memory_only_export)
    _patch(AAA_Creds, "import_vals", _memory_only_import)


@contextmanager
def isolated_eodms_environment(upstream: Any) -> Iterator[Sandbox]:
    """Run a block with the upstream CLI stripped of all credential/file paths.

    ``os.environ`` is **not** touched. Every patched attribute (module globals,
    class methods, the Click group) is restored when the block exits.
    """
    root = tempfile.mkdtemp(prefix="eodms-wrapper-")
    os.makedirs(os.path.join(root, ".eodms"), exist_ok=True)

    # (target object, attribute, original value) restored in reverse on exit
    saved: list[tuple[Any, str, Any]] = []
    detached: list[logging.Handler] = []

    def _patch(obj: Any, name: str, value: Any) -> None:
        saved.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def _sandbox_default_config_path() -> str:
        return os.path.join(root, ".eodms", "config.ini")

    try:
        _patch(upstream, "_initialize_cli_logging", lambda: None)
        _patch(upstream, "_default_config_path", _sandbox_default_config_path)
        # eodms_cli uses `os` for its own logging paths only; the shim keeps any
        # incidental home lookup inside the task temp dir.
        _patch(upstream, "os", _OsModuleShim(upstream.os, root))

        removed = {
            name: upstream.cli.commands.pop(name)
            for name in FORBIDDEN_UPSTREAM_COMMANDS
            if name in upstream.cli.commands
        }

        config_util = _config_util_module()
        if config_util is not None:
            _patch(config_util, "os", _OsModuleShim(config_util.os, root))

        aaa_module = _aaa_module()
        if aaa_module is not None:
            _patch(aaa_module, "os", _OsModuleShim(aaa_module.os, root))
            _neutralise_aaa_token_persistence(_patch)

        _neutralise_file_logging(upstream)
        detached = _strip_file_handlers()
        yield Sandbox(
            root=root,
            removed_commands=tuple(name for name in removed if name),
        )
        detached.extend(_strip_file_handlers())
    finally:
        _restore_handlers(detached)
        for obj, name, value in reversed(saved):
            setattr(obj, name, value)
        for name, command in removed.items():
            upstream.cli.commands[name] = command
        shutil.rmtree(root, ignore_errors=True)


def _config_util_module():
    try:
        from scripts import config_util  # noqa: PLC0415

        return config_util
    except Exception:
        return None


def _aaa_module():
    try:
        import eodms.aaa as aaa_module  # noqa: PLC0415

        return aaa_module
    except Exception:
        return None


def sandboxed_paths(upstream: Any) -> dict[str, str]:
    """Resolve, without side effects, the home-derived paths the upstream would use.

    Pure string maths on purpose: instantiating ``ConfigUtils`` outside a sandbox
    creates ``~/.eodms`` and writes ``config.ini``, which is exactly what this
    project must never do.
    """
    paths: dict[str, str] = {"eodms_cli.default_config": upstream._default_config_path()}

    config_util = _config_util_module()
    if config_util is not None:
        home = config_util.os.path.expanduser("~")
        paths["config_util.config_fn"] = os.path.join(
            config_util.os.sep, home, ".eodms", "config.ini"
        )
        paths["config_util.legacy_config_fn"] = os.path.join(
            config_util.os.sep, home, ".eodms", "eodmscli_config.ini"
        )

    aaa_module = _aaa_module()
    if aaa_module is not None:
        home = aaa_module.os.path.expanduser("~")
        paths["aaa.creds_fn"] = os.path.join(home, ".eodms", "aaa_creds.<user>.prod.json")
        paths["aaa.token_lock_fn"] = paths["aaa.creds_fn"] + ".lock"

    return paths
