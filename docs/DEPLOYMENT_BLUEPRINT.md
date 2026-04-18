# Deployment Blueprint

This file is now the top-level deployment index.

## Core idea

- local machine collects data and produces release bundles
- cloud server imports the bundle into staging
- health checks pass
- live switches atomically
- rollback always falls back to the previous live release

## Read in this order

1. [PRODUCTION_DEPLOYMENT.md](C:\Users\somehow\Documents\Playground\dgteam\docs\PRODUCTION_DEPLOYMENT.md)
2. [BACKUP_AND_RESTORE.md](C:\Users\somehow\Documents\Playground\dgteam\docs\BACKUP_AND_RESTORE.md)
3. [ROLLBACK.md](C:\Users\somehow\Documents\Playground\dgteam\docs\ROLLBACK.md)
4. [OPERATIONS.md](C:\Users\somehow\Documents\Playground\dgteam\docs\OPERATIONS.md)
