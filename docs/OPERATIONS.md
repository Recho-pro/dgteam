# DGTEAM Operations

## Path Truth

All Linux production runbooks in this repository assume one fixed root:

`/srv/dgteam`

Do not substitute `/opt/dgteam/current`.

## Core Services

- `dgteam-query.service`
- `dgteam-publish.service`
- `dgteam-wechat-official.service`
- `dgteam-wechat-official-worker.service`
- `dgteam-wechat-clawbot.service` (optional)
- `dgteam-github-runner.service` (trusted real-source gate)

## Useful Commands

### Service status

```bash
sudo systemctl status dgteam-query.service
sudo systemctl status dgteam-publish.service
sudo systemctl status dgteam-wechat-official.service
sudo systemctl status dgteam-wechat-official-worker.service
sudo systemctl status dgteam-github-runner.service
sudo systemctl status dgteam-prune.timer
sudo systemctl status dgteam-backup.timer
sudo systemctl status dgteam-healthcheck.timer
```

### Live logs

```bash
sudo tail -f /var/log/dgteam/query-api.log
sudo tail -f /var/log/dgteam/publish-api.log
sudo tail -f /var/log/dgteam/wechat-official.log
sudo tail -f /var/log/dgteam/wechat-official-worker.log
sudo tail -f /var/log/dgteam/github-runner.log
sudo tail -f /var/log/dgteam/prune.log
sudo tail -f /var/log/dgteam/healthcheck.log
```

### Health, audit, and cleanup

```bash
curl http://127.0.0.1:9765/health
curl http://127.0.0.1:9865/health
curl http://127.0.0.1:8975/health
curl https://dgtdnb.com/api/status
python /srv/dgteam/scripts/ops_runtime_audit.py --project-root /srv/dgteam --assert-ok
python /srv/dgteam/scripts/prune_storage.py --project-root /srv/dgteam --dry-run
/srv/dgteam/deploy/linux/bin/dgteam-prune.sh --dry-run
python /srv/dgteam/scripts/smoke_linked_chain.py --mode real-source
python /srv/dgteam/scripts/live_release_backfill.py --project-root /srv/dgteam --server-url http://127.0.0.1:9865 --query-base-url http://127.0.0.1:9765 --public-base-url https://dgtdnb.com
python /srv/dgteam/scripts/trusted_runner_preflight.py --project-root /srv/dgteam --workflow-path /srv/dgteam/.github/workflows/release_rehearsal.yml --runner-env-file /srv/dgteam/.runner.env --mode real-source --assert-gate-ready
/srv/dgteam/deploy/linux/bin/dgteam-runner-register.sh --project-root /srv/dgteam --workflow-path /srv/dgteam/.github/workflows/release_rehearsal.yml --runner-env-file /srv/dgteam/.runner.env --dry-run
```

The smoke rehearsal writes one auditable chain: release import/activate journals, release-scoped `query_ui` checks, WeChat consumer verification, rollback evidence, and the post-run ops audit snapshot.
The trusted runner preflight adds the workflow-side contract check before GitHub picks up the job: same gate commands, same source DB path, same labels, and the same preflight plus `gate_result.json` evidence shape.
The default real-source DB is `/srv/dgteam/runtime/cloud/current/dgteam.db`; use `--source-db` only when you intentionally override it, for example with a manually mirrored `/srv/dgteam/runtime/local/data/dgteam.db`.

## Health Interfaces

- query API: `GET /health`
- publish API: `GET /health`
- official account bridge: `GET /health`
- optional clawbot bridge: `GET /health`
- public query status: `GET /api/status`

## Timers And Closed Loop

The lowest-maintenance closed loop is now:

1. `dgteam-backup.timer`
   - runs daily at `03:30`
   - creates the backup archive before cleanup
2. `dgteam-prune.timer`
   - runs daily at `04:15`
   - clears residue and trims retention-bound runtime directories
3. `dgteam-healthcheck.timer`
   - runs every 5 minutes
   - checks local services, public status, and `ops_runtime_audit.py`
4. an external checker such as Uptime Kuma, Better Stack, or Healthchecks.io
   - confirms the public site and callback path are reachable from outside

Repository templates:

- `deploy/linux/systemd/dgteam-backup.service`
- `deploy/linux/systemd/dgteam-backup.timer`
- `deploy/linux/systemd/dgteam-prune.service`
- `deploy/linux/systemd/dgteam-prune.timer`
- `deploy/linux/systemd/dgteam-healthcheck.service`
- `deploy/linux/systemd/dgteam-healthcheck.timer`

