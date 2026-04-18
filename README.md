# DGTEAM

DGTEAM is the standalone architecture for the market data platform.

It is organized around two clear halves:

1. A local agent that logs in, crawls, OCRs, cleans, and computes market outputs.
2. A cloud SaaS layer that receives release bundles, switches versions, and serves query traffic.

## Architecture

- `src/dgteam/agent`
  - Local collection pipeline, scheduler hooks, bundle publishing.
- `src/dgteam/release`
  - Release manifest, bundle builder, version metadata.
- `src/dgteam/publish_api`
  - Cloud-side release upload and version switching API.
- `src/dgteam/query_api`
  - Read-only query API for search, status, and model detail.
- `src/dgteam/integrations/wechat_clawbot`
- WeChat clawbot bridge, event intake, and future command mapping layer.
- `src/dgteam/integrations/wechat_official`
- WeChat Official Account bridge, passive reply logic, and custom menu tooling.
- `src/dgteam/core`
  - Shared config, paths, models, logging, text I/O, and encoding guardrails.
- `web`
  - Query frontend shell and operator entry pages.
- `scripts`
  - Operator-friendly local and cloud entry scripts.
- `docs`
  - Architecture, migration, and deployment notes.

## Design Principles

- Local agent owns source-site login and data production.
- Cloud services only accept validated release bundles.
- Query service is read-only and always points to the current release.
- Every release is versioned and rollback-safe.
- External bot integrations stay behind explicit adapters instead of touching core logic directly.
- The project name is `dgteam`.

## Current Status

This repository now contains the full DGTEAM application frame.

Already migrated and wired into DGTEAM:

- storage layer
- project config loader
- live market publish logic
- crawler entrypoint
- rules and cleaning logic
- market engine
- query API server
- query frontend assets
- WeChat clawbot integration boundary
- publish API status and version switching flow
- local release bundle builder
- cloud release store
- storage retention
- encoding guard and repair scripts

## Quick Start

```powershell
cd C:\Users\somehow\Documents\Playground\dgteam
python -m pip install -e .[dev]
python -m compileall src
python -m dgteam.query_api.app --host 127.0.0.1 --port 8875
```

When dependencies are installed:

```powershell
python -m dgteam.agent.main --help
python -m dgteam.query_api.app --help
python -m dgteam.publish_api.app --help
python -m dgteam.integrations.wechat_clawbot.app --help
python -m dgteam.integrations.wechat_official.app --help
python -m dgteam.integrations.wechat_official.menu_cli --help
```

## Developer Bootstrap And Quality Gate

Use the editable-install workflow for local development so imports, tests, and CLI entrypoints do not depend on a hand-set `PYTHONPATH`.

Bootstrap once in a clean checkout:

```powershell
cd C:\Users\somehow\Documents\Playground\dgteam
python -m pip install -e .[dev]
```

Recommended developer commands:

```powershell
dgteam-dev install-ui-browser
dgteam-dev quality
dgteam-dev test
dgteam-dev test-ui
dgteam-dev ops-audit
dgteam-dev smoke-linked-chain
dgteam-dev smoke-linked-chain-real-source
dgteam-dev candidate-gate
dgteam-dev candidate-gate-fixture
dgteam-dev candidate-gate-real-source
dgteam-dev ci
```

If the console script is not on your shell path yet, the module entrypoint is equivalent:

```powershell
python -m dgteam.dev_cli install-ui-browser
python -m dgteam.dev_cli quality
python -m dgteam.dev_cli test
python -m dgteam.dev_cli test-ui
python -m dgteam.dev_cli ops-audit
python -m dgteam.dev_cli smoke-linked-chain
python -m dgteam.dev_cli smoke-linked-chain-real-source
python -m dgteam.dev_cli candidate-gate
python -m dgteam.dev_cli candidate-gate-fixture
python -m dgteam.dev_cli candidate-gate-real-source
python -m dgteam.dev_cli ci
```

Command contract:

- `dgteam-dev install-ui-browser`
  - runs `python -m playwright install chromium`
- `dgteam-dev quality`
  - runs `python -m compileall src`
  - runs `python scripts/check_encoding.py`
- `dgteam-dev test`
  - runs `python -m pytest -q`
- `dgteam-dev test-ui`
  - runs `python -m pytest -q tests/test_query_ui_e2e.py`
- `dgteam-dev ops-audit`
  - runs `python scripts/ops_runtime_audit.py --project-root <repo>`
- `dgteam-dev smoke-linked-chain`
  - runs `python scripts/smoke_linked_chain.py --fixture`
- `dgteam-dev smoke-linked-chain-real-source`
  - runs `python scripts/smoke_linked_chain.py --mode real-source`
  - defaults to the active release DB at `runtime/cloud/current/dgteam.db`
  - uses `DGTEAM_SMOKE_SOURCE_DB` only when you want to override that default, for example with `runtime/local/data/dgteam.db`
