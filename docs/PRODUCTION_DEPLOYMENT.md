# DGTEAM Production Deployment

## Recommended layout

Use one fixed layout on every Linux server:

```text
/srv/dgteam
/srv/dgteam/.venv
/srv/dgteam/.env
/var/log/dgteam
/var/backups/dgteam
```

The repository should live at `/srv/dgteam`.  
The Python virtual environment should live inside the repo at `/srv/dgteam/.venv`.

## 1. Prepare the host

```bash
sudo mkdir -p /srv/dgteam /var/log/dgteam /var/backups/dgteam
```

Current `/srv/dgteam` production truth runs the services as `root`, reads runtime env from `/srv/dgteam/.env`, and binds query and publish on `127.0.0.1:9765` and `127.0.0.1:9865`.

## 2. Upload the project

```bash
sudo rsync -a --delete /path/to/dgteam/ /srv/dgteam/
cd /srv/dgteam
```

## 3. Create the virtual environment

```bash
sudo python3 -m venv /srv/dgteam/.venv
sudo /srv/dgteam/.venv/bin/pip install --upgrade pip
sudo /srv/dgteam/.venv/bin/pip install -e .
sudo chmod +x /srv/dgteam/deploy/linux/bin/*.sh
```

If you need test tooling on the server:

```bash
sudo /srv/dgteam/.venv/bin/pip install -e .[dev]
```

## 4. Write the production env file

Start from:

`deploy/linux/env/dgteam.env.example`

Copy it to:

```bash
sudo cp /srv/dgteam/deploy/linux/env/dgteam.env.example /srv/dgteam/.env
sudo chmod 600 /srv/dgteam/.env
```

The most important values to review are:

- `DGTEAM_DB_PATH=./runtime/cloud/current/dgteam.db`
- `DGTEAM_PUBLISH_TOKEN=...`
- `DGTEAM_WECHAT_OFFICIAL_*`
- `DGTEAM_OPENROUTER_API_KEY=...`
- `DGTEAM_RETENTION_*`
- `DGTEAM_KEEP_CLOUD_DEPLOYMENTS=...`
- `DGTEAM_KEEP_AUTOMATION_RUN_STATES=...`
- `DGTEAM_KEEP_AUTOMATION_RECOVERY_STATES=...`
- `DGTEAM_MAX_STAGING_AGE_HOURS=...`
- `DGTEAM_MAX_UPLOAD_AGE_HOURS=...`
- `DGTEAM_DISK_WARN_PERCENT=...`

## 5. Install systemd units

Copy the unit files:

```bash
sudo cp /srv/dgteam/deploy/linux/systemd/dgteam-*.service /etc/systemd/system/
sudo cp /srv/dgteam/deploy/linux/systemd/dgteam-*.timer /etc/systemd/system/
sudo systemctl daemon-reload
```

Enable the core services:

```bash
sudo cp /srv/dgteam/deploy/linux/systemd/dgteam-query.service /etc/systemd/system/
sudo cp /srv/dgteam/deploy/linux/systemd/dgteam-publish.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dgteam-query.service
sudo systemctl enable --now dgteam-publish.service
sudo systemctl enable --now dgteam-wechat-official.service
sudo systemctl enable --now dgteam-wechat-official-worker.service
```

Only enable this if you still use the old WeCom bridge:

```bash
sudo systemctl enable --now dgteam-wechat-clawbot.service
```

Enable the maintenance timers:

```bash
sudo systemctl enable --now dgteam-backup.timer
sudo systemctl enable --now dgteam-prune.timer
sudo systemctl enable --now dgteam-healthcheck.timer
```

Validate the cleanup contract before the first real run:

```bash
sudo /srv/dgteam/deploy/linux/bin/dgteam-prune.sh --dry-run
python /srv/dgteam/scripts/ops_runtime_audit.py --project-root /srv/dgteam
```

## 5.5 Trusted runner for the real-source gate

The hosted GitHub workflow already covers the fixture gate. To formalize the real-source path, keep one additional env file on the live host:

```bash
sudo cp /srv/dgteam/deploy/linux/env/dgteam.runner.env.example /srv/dgteam/.runner.env
sudo chmod 600 /srv/dgteam/.runner.env
```

Fill in at least:

- `DGTEAM_GITHUB_REPOSITORY=owner/repo`
- `DGTEAM_GITHUB_RUNNER_NAME=dgteam-trusted-live`
- `DGTEAM_GITHUB_RUNNER_LABELS=self-hosted,dgteam-trusted,linux,x64`
- `DGTEAM_TRUSTED_RUNNER_SOURCE_DB=/srv/dgteam/runtime/cloud/current/dgteam.db`

Preflight the same workflow contract the runner will consume:

```bash
python /srv/dgteam/scripts/trusted_runner_preflight.py \
  --project-root /srv/dgteam \
  --workflow-path /srv/dgteam/.github/workflows/release_rehearsal.yml \
  --runner-env-file /srv/dgteam/.runner.env \
  --mode real-source \
  --assert-gate-ready
```

The real-source default is `/srv/dgteam/runtime/cloud/current/dgteam.db`. Only pass `--source-db` when you intentionally override that truth, for example during a controlled working-DB rehearsal.

Dry-run the runner installation and registration plan:

