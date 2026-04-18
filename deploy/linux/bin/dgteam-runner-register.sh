#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="${DGTEAM_PROJECT_ROOT:-${PROJECT_ROOT:-/srv/dgteam}}"
resolve_python_bin() {
  if [[ -n "${DGTEAM_PYTHON_BIN:-}" ]]; then
    printf '%s\n' "${DGTEAM_PYTHON_BIN}"
    return 0
  fi
  if [[ -x "${PROJECT_ROOT}/.venv/bin/python" ]]; then
    printf '%s\n' "${PROJECT_ROOT}/.venv/bin/python"
    return 0
  fi
  if command -v python3 >/dev/null 2>&1; then
    printf '%s\n' "python3"
    return 0
  fi
  printf '%s\n' "python"
}
PYTHON_BIN="$(resolve_python_bin)"

"${PYTHON_BIN}" "${PROJECT_ROOT}/scripts/trusted_runner_register.py" "$@"
