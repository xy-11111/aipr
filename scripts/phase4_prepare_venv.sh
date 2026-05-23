#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${REPO_ROOT}/.venv-phase4"
REQUIREMENTS_FILE="${REPO_ROOT}/requirements-phase4.txt"

"${PYTHON_BIN}" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 10) else 1)'
if ! "${PYTHON_BIN}" -m venv "${VENV_DIR}" >/dev/null 2>&1; then
  if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    "${PYTHON_BIN}" -m pip install --user virtualenv
    "${PYTHON_BIN}" -m virtualenv "${VENV_DIR}"
  fi
fi
VENV_PYTHON="${VENV_DIR}/bin/python"
VENV_SITE_PACKAGES="$("${VENV_PYTHON}" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
mkdir -p "${VENV_SITE_PACKAGES}"
if "${VENV_PYTHON}" -m pip --version >/dev/null 2>&1; then
  "${VENV_PYTHON}" -m pip install --upgrade pip wheel setuptools
  "${VENV_PYTHON}" -m pip install -r "${REQUIREMENTS_FILE}"
else
  "${PYTHON_BIN}" -m pip install --upgrade --target "${VENV_SITE_PACKAGES}" pip wheel setuptools
  "${PYTHON_BIN}" -m pip install --target "${VENV_SITE_PACKAGES}" -r "${REQUIREMENTS_FILE}"
fi

echo "Phase 4 venv is ready: ${VENV_DIR}"
