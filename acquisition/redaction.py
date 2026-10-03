"""Redaction of credential and signed-URL material.

EODMS download URLs are pre-signed: they carry access tokens, refresh tokens and
sometimes credentials in the query string or in HTTP basic-auth userinfo. Those
values can surface in upstream log records, in ``click.echo`` output, in the text
of an exception, and in the JSONL download manifest the upstream maintains.

:func:`redaction_scope` installs, for the duration of one upstream invocation:

* a ``logging`` record factory that redacts every record at creation time (so
  every logger, including ``eodms.*``, is covered - filters on individual
  loggers do not apply to propagated records);
* redacting proxies for ``sys.stdout``/``sys.stderr``;
* a post-pass that scrubs token-bearing ``url``/``detail``/``error``/``message``
  fields from any download manifest written into the download directory.

Everything is restored on exit.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import sys
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

#: Query-string / JSON / header keys whose values are never safe to print.
SENSITIVE_KEYS = (
    "access_token",
    "accesstoken",
    "refresh_token",
    "refreshtoken",
    "auth_token",
    "authtoken",
    "authorization",
    "token",
    "password",
    "passwd",
    "pwd",
    "secret",
    "client_secret",
    "api_key",
    "apikey",
    "x-auth-token",
    "x-amz-security-token",
    "signature",
    "sig",
    "credential",
    "sessionid",
    "session_id",
    "jsessionid",
)

#: Manifest fields that can carry a signed URL or an upstream error string.
MANIFEST_SCRUBBED_FIELDS = ("url", "href", "link", "detail", "error", "message", "reason")

_REDACTED = "[REDACTED]"

#: Live secret values (the prompted password, AAA access/refresh tokens). Signed
#: URLs embed tokens in the *path* as well as the query string, so pattern rules
#: alone are not enough: known secret values are also scrubbed verbatim.
_KNOWN_SECRETS: set[str] = set()
_KNOWN_SECRETS_LOCK = threading.Lock()


def register_secrets(*values: Any) -> tuple[str, ...]:
    """Remember live secret values so they are scrubbed wherever they appear.

    Values are held in memory for the life of the process only.
    """
    added = []
    with _KNOWN_SECRETS_LOCK:
        for value in values:
            text = str(value or "").strip()
            if len(text) >= 6 and text not in _KNOWN_SECRETS:
                _KNOWN_SECRETS.add(text)
                added.append(text)
    return tuple(added)


def forget_secrets() -> None:
    with _KNOWN_SECRETS_LOCK:
        _KNOWN_SECRETS.clear()


def known_secrets() -> tuple[str, ...]:
    with _KNOWN_SECRETS_LOCK:
        return tuple(sorted(_KNOWN_SECRETS, key=len, reverse=True))

_QUERY_SECRET = re.compile(
    r"(?i)\b(" + "|".join(SENSITIVE_KEYS) + r")=([^&\s\"'\\]+)"
)
_JSON_SECRET = re.compile(
    r"(?i)([\"']\b(?:" + "|".join(SENSITIVE_KEYS) + r")\b[\"']\s*:\s*)([\"'])(.*?)(?<!\\)\2"
)
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")
_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@")


def redact_text(text: Any) -> str:
    """Remove token/password-bearing material from a string."""
    if text is None:
        return ""
    value = text if isinstance(text, str) else str(text)
    for secret in known_secrets():
        if secret in value:
            value = value.replace(secret, _REDACTED)
    value = _QUERY_SECRET.sub(lambda m: f"{m.group(1)}={_REDACTED}", value)
    value = _JSON_SECRET.sub(lambda m: f"{m.group(1)}{m.group(2)}{_REDACTED}{m.group(2)}", value)
    value = _BEARER.sub(lambda m: f"{m.group(1)} {_REDACTED}", value)
    value = _USERINFO.sub(lambda m: f"{m.group(1)}{_REDACTED}@", value)
    return value


def redact_value(value: Any) -> Any:
    """Recursively redact strings and mapping values in a JSON-like structure."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and key.strip().lower() in SENSITIVE_KEYS:
                redacted[key] = _REDACTED
            else:
                redacted[key] = redact_value(item)
        return redacted
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    return value


def _redact_record(record: logging.LogRecord) -> logging.LogRecord:
    """Redact one already-constructed record (message and interpolated args)."""
    if not isinstance(record, logging.LogRecord):
        return record
    if isinstance(record.msg, str):
        record.msg = redact_text(record.msg)
    if record.args:
        if isinstance(record.args, dict):
            record.args = {k: _redact_arg(v) for k, v in record.args.items()}
        elif isinstance(record.args, tuple):
            record.args = tuple(_redact_arg(value) for value in record.args)
    return record


class _RedactedStr(str):
    """A string that is already redacted, safe for ``%s`` interpolation."""