## Alert Matrix

| Trigger | Source of truth | Default threshold | Severity | Operator action |
| --- | --- | --- | --- | --- |
| public `/api/status` unreachable | `deploy/linux/bin/dgteam-healthcheck.sh` | any failure | high | check nginx, query service, latest deployment, then rollback if needed |
| query/publish/wechat `/health` unreachable | `deploy/linux/bin/dgteam-healthcheck.sh` | any failure | high | inspect systemd status and logs, then deployment journal |
| disk usage high | `scripts/ops_runtime_audit.py` | `DGTEAM_DISK_WARN_PERCENT=80` | high | run prune dry-run, inspect `disk_governance`, clear non-critical artifacts, add capacity if headroom stays low |
| backup root missing | `scripts/ops_runtime_audit.py` | any missing `/var/backups/dgteam` | high | create `/var/backups/dgteam`, run manual backup once, then re-run audit |
| backup stale | `scripts/ops_runtime_audit.py` | `DGTEAM_MAX_BACKUP_AGE_HOURS=30` | high | check `dgteam-backup.timer`, create manual backup, then inspect `/var/backups/dgteam` |
| stale staging residue | `scripts/ops_runtime_audit.py` | `DGTEAM_MAX_STAGING_AGE_HOURS=6` | medium | inspect latest deployment journal first, then prune stale staging residue |
| stale upload residue | `scripts/ops_runtime_audit.py` | `DGTEAM_MAX_UPLOAD_AGE_HOURS=24` | medium | verify no in-flight import, then prune old upload artifacts |
| worker backlog high | `scripts/ops_runtime_audit.py` | `DGTEAM_MAX_WORKER_BACKLOG=10` | medium | inspect worker log and queue directories, then drain backlog if safe |
| failed worker tasks present | `scripts/ops_runtime_audit.py` | `DGTEAM_MAX_FAILED_TASKS=0` | medium | preserve failed payloads, inspect worker log, then retry or classify |
| recent deployment failed | `scripts/ops_runtime_audit.py` | latest failed journal | high | inspect `runtime/cloud/deployments/<id>`, confirm current/previous, rollback if necessary |
| trusted runner preflight fails | `scripts/trusted_runner_preflight.py` | any failed gate-ready check | high | fix workflow/env/source DB drift before retrying `workflow_dispatch` |
| trusted runner service inactive | `systemctl status dgteam-github-runner.service` | any inactive state after registration | medium | inspect `github-runner.log`, rerun registration, then restart service |

## Retention Policy

The same retention rules should drive local dry-runs and Linux timers. Keep them aligned through `/srv/dgteam/.env`.

| Path | Role | Automated rule | Safety rule |
| --- | --- | --- | --- |
| `runtime/cloud/current` | live release | never pruned by cleanup | only changed by deploy, rollback, or restore |
| `runtime/cloud/previous` | rollback safety net | never pruned by cleanup | do not delete manually during incidents |
| `runtime/cloud/releases` | short release history | keep latest `DGTEAM_KEEP_CLOUD_RELEASES` directories, default `1` | do not treat as the primary restore source |
| `runtime/cloud/staging` | ephemeral deploy workspace | remove entries older than `DGTEAM_MAX_STAGING_AGE_HOURS`, default `6h` | inspect deployment journal before cleanup |
| `runtime/cloud/uploads` | ingress residue | remove entries older than `DGTEAM_MAX_UPLOAD_AGE_HOURS`, default `24h` | verify no in-flight import before cleanup |
| `runtime/cloud/deployments` | deploy audit journal | keep latest `DGTEAM_KEEP_CLOUD_DEPLOYMENTS`, default `20` | journal is small; prefer backup plus trim, not full wipe |
| `runtime/local/automation/prod/state/last_run.json` | live checkpoint pointer | always keep | this is the local continuity anchor |
| `runtime/local/automation/prod/state/run_*.json` | collect/sync history | keep latest `DGTEAM_KEEP_AUTOMATION_RUN_STATES`, default `30` | prune only older run snapshots |
| `runtime/local/automation/prod/state/manual_sync_recovery_*.json` | manual recovery evidence | keep latest `DGTEAM_KEEP_AUTOMATION_RECOVERY_STATES`, default `10` | keep newest recovery breadcrumbs for audit |
| `runtime/local/integration_smoke` | rehearsal artifacts | keep latest `DGTEAM_KEEP_INTEGRATION_SMOKE_RUNS`, default `3` | keep at least the newest evidence while debugging release gates |