- `dgteam-dev candidate-gate`
  - alias for `dgteam-dev candidate-gate-fixture`
- `dgteam-dev candidate-gate-fixture`
  - runs `dgteam-dev quality`
  - runs release/operations contract tests
  - runs `python scripts/smoke_linked_chain.py --fixture`
- `dgteam-dev candidate-gate-real-source`
  - runs `dgteam-dev quality`
  - runs the same release/operations contract tests
  - runs `python scripts/smoke_linked_chain.py --mode real-source`
- `dgteam-dev ci`
  - runs the full minimal local quality gate in the same order as CI

Notes:

- `tests/conftest.py` now adds `src` to `sys.path` during pytest startup, so test collection no longer depends on manually exporting `PYTHONPATH`.
- Editable install is still the recommended baseline because it also makes package CLI entrypoints work consistently.
- GitHub Actions now installs Playwright Chromium and runs the browser baseline explicitly under `.github/workflows/ci.yml`, so local and hosted verification cover the same `query_ui` path.

## Operations Audit And Release Rehearsal

Round 17 adds two operator-facing guardrails:

- `scripts/ops_runtime_audit.py`
  - audits disk pressure, backup freshness, stale `staging` / `uploads`, deployment journal residue, and WeChat worker backlog
  - classifies `runtime/cloud/current` and `runtime/cloud/previous` as backup scope
  - classifies `runtime/cloud/staging` and `runtime/cloud/uploads` as audit-only ephemeral state
  - classifies `runtime/cloud/deployments` and `runtime/local/automation/prod/state` as small but important audit/checkpoint state
- `scripts/prune_storage.py`
  - now supports a real `--dry-run` plan instead of an empty stub
  - trims retention-bound local/cloud artifacts, stale staging/uploads residue, older deployment journals, and older automation checkpoint files
- `scripts/smoke_linked_chain.py`
  - now treats `runtime/cloud/current/dgteam.db` as the real-source default on both local and `/srv/dgteam`
  - still supports a high-fidelity explicit override against `runtime/local/data/dgteam.db` when you want to rehearse unpublished working data
  - now also supports `--fixture` so nightly or candidate smoke gates can run without a live source database
  - uses the same `runtime/local` and `runtime/cloud` layout as the real repo, then verifies `release -> activate -> query_ui -> WeChat consumer -> rollback -> ops audit`

Recommended commands:

```powershell
python scripts/ops_runtime_audit.py --project-root C:\Users\somehow\Documents\Playground\dgteam
python scripts/prune_storage.py --project-root C:\Users\somehow\Documents\Playground\dgteam --dry-run
python scripts/smoke_linked_chain.py
python scripts/smoke_linked_chain.py --fixture
python scripts/smoke_linked_chain.py --mode real-source
python scripts/smoke_linked_chain.py --mode real-source --source-db C:\Users\somehow\Documents\Playground\dgteam\runtime\local\data\dgteam.db
dgteam-dev candidate-gate
dgteam-dev candidate-gate-real-source
```

For Linux `/srv/dgteam` deployments, pair the backup timer, prune timer, and healthcheck timer so the same retention/audit contract applies locally and on the server.

Candidate gate split:

- fixture gate
  - intended for hosted CI and manual checks when a live source DB is unavailable
  - runs the same smoke contract with synthetic source data and still validates the release-scoped `query_ui`, rollback evidence, unknown `/api/*` JSON contract, and WeChat consumer path
- real-source gate
  - intended for trusted local or self-hosted runners with a readable source DB snapshot
  - uses the same report schema and audit chain, but points at a real SQLite source

The repository now also ships a dedicated GitHub Actions workflow at `.github/workflows/release_rehearsal.yml` for nightly/manual candidate rehearsal. The hosted workflow runs the fixture gate, while the self-hosted `dgteam-trusted` workflow path runs the real-source gate and uploads the same `smoke_report.json` plus `gate_result.json` evidence shape. Both reports link the smoke run, deployment journals, and rollback evidence with the same field names.

Trusted runner rollout now has one repo-managed contract:

- `scripts/trusted_runner_preflight.py`
  - validates that the workflow, source DB, runner labels, and artifact names still match the release gate contract
- `scripts/trusted_runner_register.py`
  - downloads the official GitHub Actions runner, prepares `/srv/dgteam/runtime/cloud/github_runner`, and configures the runner when a short-lived registration token is supplied
- `deploy/linux/env/dgteam.runner.env.example`
  - stores the persistent runner identity and the current trusted real-source DB path
- `deploy/linux/systemd/dgteam-github-runner.service`
  - keeps the registered runner alive after bootstrap

