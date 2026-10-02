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

# Credential-free smoke check: import the pinned CLI and render its help.
# No EODMS login, no ~/.eodms read, no network.
log "credential-free help check"
SMOKE_HOME="$(mktemp -d)"
trap 'rm -rf "${SMOKE_HOME}"' EXIT
(
  cd "${SRC_DIR}"
  HOME="${SMOKE_HOME}" USERPROFILE="${SMOKE_HOME}" \
    "${PY}" -W ignore -c '
import sys
from click.testing import CliRunner
import eodms_cli
runner = CliRunner()
for args in (["--help"], ["search", "--help"], ["download", "--help"]):
    result = runner.invoke(eodms_cli.cli, args)
    if result.exit_code != 0:
        sys.stderr.write(result.output or "no output")
        raise SystemExit("help check failed for %s" % args)
print("[bootstrap] pinned eodms-cli help OK")
'
)

rm -rf "${VENV_DIR}/../.tools-smoke" 2>/dev/null || true
log "done. run the wrapper with:"
log "  ${VENV_DIR}/bin/python -m acquisition doctor"
