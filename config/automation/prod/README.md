# DGTEAM Production Automation Config

This directory is the only local config home for the formal production collection flow.

Expected files:

- `auth.json`
  - crawler login credentials
- `profile.env`
  - collection defaults for the automation profile
- `sync.env`
  - production publish endpoint and token

Template files shipped with the repository:

- `auth.json.example`
- `profile.env.example`
- `sync.env.example`

Copy the example files to their live counterparts before enabling the formal production automation flow.

Formal entry points:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_collect_and_sync.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\register_collect_and_sync_task.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\unregister_collect_and_sync_task.ps1
```

The runtime is no longer isolated under `automation_prod`.

All production runs now write directly into:

- `runtime/local`
- `runtime/cloud`

That keeps local query, release history, staged publish, and cloud sync on the same state line.
