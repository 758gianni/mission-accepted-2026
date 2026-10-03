"""Credential-free, reproducible acquisition tooling for the EODMS CLI.

This package is a thin, audited wrapper around the official
``eodms-sgdot/eodms-cli`` (pinned source revision, not a PyPI package). It adds
no service and no pipeline: it only builds the upstream Click command line and
invokes it in-process.

Credential policy enforced here:

* credentials are prompted for interactively (username + ``getpass``) and are
  passed to the upstream CLI as **in-memory Click parameter defaults**, never
  via ``argv``, environment variables, config files or logs;
* the user's existing ``~/.eodms`` is never read, and no file is written to
  ``HOME``/``USERPROFILE`` (the upstream ``configure`` command, which stores a
  base64 password, is removed from the Click group inside the sandbox);
* upstream CLI file logging is disabled;
* AAA access/refresh tokens are kept in memory only (``export_vals``/
  ``import_vals`` are neutralised), so no ``aaa_creds.*.json`` is ever written.
"""

from __future__ import annotations

from pathlib import Path

__all__ = [
    "EODMS_CLI_REPO",
    "EODMS_CLI_REV",
    "EODMS_PY_REPO",
    "EODMS_PY_REV",
    "FORBIDDEN_UPSTREAM_COMMANDS",
    "RAPI_REPO",
    "RAPI_REV",
    "REPO_ROOT",
    "TOOLS_DIR",
    "UPSTREAM_SRC_DIR",
    "VENV_DIR",
    "__version__",
]

__version__ = "0.1.0"

EODMS_CLI_REPO = "https://github.com/eodms-sgdot/eodms-cli.git"
EODMS_CLI_REV = "464b94920e7faf28c84a6d31229ef0b2828a1479"

#: Upstream commands the wrapper must never expose (credential persistence).
FORBIDDEN_UPSTREAM_COMMANDS = ("configure",)

REPO_ROOT = str(Path(__file__).resolve().parents[1])

#: Transitive EODMS API clients, pinned so the install is reproducible.
EODMS_PY_REPO = "https://github.com/eodms-sgdot/eodms-py.git"
EODMS_PY_REV = "ec373705727dd50a3e80d092356f43e5c5f0e075"
RAPI_REPO = "https://github.com/eodms-sgdot/py-eodms-rapi.git"
RAPI_REV = "22aa6348a120da98c1122057a245a1ba3100b759"

TOOLS_DIR = ".tools/eodms-cli"
UPSTREAM_SRC_DIR = ".tools/eodms-cli/src"
VENV_DIR = ".tools/eodms-cli/.venv"

#: Project defaults for the Radarsat-2 tropical forest collection.
DEFAULT_COLLECTION = "Radarsat-2_Tropical_Forest_Products"
DEFAULT_SCENES_FILE = "data/interim/selected-scenes.geojson"
DEFAULT_RAW_DIR = "data/raw"

#: Default strict bound on how many products one invocation may select/download.
DEFAULT_ITEM_BOUND = 100
