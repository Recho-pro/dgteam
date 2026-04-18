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
QUERY_URL="${DGTEAM_QUERY_HEALTH_URL:-http://127.0.0.1:9765/health}"
PUBLISH_URL="${DGTEAM_PUBLISH_HEALTH_URL:-http://127.0.0.1:9865/health}"
OFFICIAL_URL="${DGTEAM_WECHAT_OFFICIAL_HEALTH_URL:-http://127.0.0.1:8975/health}"
PUBLIC_STATUS_URL="${DGTEAM_PUBLIC_STATUS_URL:-https://dgtdnb.com/api/status}"
QUERY_SERVICE_NAME="${DGTEAM_QUERY_SERVICE_NAME:-dgteam-query.service}"
PUBLISH_SERVICE_NAME="${DGTEAM_PUBLISH_SERVICE_NAME:-dgteam-publish.service}"
OFFICIAL_SERVICE_NAME="${DGTEAM_WECHAT_OFFICIAL_SERVICE_NAME:-dgteam-wechat-official.service}"
OFFICIAL_WORKER_SERVICE_NAME="${DGTEAM_WECHAT_OFFICIAL_WORKER_SERVICE_NAME:-dgteam-wechat-official-worker.service}"
EXPECT_OFFICIAL="${DGTEAM_EXPECT_WECHAT_OFFICIAL:-true}"
EXPECT_CLAWBOT="${DGTEAM_EXPECT_WECHAT_CLAWBOT:-false}"
ALERT_WEBHOOK_URL="${DGTEAM_ALERT_WEBHOOK_URL:-}"
RUN_RUNTIME_AUDIT="${DGTEAM_RUN_RUNTIME_AUDIT:-true}"
BACKUP_ROOT="${DGTEAM_BACKUP_ROOT:-${BACKUP_ROOT:-/var/backups/dgteam}}"
DISK_WARN_PERCENT="${DGTEAM_DISK_WARN_PERCENT:-80}"
MAX_BACKUP_AGE_HOURS="${DGTEAM_MAX_BACKUP_AGE_HOURS:-30}"
MAX_STAGING_AGE_HOURS="${DGTEAM_MAX_STAGING_AGE_HOURS:-6}"
MAX_UPLOAD_AGE_HOURS="${DGTEAM_MAX_UPLOAD_AGE_HOURS:-24}"
MAX_WORKER_BACKLOG="${DGTEAM_MAX_WORKER_BACKLOG:-10}"
MAX_FAILED_TASKS="${DGTEAM_MAX_FAILED_TASKS:-0}"

check_url() {
  local name="$1"
  local url="$2"
  if ! curl --fail --silent --show-error --max-time 10 "${url}" >/dev/null; then
    echo "Healthcheck failed for ${name}: ${url}" >&2
    return 1
  fi
  return 0
}

check_service_active() {
  local name="$1"
  if ! systemctl is-active --quiet "${name}"; then
    echo "Service is not active: ${name}" >&2
    return 1
  fi
  return 0
}

FAILURES=0

check_url "query-api" "${QUERY_URL}" || FAILURES=$((FAILURES + 1))
check_url "publish-api" "${PUBLISH_URL}" || FAILURES=$((FAILURES + 1))
check_service_active "${QUERY_SERVICE_NAME}" || FAILURES=$((FAILURES + 1))
check_service_active "${PUBLISH_SERVICE_NAME}" || FAILURES=$((FAILURES + 1))
if [[ -n "${PUBLIC_STATUS_URL}" ]]; then
  check_url "public-status" "${PUBLIC_STATUS_URL}" || FAILURES=$((FAILURES + 1))
fi

if [[ "${EXPECT_OFFICIAL}" == "true" ]]; then
  check_url "wechat-official" "${OFFICIAL_URL}" || FAILURES=$((FAILURES + 1))
  check_service_active "${OFFICIAL_SERVICE_NAME}" || FAILURES=$((FAILURES + 1))
  check_service_active "${OFFICIAL_WORKER_SERVICE_NAME}" || FAILURES=$((FAILURES + 1))
fi

if [[ "${EXPECT_CLAWBOT}" == "true" ]]; then
  check_service_active "dgteam-wechat-clawbot.service" || FAILURES=$((FAILURES + 1))
fi

if [[ "${RUN_RUNTIME_AUDIT}" == "true" ]]; then
  if ! "${PYTHON_BIN}" "${PROJECT_ROOT}/scripts/ops_runtime_audit.py" \
    --project-root "${PROJECT_ROOT}" \
    --backup-root "${BACKUP_ROOT}" \
    --disk-warn-percent "${DISK_WARN_PERCENT}" \
    --max-backup-age-hours "${MAX_BACKUP_AGE_HOURS}" \
    --max-staging-age-hours "${MAX_STAGING_AGE_HOURS}" \
    --max-upload-age-hours "${MAX_UPLOAD_AGE_HOURS}" \
    --max-worker-backlog "${MAX_WORKER_BACKLOG}" \
    --max-failed-tasks "${MAX_FAILED_TASKS}" \
    --assert-ok >/dev/null; then
    echo "Runtime audit failed for ${PROJECT_ROOT}" >&2
    FAILURES=$((FAILURES + 1))
  fi
fi

if [[ "${FAILURES}" -gt 0 ]]; then
  if [[ -n "${ALERT_WEBHOOK_URL}" ]]; then
    curl --silent --show-error --max-time 10 \
      -H "Content-Type: application/json" \
      -d "{\"ok\":false,\"failures\":${FAILURES},\"host\":\"$(hostname)\"}" \
      "${ALERT_WEBHOOK_URL}" >/dev/null || true
  fi
  exit 1
fi

echo "DGTEAM healthcheck passed"