Recommended Linux runner commands:

```bash
python /srv/dgteam/scripts/trusted_runner_preflight.py --project-root /srv/dgteam --workflow-path /srv/dgteam/.github/workflows/release_rehearsal.yml --runner-env-file /srv/dgteam/.runner.env --mode real-source --assert-gate-ready
sudo /srv/dgteam/deploy/linux/bin/dgteam-runner-register.sh --project-root /srv/dgteam --workflow-path /srv/dgteam/.github/workflows/release_rehearsal.yml --runner-env-file /srv/dgteam/.runner.env --dry-run
sudo systemctl status dgteam-github-runner.service
```

## Query UI Frontend Baseline

`web/query_ui` still ships as static assets served by `query_api`, but it now uses server-rendered asset fingerprints instead of hand-maintained `?v=` strings in the checked-in HTML template.

- `index.html` is rendered with a content hash for `app.js` and `styles.css`.
- `index.html` stays `no-store`, while versioned JS/CSS stay immutable.
- New release bundles include `query_ui/index.html`, `query_ui/app.js`, `query_ui/styles.css`, and `query_ui/asset-manifest.json`.
- When `DGTEAM_DB_PATH` points at `runtime/cloud/current/dgteam.db`, `query_api` serves assets from `runtime/cloud/current/query_ui`; local development falls back to `web/query_ui`.
- Release activation and rollback switch the UI assets with the data snapshot, so `current`, `previous`, and rollback evidence carry one matched data-plus-frontend contract.
- If a live host still runs a legacy release without `runtime/cloud/current/query_ui/asset-manifest.json`, run `python /srv/dgteam/scripts/live_release_backfill.py --project-root /srv/dgteam --server-url http://127.0.0.1:9865 --query-base-url http://127.0.0.1:9765 --public-base-url https://dgtdnb.com` to rebuild the active DB into two standard releases, prove rollback, and finish with standard `current` plus `previous`.
- Snapshot refinement is now backend-owned through `/api/sku?refinement_query=...`; `app.js` no longer keeps its own local narrowing implementation.
- Unknown `GET /api/*` routes return the same JSON 404 contract locally and on `/srv/dgteam`; they must not fall back to static HTML 404 responses.
- The phase 3 Query API cleanup keeps search orchestration in `search_pipeline.py`, candidate scoring in `search_ranking.py`, branch and capacity assembly in `branch_assembly.py`, HTTP runtime handling in `http_runtime.py`, and snapshot error/refinement assembly in `snapshot_assembly.py` while preserving `QueryApp` compatibility for the UI and WeChat consumers.

To run the browser baseline locally the first time:

```powershell
dgteam-dev install-ui-browser
dgteam-dev test-ui
```

## Local Production Flow

Formal production collect-and-sync now has a single project-level entrypoint:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_collect_and_sync.ps1
```

Its config home is:

- `config/automation/prod/auth.json`
- `config/automation/prod/profile.env`
- `config/automation/prod/sync.env`

Its runtime now writes directly into the main project state:

- `runtime/local`
- `runtime/cloud`

Build a release bundle from the current database:

```powershell
python -m dgteam.agent.main pipeline --mode publish
```

Run a dry run and inspect stage readiness:

```powershell
python -m dgteam.agent.main pipeline --mode dry-run
```

Run the crawler first, then build a release bundle in one flow:

```powershell
python -m dgteam.agent.main pipeline --mode collect-and-publish -- --brand-filter 苹果
```

## Storage Retention

DGTEAM now prunes runtime artifacts automatically so repeated full-data runs do not keep stacking multi-hundred-megabyte copies forever.

- Local publish keeps only the newest local release directory by default.
- Local release zip archives are skipped entirely by default during publish.
- Integration smoke folders keep the newest three runs by default so candidate-release evidence is not wiped immediately.
- Cloud activation keeps rollback safety while pruning older release history and upload leftovers.
- Operational cleanup can also trim older deployment journals and older automation checkpoint snapshots without touching `current`, `previous`, or `last_run.json`.

Retention can be tuned with environment variables in `.env`:

```env
DGTEAM_RETENTION_ENABLED=true
DGTEAM_KEEP_LOCAL_RELEASES=1
DGTEAM_KEEP_LOCAL_RELEASE_ARCHIVES=0
DGTEAM_KEEP_INTEGRATION_SMOKE_RUNS=3
DGTEAM_KEEP_CLOUD_RELEASES=1
DGTEAM_KEEP_CLOUD_ROLLBACKS=1
DGTEAM_PRUNE_CLOUD_UPLOADS=true
DGTEAM_KEEP_CLOUD_DEPLOYMENTS=20
DGTEAM_KEEP_AUTOMATION_RUN_STATES=30
DGTEAM_KEEP_AUTOMATION_RECOVERY_STATES=10
DGTEAM_MAX_STAGING_AGE_HOURS=6
DGTEAM_MAX_UPLOAD_AGE_HOURS=24
```

Manual cleanup:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_prune_storage.ps1
```