```bash
sudo /srv/dgteam/deploy/linux/bin/dgteam-runner-register.sh \
  --project-root /srv/dgteam \
  --workflow-path /srv/dgteam/.github/workflows/release_rehearsal.yml \
  --runner-env-file /srv/dgteam/.runner.env \
  --dry-run
```

Short form:

```bash
sudo /srv/dgteam/deploy/linux/bin/dgteam-runner-register.sh --dry-run
```

When you have a short-lived GitHub registration token, run the real registration:

```bash
sudo /srv/dgteam/deploy/linux/bin/dgteam-runner-register.sh \
  --project-root /srv/dgteam \
  --workflow-path /srv/dgteam/.github/workflows/release_rehearsal.yml \
  --runner-env-file /srv/dgteam/.runner.env \
  --registration-token <github-runner-registration-token>
```

Then install the repo-managed service:

```bash
sudo cp /srv/dgteam/deploy/linux/systemd/dgteam-github-runner.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dgteam-github-runner.service
sudo systemctl status dgteam-github-runner.service
```

The short-lived GitHub registration token is the only value this repository should not persist. Workflow YAML, runner env, source DB path, and service layout should all stay under `/srv/dgteam`.

## 6. Install nginx

Copy the nginx config:

```bash
sudo cp /srv/dgteam/deploy/linux/nginx/dgteam.conf /etc/nginx/sites-available/dgtdnb.com
sudo ln -sfn /etc/nginx/sites-available/dgtdnb.com /etc/nginx/sites-enabled/dgtdnb.com
sudo cp /srv/dgteam/deploy/linux/logrotate/dgteam /etc/logrotate.d/dgteam
sudo nginx -t
sudo systemctl reload nginx
```

What this config does:

- serves the query site on `/`
- forwards `/wechat/official/callback` to the official-account bridge
- forwards `/wecom/kf/callback` to the optional WeCom bridge
- exposes internal health routes through nginx

## 7. HTTPS

The nginx template expects Let's Encrypt paths:

```text
/etc/letsencrypt/live/dgtdnb.com/fullchain.pem
/etc/letsencrypt/live/dgtdnb.com/privkey.pem
```

Typical setup:

```bash
sudo apt-get update
sudo apt-get install -y nginx certbot python3-certbot-nginx
sudo certbot --nginx -d dgtdnb.com -d www.dgtdnb.com
```

If the official-account callback is online, do not expose plain HTTP only.  
Keep the callback URL on 443 and verify it after every nginx reload.

## 8. First deployment smoke check

Run:

```bash
curl http://127.0.0.1:9765/health
curl http://127.0.0.1:9865/health
curl http://127.0.0.1:8975/health
curl https://dgtdnb.com/api/status
```

The query frontend is part of the live release, not only the code checkout. Confirm that `/srv/dgteam/runtime/cloud/current/query_ui/asset-manifest.json` exists after activation, and verify the public page returns `index.html` with versioned `app.js` and `styles.css` URLs.

If `current` predates bundled `query_ui`, standardize the live lifecycle before calling the rollout finished:

```bash
python /srv/dgteam/scripts/live_release_backfill.py \
  --project-root /srv/dgteam \
  --server-url http://127.0.0.1:9865 \
  --query-base-url http://127.0.0.1:9765 \
  --public-base-url https://dgtdnb.com
```

That backfill builds two standard releases from `/srv/dgteam/runtime/cloud/current/dgteam.db`, activates them in sequence so both `current` and `previous` contain `query_ui`, proves rollback, and finishes with a standard live `current`.

Publish API contract checks:

```bash
curl http://127.0.0.1:9865/api/status
curl "http://127.0.0.1:9865/api/deployments/status?deployment_id=<id>"
```

The supported release paths are:

- `POST /api/releases/upload` followed by `POST /api/releases/activate` for the two-step auditable path.
- `POST /api/releases/deploy` for direct archive deploy when the caller needs one request to upload, validate, activate, and post-switch validate.
- `POST /api/releases/import-local` for server-side local release directories.
- `POST /api/releases/rollback` for restoring `previous` into `current`.

Then verify service state:

```bash
sudo systemctl status dgteam-query.service
sudo systemctl status dgteam-publish.service
sudo systemctl status dgteam-wechat-official.service
sudo systemctl status dgteam-wechat-official-worker.service
sudo systemctl status dgteam-github-runner.service
python /srv/dgteam/scripts/smoke_linked_chain.py --mode real-source
```

That rehearsal must finish with a smoke report that covers the same live-release truth used locally: activated release assets from `runtime/cloud/current/query_ui`, unknown `/api/*` JSON handling, WeChat consumer queries against the activated DB, rollback evidence, and the post-run ops audit snapshot.

## 9. Notes for stable operation

- The public website should go through nginx only.
- The publish API should stay bound to `127.0.0.1` unless you intentionally add a protected public route.
- Keep query traffic and callback traffic separated at the proxy layer.
- Do not place crawler login state on the cloud server unless you really need it there.

## Related docs

- [BACKUP_AND_RESTORE.md](C:\Users\somehow\Documents\Playground\dgteam\docs\BACKUP_AND_RESTORE.md)
- [ROLLBACK.md](C:\Users\somehow\Documents\Playground\dgteam\docs\ROLLBACK.md)
- [OPERATIONS.md](C:\Users\somehow\Documents\Playground\dgteam\docs\OPERATIONS.md)