def _redact_arg(value: Any) -> Any:
    """Redact an interpolated log argument, including exception objects.

    ``logger.error("...: %s", exc)`` only interpolates at format time, so an
    exception argument has to be redacted here or the secret survives in the
    formatted record.
    """
    if isinstance(value, str):
        return _RedactedStr(redact_text(value))
    if isinstance(value, BaseException):
        return _RedactedStr(redact_text(f"{value}"))
    if isinstance(value, dict):
        return {k: _redact_arg(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_redact_arg(item) for item in value)
    return value


class _RedactingStream(io.TextIOBase):
    """In-memory stream proxy that redacts every write (used when a test runner
    or CI has replaced ``sys.stdout``/``sys.stderr`` with its own object)."""

    def __init__(self, wrapped):
        self._wrapped = wrapped
        for attribute in ("encoding", "errors", "newlines", "name"):
            try:
                setattr(self, attribute, getattr(wrapped, attribute))
            except (AttributeError, ValueError):
                pass

    def writable(self):  # noqa: ANN201
        return True

    def readable(self):  # noqa: ANN201
        return False

    def seekable(self):  # noqa: ANN201
        return False

    def write(self, text):  # noqa: ANN001
        self._wrapped.write(redact_text(text))
        return len(text)

    def writelines(self, lines):  # noqa: ANN001
        for line in lines:
            self.write(line)

    def flush(self):  # noqa: ANN001
        return self._wrapped.flush()

    def isatty(self):  # noqa: ANN201
        return getattr(self._wrapped, "isatty", lambda: False)()

    def __getattr__(self, name):  # noqa: ANN001
        return getattr(self._wrapped, name)


class _FileDescriptorRedirect:
    """Capture a real fd (1/2) into a temp file, then emit it redacted.

    Replacing ``sys.stdout`` with a proxy is not viable: Click resolves and caches
    its own text stream and would either bypass the proxy or rewrap the fd buffer.
    Redirecting the descriptor itself is invisible to Click and to the upstream.
    """

    def __init__(self, fd: int):
        self.fd = fd
        self.saved_fd: int | None = None
        self.temp = tempfile.TemporaryFile(mode="w+b")
        self.stream = io.TextIOWrapper(self.temp, encoding="utf-8", errors="replace")

    def __enter__(self):
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.flush()
            except Exception:
                pass
        self.saved_fd = os.dup(self.fd)
        os.dup2(self.temp.fileno(), self.fd)
        return self

    def __exit__(self, *_exc):
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.flush()
            except Exception:
                pass
        if self.saved_fd is not None:
            os.dup2(self.saved_fd, self.fd)
            os.close(self.saved_fd)
            self.saved_fd = None
        self.stream.flush()
        self.stream.seek(0)
        data = self.temp.read()
        self.stream.close()
        self.temp.close()
        target = sys.__stdout__ if self.fd == 1 else sys.__stderr__
        if data:
            target.write(redact_text(data.decode("utf-8", errors="replace")))
            target.flush()
        return False


def scrub_download_manifest(download_dir: Any) -> list[str]:
    """Rewrite any ``downloads.jsonl`` in ``download_dir`` with redacted fields.

    Returns the list of files that were rewritten.
    """
    if not download_dir:
        return []
    root = Path(download_dir)
    if not root.is_dir():
        return []
    rewritten: list[str] = []
    for manifest in sorted(root.rglob("downloads.jsonl")):
        try:
            raw = manifest.read_text(encoding="utf-8")
        except OSError:
            continue
        lines_out: list[str] = []
        changed = False
        for line in raw.splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                cleaned = redact_text(line)
                changed = changed or cleaned != line
                lines_out.append(cleaned)
                continue
            if isinstance(record, dict):
                for field in MANIFEST_SCRUBBED_FIELDS:
                    if field in record:
                        cleaned = redact_value(record[field])
                        if cleaned != record[field]:
                            record[field] = cleaned
                            changed = True
            lines_out.append(json.dumps(record, sort_keys=True))
        if changed:
            manifest.write_text("\n".join(lines_out) + "\n", encoding="utf-8")
            rewritten.append(str(manifest))
    return rewritten


@contextmanager
def redaction_scope(download_dir: Any = None) -> Iterator[None]:
    """Redact stdout/stderr, every log record, and the download manifest."""
    previous_factory = logging.getLogRecordFactory()

    def factory(*args, **kwargs):
        return _redact_record(previous_factory(*args, **kwargs))

    logging.setLogRecordFactory(factory)
    # Two cases: a test runner/CI may have replaced sys.stdout with its own
    # in-memory stream (then the object must be wrapped), while a real shell run
    # keeps the interpreter's own streams (then the descriptor is redirected).
    redirects = []
    saved_objects = {}
    try:
        for fd, attr, original in ((1, "stdout", sys.__stdout__), (2, "stderr", sys.__stderr__)):
            current = getattr(sys, attr)
            if current is not original:
                saved_objects[attr] = current
                setattr(sys, attr, _RedactingStream(current))
                continue
            try:
                redirects.append(_FileDescriptorRedirect(fd).__enter__())
            except OSError:  # pragma: no cover - fd not capturable
                pass
        yield
    finally:
        try:
            scrub_download_manifest(download_dir)
        finally:
            for redirect in reversed(redirects):
                redirect.__exit__()
            for attr, stream in saved_objects.items():
                setattr(sys, attr, stream)
            logging.setLogRecordFactory(previous_factory)


def assert_no_secret_material(text: Any, secrets: tuple[str, ...]) -> None:
    """Test helper: raise if any sentinel survived redaction."""
    haystack = "" if text is None else str(text)
    for secret in secrets:
        if secret and secret in haystack:
            raise AssertionError(f"secret material survived redaction: {secret!r}")


def sentinel_secrets(*values: str) -> tuple[str, ...]:
    return tuple(str(value) for value in values if value)


def environment_probe() -> str:  # pragma: no cover - convenience for debugging
    return os.environ.get("PYTHONHASHSEED", "")