## WeChat Official Account

DGTEAM now includes a first-class WeChat Official Account integration:

- secure-mode callback verification
- encrypted callback handling
- passive text replies for model queries
- default custom menu publishing for the live query site

Useful local entry points:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_wechat_official_bridge.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\sync_wechat_official_menu.ps1 --show-default
powershell -ExecutionPolicy Bypass -File .\scripts\sync_wechat_official_menu.ps1 --publish-default --base-url https://dgtdnb.com
```

## Encoding Guard

DGTEAM now ships with a repository-level UTF-8 guardrail to stop source mojibake from drifting back in.

- `.editorconfig` forces UTF-8 as the default text encoding.
- `.gitattributes` keeps text files normalized in Git.
- `src/dgteam/core/textio.py` provides the shared UTF-8 read and write helpers.
- `scripts/check_encoding.py` scans the codebase for invalid UTF-8, suspicious mojibake tokens, and text file reads or writes that omit an explicit encoding.
- `scripts/fix_encoding.py` can dry-run or apply safe repairs for common mojibake cases.
- Publish now runs the encoding guard before building a release bundle, so broken source text is blocked before it can be shipped.

Manual checks:

```powershell
python .\scripts\check_encoding.py
python .\scripts\fix_encoding.py --json
dgteam-dev quality
```

To wire the guard into daily commits:

```powershell
python -m pip install -e .[dev]
pre-commit install
```

## Sync To Cloud

Use the one-step sync command when you want the local machine to publish the newest release and push it to the cloud in a single flow.
In production the publish API template binds to `127.0.0.1:9865`, so call it from the server itself, through SSH port forwarding, or through an explicitly protected internal route.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\sync_release.ps1 `
  --server-url http://127.0.0.1:9865 `
  --token your-token
```

Register the fixed daily schedule:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\register_collect_and_sync_task.ps1
```

Remove the scheduled task:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\unregister_collect_and_sync_task.ps1
```

What it does:

- runs the encoding guard first
- builds the newest local release
- creates a temporary upload archive
- uploads it to the publish API
- deletes the temporary archive after upload

If you already have a release directory and only want to sync that directory:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\sync_release.ps1 `
  --server-url http://127.0.0.1:9865 `
  --token your-token `
  --release-dir .\runtime\local\releases\release_2026-04-15T07-04-48 `
  --release-id release_2026-04-15T07-04-48
```

If the live host already has a legacy `current` release that predates bundled `query_ui`, standardize it in place with:

```bash
python /srv/dgteam/scripts/live_release_backfill.py \
  --project-root /srv/dgteam \
  --server-url http://127.0.0.1:9865 \
  --query-base-url http://127.0.0.1:9765 \
  --public-base-url https://dgtdnb.com
```

That flow builds two standard bundles from the active release DB, activates them in sequence so `previous` is also standard, proves rollback, then re-activates the final standard release so live `current`, `previous`, and rollback evidence no longer depend on query-ui fallback compatibility.

## Publish API

Start the publish API:

```powershell
python -m dgteam.publish_api.app --host 127.0.0.1 --port 9865
```

The publish API supports:

- `GET /health`
- `GET /api/status`
- `GET /api/releases/validate?release_id=...`
- `GET /api/deployments/status`
- `POST /api/releases/deploy`
- `POST /api/releases/upload`
- `POST /api/releases/import-local`
- `POST /api/releases/activate`
- `POST /api/releases/rollback`

Prefer `upload` plus `activate` when you want an auditable two-step deployment. Use `deploy` only when the caller needs a single request to upload, validate, switch `current`, and run post-switch validation.

## Production Deployment

This repository now ships with a complete Linux deployment kit under:

- `deploy/linux/env`
- `deploy/linux/systemd`
- `deploy/linux/nginx`
- `deploy/linux/logrotate`
- `deploy/linux/bin`

The production path used by the templates is `/srv/dgteam`; runtime data lives under `/srv/dgteam/runtime/cloud`.

Recommended reading order:

- [Production Deployment](C:\Users\somehow\Documents\Playground\dgteam\docs\PRODUCTION_DEPLOYMENT.md)
- [Backup And Restore](C:\Users\somehow\Documents\Playground\dgteam\docs\BACKUP_AND_RESTORE.md)
- [Rollback](C:\Users\somehow\Documents\Playground\dgteam\docs\ROLLBACK.md)
- [Operations](C:\Users\somehow\Documents\Playground\dgteam\docs\OPERATIONS.md)