## Residue Cleanup

Formal Linux entrypoint:

```bash
/srv/dgteam/deploy/linux/bin/dgteam-prune.sh --dry-run
sudo /srv/dgteam/deploy/linux/bin/dgteam-prune.sh
```

Manual Python entrypoint:

```bash
python /srv/dgteam/scripts/prune_storage.py --project-root /srv/dgteam --dry-run
python /srv/dgteam/scripts/prune_storage.py --project-root /srv/dgteam
```

Failed-task archive entrypoint:

```bash
python /srv/dgteam/scripts/archive_wechat_failed_tasks.py --project-root /srv/dgteam --dry-run
python /srv/dgteam/scripts/archive_wechat_failed_tasks.py --project-root /srv/dgteam
```

Safe cleanup flow:

1. Run `ops_runtime_audit.py`.
2. If the warning is about staging or uploads, inspect the newest deployment journal first.
3. Run prune in `--dry-run` mode and review the JSON plan.
4. If the warning is `worker_failed_tasks`, archive failed payloads into `runtime/local/wechat_official/state/recognition/failed_archive/<timestamp>/` before deleting or replaying anything.
5. If the plan does not touch incident evidence you still need, run the real prune or the failed-task archive command.
6. Re-run `ops_runtime_audit.py`.

Never manually prune:

- `runtime/cloud/current`
- `runtime/cloud/previous`
- the newest deployment journal during an active incident

## Disk Governance

Current disk pressure should be treated in this order:

1. `ops_runtime_audit.py`
   - read `disk.used_percent`, `disk.free_gb`, and `disk_governance.largest_runtime_paths`
2. `dgteam-prune.sh --dry-run`
   - estimate reclaimable space without deleting anything
3. `dgteam-prune.sh`
   - remove retention-bound artifacts
4. manual action
   - if the warning remains, capacity work is needed because the largest paths are live data or rollback safety

Interpretation rule:

- If the space is mostly in `runtime/cloud/releases`, `runtime/cloud/uploads`, `runtime/cloud/staging`, `runtime/local/releases`, or `runtime/local/integration_smoke`, cleanup should help.
- If the space is mostly in `runtime/cloud/current`, `runtime/cloud/previous`, or `runtime/local/data/dgteam.db`, cleanup alone will not solve the problem; add capacity or archive data with a deliberate migration.

## Runbooks

### Query Anomaly

Symptoms:

- `https://dgtdnb.com/api/status` is down
- `/health` is healthy but search results are empty or clearly wrong
- UI loads but `/api/search` or `/api/sku` regressed after a release

Checklist:

1. `curl https://dgtdnb.com/api/status`
2. `curl http://127.0.0.1:9765/health`
3. `sudo systemctl status dgteam-query.service`
4. `python /srv/dgteam/scripts/ops_runtime_audit.py --project-root /srv/dgteam`
5. Inspect current/previous manifests:
   - `/srv/dgteam/runtime/cloud/current/manifest.json`
   - `/srv/dgteam/runtime/cloud/previous/manifest.json`
6. Confirm unknown API paths still return JSON: `curl -i https://dgtdnb.com/api/unknown-route` should be `404` with `error_code=unknown_api_endpoint`, not a static HTML fallback.
7. If the regression began after activation, use the rollback runbook first.

### Release Anomaly

Symptoms:

- publish API upload succeeds but activation fails
- `runtime/cloud/staging` is not empty after deploy
- `runtime/cloud/deployments/<id>/status.json` shows `failed`
- current switch succeeded but post-switch validation is wrong

Checklist:

1. `curl http://127.0.0.1:9865/api/status`
2. Inspect the newest deployment journal:
   - `/srv/dgteam/runtime/cloud/deployments/<latest>/status.json`
   - `/srv/dgteam/runtime/cloud/deployments/<latest>/events.jsonl`
3. Confirm whether `runtime/cloud/current` or `runtime/cloud/previous` is the last good release.
4. Run rollback if needed:

```bash
curl -X POST -H "X-DGTEAM-Token: <publish-token>" http://127.0.0.1:9865/api/releases/rollback
```

