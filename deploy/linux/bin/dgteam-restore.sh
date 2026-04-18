#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 [--restore-deployments-to <dir>] [--restore-automation-state-to <dir>] <backup-tar-or-directory>" >&2
}

RESTORE_DEPLOYMENTS_TO=""
RESTORE_AUTOMATION_STATE_TO=""
POSITIONAL_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --restore-deployments-to)
      RESTORE_DEPLOYMENTS_TO="$2"
      shift 2
      ;;
    --restore-automation-state-to)
      RESTORE_AUTOMATION_STATE_TO="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --*)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
    *)
      POSITIONAL_ARGS+=("$1")
      shift
      ;;
  esac
done

if [[ ${#POSITIONAL_ARGS[@]} -ne 1 ]]; then
  usage
  exit 1
fi

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
LIVE_DIR="${PROJECT_ROOT}/runtime/cloud/current"
PREVIOUS_DIR="${PROJECT_ROOT}/runtime/cloud/previous"
RESTORE_SOURCE="$(readlink -f "${POSITIONAL_ARGS[0]}")"
WORK_DIR="$(mktemp -d)"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
SERVICES=(
  "dgteam-query.service"
  "dgteam-publish.service"
  "dgteam-wechat-official.service"
  "dgteam-wechat-official-worker.service"
  "dgteam-wechat-clawbot.service"
)

cleanup() {
  rm -rf "${WORK_DIR}"
}
trap cleanup EXIT

if [[ -d "${RESTORE_SOURCE}" ]]; then
  cp -a "${RESTORE_SOURCE}/." "${WORK_DIR}/"
else
  tar -xzf "${RESTORE_SOURCE}" -C "${WORK_DIR}"
  FIRST_DIR="$(find "${WORK_DIR}" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
  if [[ -n "${FIRST_DIR:-}" ]]; then
    WORK_DIR="${FIRST_DIR}"
  fi
fi

if [[ ! -f "${WORK_DIR}/dgteam.db" ]]; then
  echo "Backup is missing dgteam.db: ${RESTORE_SOURCE}" >&2
  exit 1
fi

"${PYTHON_BIN}" - "${WORK_DIR}/dgteam.db" <<'PY'
import sqlite3
import sys
from pathlib import Path

db_path = Path(sys.argv[1]).resolve()
conn = sqlite3.connect(db_path)
try:
    cursor = conn.execute("PRAGMA quick_check;")
    row = cursor.fetchone()
    if not row or str(row[0]).lower() != "ok":
        raise RuntimeError(f"sqlite quick_check failed: {row!r}")
finally:
    conn.close()
PY

for service in "${SERVICES[@]}"; do
  systemctl stop "${service}" || true
done

mkdir -p "${PREVIOUS_DIR}"
if [[ -d "${LIVE_DIR}" ]]; then
  rm -rf "${PREVIOUS_DIR}"
  mv "${LIVE_DIR}" "${PREVIOUS_DIR}"
fi
mkdir -p "${LIVE_DIR}"

cp -a "${WORK_DIR}/dgteam.db" "${LIVE_DIR}/dgteam.db"
for file_name in manifest.json release.json summary.json market_v1_snapshot.csv market_v1_clusters.csv; do
  if [[ -f "${WORK_DIR}/${file_name}" ]]; then
    cp -a "${WORK_DIR}/${file_name}" "${LIVE_DIR}/${file_name}"
  fi
done

if [[ -n "${RESTORE_DEPLOYMENTS_TO}" && -d "${WORK_DIR}/deployments" ]]; then
  rm -rf "${RESTORE_DEPLOYMENTS_TO}"
  mkdir -p "$(dirname "${RESTORE_DEPLOYMENTS_TO}")"
  cp -a "${WORK_DIR}/deployments" "${RESTORE_DEPLOYMENTS_TO}"
fi

if [[ -n "${RESTORE_AUTOMATION_STATE_TO}" && -d "${WORK_DIR}/local_automation_state" ]]; then
  rm -rf "${RESTORE_AUTOMATION_STATE_TO}"
  mkdir -p "$(dirname "${RESTORE_AUTOMATION_STATE_TO}")"
  cp -a "${WORK_DIR}/local_automation_state" "${RESTORE_AUTOMATION_STATE_TO}"
fi

for service in "${SERVICES[@]}"; do
  systemctl start "${service}" || true
done

echo "Restore completed at ${TIMESTAMP} from ${RESTORE_SOURCE}"
if [[ -n "${RESTORE_DEPLOYMENTS_TO}" ]]; then
  echo "Deployment journals extracted to ${RESTORE_DEPLOYMENTS_TO}"
fi
if [[ -n "${RESTORE_AUTOMATION_STATE_TO}" ]]; then
  echo "Automation checkpoints extracted to ${RESTORE_AUTOMATION_STATE_TO}"
fi
