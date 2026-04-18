#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="${DGTEAM_PROJECT_ROOT:-${PROJECT_ROOT:-/srv/dgteam}}"
BACKUP_ROOT="${DGTEAM_BACKUP_ROOT:-${BACKUP_ROOT:-/var/backups/dgteam}}"
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
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
LIVE_DIR="${PROJECT_ROOT}/runtime/cloud/current"
DEPLOYMENTS_DIR="${PROJECT_ROOT}/runtime/cloud/deployments"
AUTOMATION_STATE_DIR="${PROJECT_ROOT}/runtime/local/automation/prod/state"
TARGET_DIR="${BACKUP_ROOT}/backup_${TIMESTAMP}"
ARCHIVE_PATH="${BACKUP_ROOT}/backup_${TIMESTAMP}.tar.gz"
ENV_FILE="${DGTEAM_ENV_FILE:-${PROJECT_ROOT}/.env}"

mkdir -p "${TARGET_DIR}"

if [[ ! -d "${LIVE_DIR}" ]]; then
  echo "Live directory not found: ${LIVE_DIR}" >&2
  exit 1
fi

if [[ ! -f "${LIVE_DIR}/dgteam.db" ]]; then
  echo "Live database not found: ${LIVE_DIR}/dgteam.db" >&2
  exit 1
fi

"${PYTHON_BIN}" - "${LIVE_DIR}/dgteam.db" "${TARGET_DIR}/dgteam.db" <<'PY'
import sqlite3
import sys
from pathlib import Path

source = Path(sys.argv[1]).resolve()
target = Path(sys.argv[2]).resolve()
target.parent.mkdir(parents=True, exist_ok=True)

read_conn = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
write_conn = sqlite3.connect(target)
try:
    read_conn.backup(write_conn)
finally:
    write_conn.close()
    read_conn.close()
PY

for file_name in manifest.json release.json summary.json market_v1_snapshot.csv market_v1_clusters.csv; do
  if [[ -f "${LIVE_DIR}/${file_name}" ]]; then
    cp -a "${LIVE_DIR}/${file_name}" "${TARGET_DIR}/${file_name}"
  fi
done

if [[ -d "${PROJECT_ROOT}/runtime/cloud/previous" ]]; then
  cp -a "${PROJECT_ROOT}/runtime/cloud/previous" "${TARGET_DIR}/previous"
fi

if [[ -d "${DEPLOYMENTS_DIR}" ]]; then
  cp -a "${DEPLOYMENTS_DIR}" "${TARGET_DIR}/deployments"
fi

if [[ -d "${AUTOMATION_STATE_DIR}" ]]; then
  cp -a "${AUTOMATION_STATE_DIR}" "${TARGET_DIR}/local_automation_state"
fi

if [[ -f "${ENV_FILE}" ]]; then
  cp -a "${ENV_FILE}" "${TARGET_DIR}/dgteam.env"
fi

tar -czf "${ARCHIVE_PATH}" -C "${BACKUP_ROOT}" "backup_${TIMESTAMP}"
echo "Backup created: ${ARCHIVE_PATH}"
