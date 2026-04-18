# DGTEAM Rollback

## Fast Rollback

The safest rollback path is the publish API rollback flow, not manual file copying.

Use:

```bash
curl -X POST \
  -H "X-DGTEAM-Token: <publish-token>" \
  http://127.0.0.1:9865/api/releases/rollback
```

This restores the previous live release atomically. The release directory now includes the `query_ui` asset bundle, so rollback restores the matching frontend files and data snapshot together.

## When To Use Rollback

Use release rollback when:

- a new publish completed but query results are clearly wrong
- the new release passed import but failed under real traffic
- post-switch validation or public `api/status` regressed after activation

## When To Use Restore Instead

Use full restore when:

- both `current` and `previous` are bad
- the database file is corrupted
- release metadata is missing or broken
- you need to recover deployment evidence or automation checkpoint files from backup media

## Rollback Checklist

1. Check which release is live:

```bash
curl http://127.0.0.1:9865/api/status
```

2. Inspect the latest deployment journal before you switch anything:

- `/srv/dgteam/runtime/cloud/deployments/<latest>/status.json`
- `/srv/dgteam/runtime/cloud/deployments/<latest>/events.jsonl`

3. Trigger rollback:

```bash
curl -X POST \
  -H "X-DGTEAM-Token: <publish-token>" \
  http://127.0.0.1:9865/api/releases/rollback
```

4. Collect the three rollback evidence objects before and after the switch:

- the candidate `smoke_report.json`
- the deployment journal under `runtime/cloud/deployments/<deployment_or_rollback_id>/`
- the archived rollback evidence directory under `runtime/cloud/releases/rolled_back_<timestamp>`

5. Re-check query health, public status, and runtime audit:

```bash
curl http://127.0.0.1:9765/health
curl https://dgtdnb.com/api/status
python /srv/dgteam/scripts/ops_runtime_audit.py --project-root /srv/dgteam
```

6. Confirm the query page and public callbacks are normal again.

The query page must load `index.html` from `runtime/cloud/current/query_ui`, and its `app.js` / `styles.css` URLs should carry the restored asset version from `asset-manifest.json`.

## Rollback And Cleanup Safety

Prune and rollback are intentionally separate concerns.

Prune may trim:

- old `runtime/cloud/releases`
- old `runtime/cloud/deployments`
- stale `runtime/cloud/staging`
- stale `runtime/cloud/uploads`

Prune must never touch:

- `runtime/cloud/current`
- `runtime/cloud/previous`

Incident rule:

- if you are still deciding whether to rollback, do not run cleanup first
- inspect the deployment journal, rollback if needed, and only then clean stale residue

## Candidate Rehearsal

Fixture gate for hosted CI or manual checks without a live source DB:

```bash
python /srv/dgteam/scripts/smoke_linked_chain.py --fixture
```

Real-source gate for a trusted local or self-hosted runner with a readable source DB snapshot:

```bash
python /srv/dgteam/scripts/smoke_linked_chain.py --mode real-source
```

By default this gate samples `/srv/dgteam/runtime/cloud/current/dgteam.db`, which is the same active release DB that query consumers read after activation. Pass `--source-db` only when you intentionally override that truth.

Both gates share the same blocking conditions:

- release import or activation fails
- query status is not ok after activation
- required smoke searches return zero results
- detail queries return no branches or capacity groups
- rollback validation is not ok

Both gates also share the same rollback decision boundary:

- bundle B activated but query checks regressed
- post-switch validation regressed after the second activation
- the rollback journal or rollback validation is not healthy

Important boundary:

- this rehearsal covers release bundle build, publish/import, activate, query checks, and rollback
- the resulting evidence should always include `smoke_report.json`, the deployment journal, and rollback evidence together
- it does not prove real `systemd`, `nginx`, or production disk behavior on `/srv/dgteam` unless you run it on a machine that actually hosts those paths

## Operational Rule

Do not overwrite `runtime/cloud/current` by hand during an incident unless rollback and restore are both unavailable.

The whole point of the release store is to keep that path atomic.
