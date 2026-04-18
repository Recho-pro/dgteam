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
LEGACY_ROOT="${DGTEAM_LEGACY_ROOT:-}"
KEEP_LOCAL_RELEASES="${DGTEAM_KEEP_LOCAL_RELEASES:-1}"
KEEP_LOCAL_RELEASE_ARCHIVES="${DGTEAM_KEEP_LOCAL_RELEASE_ARCHIVES:-0}"
KEEP_INTEGRATION_SMOKE_RUNS="${DGTEAM_KEEP_INTEGRATION_SMOKE_RUNS:-3}"
KEEP_CLOUD_RELEASES="${DGTEAM_KEEP_CLOUD_RELEASES:-1}"
KEEP_CLOUD_ROLLBACKS="${DGTEAM_KEEP_CLOUD_ROLLBACKS:-1}"
KEEP_CLOUD_DEPLOYMENTS="${DGTEAM_KEEP_CLOUD_DEPLOYMENTS:-20}"
KEEP_AUTOMATION_RUN_STATES="${DGTEAM_KEEP_AUTOMATION_RUN_STATES:-30}"
KEEP_AUTOMATION_RECOVERY_STATES="${DGTEAM_KEEP_AUTOMATION_RECOVERY_STATES:-10}"
MAX_STAGING_AGE_HOURS="${DGTEAM_MAX_STAGING_AGE_HOURS:-6}"
MAX_UPLOAD_AGE_HOURS="${DGTEAM_MAX_UPLOAD_AGE_HOURS:-24}"
KEEP_CLOUD_UPLOADS="${DGTEAM_KEEP_CLOUD_UPLOADS:-false}"
PRUNE_DRY_RUN="${DGTEAM_PRUNE_DRY_RUN:-false}"

ARGS=(
  --project-root "${PROJECT_ROOT}"
  --keep-local-releases "${KEEP_LOCAL_RELEASES}"
  --keep-local-release-archives "${KEEP_LOCAL_RELEASE_ARCHIVES}"
  --keep-integration-smoke "${KEEP_INTEGRATION_SMOKE_RUNS}"
  --keep-cloud-releases "${KEEP_CLOUD_RELEASES}"
  --keep-cloud-rollbacks "${KEEP_CLOUD_ROLLBACKS}"
  --keep-cloud-deployments "${KEEP_CLOUD_DEPLOYMENTS}"
  --keep-automation-run-states "${KEEP_AUTOMATION_RUN_STATES}"
  --keep-automation-recovery-states "${KEEP_AUTOMATION_RECOVERY_STATES}"
  --max-staging-age-hours "${MAX_STAGING_AGE_HOURS}"
  --max-upload-age-hours "${MAX_UPLOAD_AGE_HOURS}"
)

if [[ -n "${LEGACY_ROOT}" ]]; then
  ARGS+=(--legacy-root "${LEGACY_ROOT}")
fi

if [[ "${KEEP_CLOUD_UPLOADS}" == "true" ]]; then
  ARGS+=(--keep-cloud-uploads)
fi

if [[ "${PRUNE_DRY_RUN}" == "true" ]]; then
  ARGS+=(--dry-run)
fi

if [[ "${1:-}" == "--dry-run" ]]; then
  ARGS+=(--dry-run)
fi

"${PYTHON_BIN}" "${PROJECT_ROOT}/scripts/prune_storage.py" "${ARGS[@]}"
