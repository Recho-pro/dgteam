# DGTEAM Backup And Restore

## Path Truth

All Linux production examples in this document assume:

`/srv/dgteam`

Not `/opt/dgteam/current`.

## Backup Scope

The production backup entrypoint is:

`/srv/dgteam/deploy/linux/bin/dgteam-backup.sh`

The script creates a timestamped tarball under:

`/var/backups/dgteam`

If the directory does not exist yet, create it once before enabling the timer:

```bash
sudo mkdir -p /var/backups/dgteam
```

It currently captures:

- `/srv/dgteam/runtime/cloud/current/dgteam.db`
- `/srv/dgteam/runtime/cloud/current/manifest.json`
- `/srv/dgteam/runtime/cloud/current/release.json`
- `/srv/dgteam/runtime/cloud/current/summary.json`
- `/srv/dgteam/runtime/cloud/current/market_v1_snapshot.csv`
- `/srv/dgteam/runtime/cloud/current/market_v1_clusters.csv`
- `/srv/dgteam/runtime/cloud/previous`
- `/srv/dgteam/runtime/cloud/deployments`
- `/srv/dgteam/runtime/local/automation/prod/state` if present
- `/srv/dgteam/.env` if present

## Backup vs Audit Classification

| Path | Classification | Rule |
| --- | --- | --- |
| `runtime/cloud/current` | backup | primary live restore source |
| `runtime/cloud/previous` | backup | first rollback safety net |
| `runtime/cloud/releases` | retention + audit | keep short history, do not use as the primary restore path |
| `runtime/cloud/staging` | audit only | should normally be empty between deploys |
| `runtime/cloud/uploads` | audit only | ingress residue, should age out automatically |
| `runtime/cloud/deployments` | audit + light backup | preserve journals for incident reconstruction |
| `runtime/local/automation/prod/state` | checkpoint backup + audit | preserve local continuity markers, but do not treat them as cloud live restore payload |
| `runtime/local/wechat_official/state` | audit only | useful for replay analysis, but not part of the current backup restore payload |

## Backup Schedule

The repository includes:

- `deploy/linux/systemd/dgteam-backup.service`
- `deploy/linux/systemd/dgteam-backup.timer`

The default timer runs once per day at `03:30`.

Recommended companion timer:

- `deploy/linux/systemd/dgteam-prune.service`
- `deploy/linux/systemd/dgteam-prune.timer`

The default prune timer runs at `04:15`, after backup creation, so cleanup never removes the newest evidence before it is copied into `/var/backups/dgteam`.

## Manual Backup

```bash
sudo /srv/dgteam/deploy/linux/bin/dgteam-backup.sh
```

Default output:

```text
/var/backups/dgteam/backup_YYYYmmdd_HHMMSS.tar.gz
```

## Manual Restore

Use a backup directory or a tarball:

```bash
sudo /srv/dgteam/deploy/linux/bin/dgteam-restore.sh /var/backups/dgteam/backup_20260416_033000.tar.gz
```

The restore script:

1. Validates `dgteam.db` with `PRAGMA quick_check`.
2. Stops the DGTEAM services if they exist.
3. Moves the current live directory to `runtime/cloud/previous`.
4. Restores the backup into `runtime/cloud/current`.
5. Starts the services again.

Current restore scope is intentionally narrow:

- it restores the live release payload
- it does not automatically replay `runtime/cloud/deployments`
- it does not automatically replay `runtime/local/automation/prod/state`

Those audit/checkpoint directories stay in the backup tarball and can be extracted explicitly when incident recovery needs them.

Audit extraction entrypoints:

```bash
sudo /srv/dgteam/deploy/linux/bin/dgteam-restore.sh \
  --restore-deployments-to /srv/dgteam/runtime/cloud/restore_evidence/incident_001/deployments \
  --restore-automation-state-to /srv/dgteam/runtime/cloud/restore_evidence/incident_001/automation_state \
  /var/backups/dgteam/backup_20260416_033000.tar.gz
```

Contract decision:

- live restore keeps a narrow payload and remains the default
- deployment journals and automation checkpoints are audit evidence, not auto-replayed runtime state
- if operators need them, extract them into a dedicated evidence directory instead of writing them back into live runtime paths

## Cleanup And Backup Interaction

Prune is allowed to remove only retention-bound artifacts:

- old `runtime/cloud/releases`
- old `runtime/cloud/deployments`
- stale `runtime/cloud/staging`
- stale `runtime/cloud/uploads`
- old `runtime/local/automation/prod/state/run_*.json`
- old `runtime/local/automation/prod/state/manual_sync_recovery_*.json`

Prune must not remove:

- `runtime/cloud/current`
- `runtime/cloud/previous`
- `runtime/local/automation/prod/state/last_run.json`

Operational rule:

1. create or verify the newest backup
2. inspect `ops_runtime_audit.py`
3. run prune in `--dry-run`
4. run real prune only after the dry-run matches the intended retention policy

## Post-Restore Verification

Run these checks in order:

```bash
curl http://127.0.0.1:9765/health
curl http://127.0.0.1:9865/health
curl https://dgtdnb.com/api/status
python /srv/dgteam/scripts/ops_runtime_audit.py --project-root /srv/dgteam
sudo systemctl status dgteam-query.service
sudo systemctl status dgteam-publish.service
sudo systemctl status dgteam-wechat-official.service
sudo systemctl status dgteam-wechat-official-worker.service
```

## Practical Rule

Use release rollback first.

Use file-level restore only when:

- both `current` and `previous` are damaged
- the wrong release already overwrote rollback safety
- the database file itself is corrupted
- you need to recover deployment evidence or automation checkpoints from backup media
