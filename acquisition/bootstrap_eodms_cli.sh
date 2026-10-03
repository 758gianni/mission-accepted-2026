#!/usr/bin/env bash
# Reproducible, credential-free install of the official EODMS CLI (from source)
# into the ignored directory .tools/eodms-cli.
#
# The upstream EODMS CLI is NOT published on PyPI; it is installed from a
# pinned source revision. This script never touches credentials, never reads
# ~/.eodms, and never performs an EODMS login.
#
# Usage:  bash acquisition/bootstrap_eodms_cli.sh
# Result: .tools/eodms-cli/.venv/bin/python  (use this interpreter to run the wrapper)
set -euo pipefail

EODMS_CLI_REPO="https://github.com/eodms-sgdot/eodms-cli.git"
EODMS_CLI_REV="464b94920e7faf28c84a6d31229ef0b2828a1479"

# Transitive EODMS API clients, pinned so the install is reproducible.
EODMS_PY_REPO="https://github.com/eodms-sgdot/eodms-py.git"
EODMS_PY_REV="ec373705727dd50a3e80d092356f43e5c5f0e075"
RAPI_REPO="https://github.com/eodms-sgdot/py-eodms-rapi.git"
RAPI_REV="22aa6348a120da98c1122057a245a1ba3100b759"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOLS_DIR="${REPO_ROOT}/.tools/eodms-cli"
VENV_DIR="${TOOLS_DIR}/.venv"
SRC_DIR="${TOOLS_DIR}/src"

log() { printf '[bootstrap] %s\n' "$*"; }

if [ ! -d "${SRC_DIR}/.git" ]; then
  log "cloning ${EODMS_CLI_REPO}"
  mkdir -p "${TOOLS_DIR}"
  git clone --quiet "${EODMS_CLI_REPO}" "${SRC_DIR}"
fi

git -C "${SRC_DIR}" fetch --quiet --depth 1 origin "${EODMS_CLI_REV}" || true
git -C "${SRC_DIR}" checkout --quiet --force "${EODMS_CLI_REV}"

ACTUAL_REV="$(git -C "${SRC_DIR}" rev-parse HEAD)"
if [ "${ACTUAL_REV}" != "${EODMS_CLI_REV}" ]; then
  log "ERROR: expected revision ${EODMS_CLI_REV}, got ${ACTUAL_REV}"
  exit 1
fi
log "eodms-cli pinned at ${ACTUAL_REV}"

if [ ! -x "${VENV_DIR}/bin/python" ]; then
  log "creating virtualenv at ${VENV_DIR}"
  python3 -m venv "${VENV_DIR}"
fi

PY="${VENV_DIR}/bin/python"
"${PY}" -m pip install --quiet --upgrade pip

# Install the CLI's own runtime requirements (read from the pinned source tree),
# then the two pinned EODMS API client repos. The eodms-cli package itself is
# imported from the pinned source tree by the wrapper, so it is not pip-installed.
log "installing dependencies (this may take a few minutes on first run)"
"${PY}" -m pip install --quiet --requirement "${SRC_DIR}/requirements.txt"
"${PY}" -m pip install --quiet "eodms-py @ git+${EODMS_PY_REPO}@${EODMS_PY_REV}"
"${PY}" -m pip install --quiet "py-eodms-rapi @ git+${RAPI_REPO}@${RAPI_REV}"

# Dev/test dependencies for the wrapper itself, so the documented test command
# (`${VENV_DIR}/bin/python -m pytest tests/acquisition -q`) works straight after
# a bootstrap. Pinned in requirements-acquisition.txt.
"${PY}" -m pip install --quiet --requirement "${REPO_ROOT}/requirements-acquisition.txt"

# Integrity manifest: pins plus a pip RECORD digest for every installed
# distribution. acquisition/integrity.py re-verifies source and dependency
# integrity before the CLI is imported or any credential is requested.
log "writing the integrity manifest"
PYTHONPATH="${REPO_ROOT}" "${PY}" -W ignore -m acquisition.integrity --write-manifest
log "credential-free integrity check"
PYTHONPATH="${REPO_ROOT}" "${PY}" -W ignore -m acquisition.integrity

# Credential-free smoke check: import the pinned CLI and render its help inside
# the wrapper sandbox, which redirects every home-derived lookup to a task-scoped
# temp dir. No EODMS login, no network, no ~/.eodms read, no log file written,
# and no environment variable is modified.
log "credential-free help check"
(
  cd "${SRC_DIR}"
  PYTHONPATH="${SRC_DIR}:${REPO_ROOT}" \
    "${PY}" -W ignore -c '
import os
import sys
from pathlib import Path
from click.testing import CliRunner

import eodms_cli
from acquisition.sandbox import isolated_eodms_environment, sandboxed_paths

before_env = dict(os.environ)
before_home = str(Path.home())
real_eodms = Path.home() / ".eodms"
before_eodms = sorted(str(p) for p in real_eodms.rglob("*")) if real_eodms.is_dir() else []

runner = CliRunner()
with isolated_eodms_environment(eodms_cli) as sandbox:
    for name, value in sandboxed_paths(eodms_cli).items():
        if not value.startswith(sandbox.root):
            raise SystemExit("%s escaped the sandbox: %s" % (name, value))
    for args in (["--help"], ["search", "--help"], ["download", "--help"]):
        result = runner.invoke(eodms_cli.cli, args)
        if result.exit_code != 0:
            sys.stderr.write(result.output or "no output")
            raise SystemExit("help check failed for %s" % args)

after_eodms = sorted(str(p) for p in real_eodms.rglob("*")) if real_eodms.is_dir() else []
if after_eodms != before_eodms:
    raise SystemExit("real ~/.eodms was modified: %s" % after_eodms)
if dict(os.environ) != before_env or str(Path.home()) != before_home:
    raise SystemExit("environment or home was modified")
if Path(os.path.join(os.path.dirname(os.path.abspath(eodms_cli.__file__)), "log")).exists():
    raise SystemExit("upstream file logging was not disabled")
print("[bootstrap] pinned eodms-cli help OK; sandbox contained all ~/.eodms lookups")
'
)

log "done. run the wrapper with:"
log "  ${VENV_DIR}/bin/python -m acquisition doctor"