5. Re-check `/health`, public `/api/status`, and `ops_runtime_audit.py`.
6. If the release only fails inside GitHub Actions, inspect `dgteam-github-runner.service` and confirm `trusted-runner-preflight.json` plus `trusted-runner-registration.json` were uploaded as artifacts.
7. If activation succeeded but `current` or `previous` still lacks `query_ui/asset-manifest.json`, run `python /srv/dgteam/scripts/live_release_backfill.py --project-root /srv/dgteam --server-url http://127.0.0.1:9865 --query-base-url http://127.0.0.1:9765 --public-base-url https://dgtdnb.com` so live `current`, `previous`, and rollback all stay on the standard release contract.

### Worker Anomaly

Symptoms:

- official account worker service is active but images never finish
- `runtime/local/wechat_official/state/recognition/queued` keeps growing
- `failed` directory starts accumulating tasks

Checklist:

1. `sudo systemctl status dgteam-wechat-official-worker.service`
2. `python /srv/dgteam/scripts/ops_runtime_audit.py --project-root /srv/dgteam`
3. Inspect queue directories:
   - `runtime/local/wechat_official/state/recognition/queued`
   - `runtime/local/wechat_official/state/recognition/processing`
   - `runtime/local/wechat_official/state/recognition/failed`
4. Inspect worker log:

```bash
sudo tail -f /var/log/dgteam/wechat-official-worker.log
```

5. Preserve failed payloads before manual cleanup or replay.
6. Archive handled failures with:

```bash
python /srv/dgteam/scripts/archive_wechat_failed_tasks.py --project-root /srv/dgteam
```

### Callback Anomaly

Symptoms:

- WeChat callback retries spike
- verification or decrypt path fails
- passive reply succeeds inconsistently
- callback dedupe/session state looks corrupted

Checklist:

1. `curl http://127.0.0.1:8975/health`
2. `sudo systemctl status dgteam-wechat-official.service`
3. Inspect callback/session state:
   - `runtime/local/wechat_official/state/callback_dedupe`
   - `runtime/local/wechat_official/state/sessions`
   - `runtime/local/wechat_official/state/trace`
4. Inspect bridge log:

```bash
sudo tail -f /var/log/dgteam/wechat-official.log
```

5. If callbacks are duplicating, preserve the state directory before manual cleanup so replay analysis remains possible.

### Trusted Runner Anomaly

Symptoms:

- GitHub `workflow_dispatch` for `release_rehearsal.yml` stays queued
- the self-hosted job is picked up but fails before `candidate-gate-real-source`
- `trusted-runner-preflight.json` shows `gate_ready=false`

Checklist:

1. `sudo systemctl status dgteam-github-runner.service`
2. `sudo tail -f /var/log/dgteam/github-runner.log`
3. `python /srv/dgteam/scripts/trusted_runner_preflight.py --project-root /srv/dgteam --workflow-path /srv/dgteam/.github/workflows/release_rehearsal.yml --runner-env-file /srv/dgteam/.runner.env --mode real-source --assert-gate-ready`
4. `sudo /srv/dgteam/deploy/linux/bin/dgteam-runner-register.sh --project-root /srv/dgteam --workflow-path /srv/dgteam/.github/workflows/release_rehearsal.yml --runner-env-file /srv/dgteam/.runner.env --dry-run`
5. If the source DB truth changed, update `/srv/dgteam/.runner.env` so `DGTEAM_TRUSTED_RUNNER_SOURCE_DB` still points at `/srv/dgteam/runtime/cloud/current/dgteam.db`, rerun registration, then retry the workflow.

## Real /srv Rollout Checklist

If you cannot access the real server from the current workstation, treat this as the exact on-host procedure:

```bash
sudo cp /srv/dgteam/deploy/linux/systemd/dgteam-prune.service /etc/systemd/system/
sudo cp /srv/dgteam/deploy/linux/systemd/dgteam-prune.timer /etc/systemd/system/
sudo chmod +x /srv/dgteam/deploy/linux/bin/dgteam-prune.sh
sudo systemctl daemon-reload
sudo systemctl enable --now dgteam-prune.timer
sudo /srv/dgteam/deploy/linux/bin/dgteam-prune.sh --dry-run
sudo /srv/dgteam/deploy/linux/bin/dgteam-prune.sh
python /srv/dgteam/scripts/ops_runtime_audit.py --project-root /srv/dgteam
```

If the post-prune audit still reports `disk_usage_high`, treat it as a capacity or data-archival task, not as a missing prune task.
