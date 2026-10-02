"""Interactive, memory-only credential acquisition.

Rules enforced here:

* nothing is ever read from the environment, a config file or a file cache;
* the username is prompted for with a visible prompt, the password with
  ``getpass`` so it is never echoed;
* the returned values live only in the caller's stack -- they are handed to the
  upstream Click command as parameter defaults and are not written anywhere.
"""

from __future__ import annotations

import getpass
from typing import Callable

import click


class CredentialError(click.ClickException):
    """Raised when credentials are missing or incomplete."""


def prompt_username(label: str = "EODMS username") -> str:
    """Prompt (echoing) for the EODMS username."""
    value = click.prompt(label, type=str, default="", show_default=False)
    return str(value).strip()


def prompt_password(label: str = "EODMS password") -> str:
    """Prompt for the EODMS password without echoing it."""
    return str(getpass.getpass(f"{label}: "))


def obtain_credentials(
    *,
    anonymous: bool,
    needs_credentials: bool,
    username_prompt: Callable[[], str] | None = None,
    password_prompt: Callable[[], str] | None = None,
) -> tuple[str | None, str | None]:
    """Return ``(username, password)`` for one upstream invocation.

    ``(None, None)`` is returned -- with no prompt at all -- when the command is
    anonymous or does not touch an authenticated endpoint.
    """
    if anonymous or not needs_credentials:
        return None, None

    ask_user = username_prompt or prompt_username
    ask_password = password_prompt or prompt_password

    username = (ask_user() or "").strip()
    if not username:
        raise CredentialError(
            "An EODMS username is required. Re-run and enter it, or use "
            "--anonymous for public catalogue access."
        )

    password = ask_password() or ""
    if not password:
        raise CredentialError("An EODMS password is required.")

    return username, password
