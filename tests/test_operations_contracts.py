from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_operations_docs_and_workflow_reference_round_17_guards() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    operations = (PROJECT_ROOT / "docs" / "OPERATIONS.md").read_text(encoding="utf-8")
    backup_restore = (PROJECT_ROOT / "docs" / "BACKUP_AND_RESTORE.md").read_text(encoding="utf-8")
    rollback = (PROJECT_ROOT / "docs" / "ROLLBACK.md").read_text(encoding="utf-8")
    production = (PROJECT_ROOT / "docs" / "PRODUCTION_DEPLOYMENT.md").read_text(encoding="utf-8")
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "release_rehearsal.yml").read_text(encoding="utf-8")
    healthcheck = (PROJECT_ROOT / "deploy" / "linux" / "bin" / "dgteam-healthcheck.sh").read_text(encoding="utf-8")
    backup_script = (PROJECT_ROOT / "deploy" / "linux" / "bin" / "dgteam-backup.sh").read_text(encoding="utf-8")
    prune_script = (PROJECT_ROOT / "deploy" / "linux" / "bin" / "dgteam-prune.sh").read_text(encoding="utf-8")
    restore_script = (PROJECT_ROOT / "deploy" / "linux" / "bin" / "dgteam-restore.sh").read_text(encoding="utf-8")
    runner_preflight = (PROJECT_ROOT / "deploy" / "linux" / "bin" / "dgteam-runner-preflight.sh").read_text(encoding="utf-8")
    runner_register = (PROJECT_ROOT / "deploy" / "linux" / "bin" / "dgteam-runner-register.sh").read_text(encoding="utf-8")
    runner_env = (PROJECT_ROOT / "deploy" / "linux" / "env" / "dgteam.runner.env.example").read_text(encoding="utf-8")
    env_example = (PROJECT_ROOT / "deploy" / "linux" / "env" / "dgteam.env.example").read_text(encoding="utf-8")
    prune_service = (PROJECT_ROOT / "deploy" / "linux" / "systemd" / "dgteam-prune.service").read_text(encoding="utf-8")
    prune_timer = (PROJECT_ROOT / "deploy" / "linux" / "systemd" / "dgteam-prune.timer").read_text(encoding="utf-8")
    runner_service = (PROJECT_ROOT / "deploy" / "linux" / "systemd" / "dgteam-github-runner.service").read_text(encoding="utf-8")

    for text in (readme, operations, backup_restore, rollback, production):
        assert "/srv/dgteam" in text

    assert "scripts/ops_runtime_audit.py" in readme
    assert "scripts/prune_storage.py" in readme
    assert "scripts/smoke_linked_chain.py --fixture" in readme
    assert "candidate-gate-real-source" in readme
    assert "runtime/cloud/current/dgteam.db" in readme
    assert "runtime/cloud/current/dgteam.db" in operations
    assert "runtime/cloud/deployments" in operations
    assert "dgteam-prune.timer" in operations
    assert "DGTEAM_KEEP_CLOUD_DEPLOYMENTS" in operations
    assert "DGTEAM_MAX_STAGING_AGE_HOURS" in operations
    assert "archive_wechat_failed_tasks.py" in operations
    assert "dgteam-query.service" in operations
    assert "dgteam-publish.service" in operations
    assert "dgteam-query-api.service" not in operations
    assert "dgteam-publish-api.service" not in operations
    assert "runtime/local/automation/prod/state" in backup_restore
    assert "does not automatically replay `runtime/cloud/deployments`" in backup_restore
    assert "--restore-deployments-to" in backup_restore
    assert "dgteam-prune.timer" in backup_restore
    assert "dgteam-query.service" in backup_restore
    assert "dgteam-publish.service" in backup_restore
    assert "dgteam-query-api.service" not in backup_restore
    assert "dgteam-publish-api.service" not in backup_restore
    assert "smoke_linked_chain.py --fixture" in rollback
    assert "smoke_linked_chain.py --mode real-source" in rollback
    assert "runtime/cloud/current/dgteam.db" in rollback
    assert "smoke_report.json" in rollback
    assert "runtime/cloud/current" in rollback
    assert "dgteam-prune.timer" in production

    assert "schedule:" in workflow
    assert "workflow_dispatch:" in workflow
    assert "candidate-gate-fixture" in workflow
    assert "candidate-gate-real-source" in workflow
    assert "self-hosted" in workflow
    assert "dgteam-trusted" in workflow
    assert "trusted_runner_preflight.py" in workflow
    assert "trusted_runner_register.py" in workflow
    assert "gate_result.json" in workflow
    assert "ops_runtime_audit.py" in healthcheck
    assert "dgteam-query.service" in healthcheck
    assert "dgteam-publish.service" in healthcheck
    assert "DGTEAM_BACKUP_ROOT" in healthcheck
    assert "DGTEAM_PUBLIC_STATUS_URL" in healthcheck
    assert "deployments" in backup_script
    assert "local_automation_state" in backup_script
    assert "DGTEAM_BACKUP_ROOT" in backup_script
    assert "scripts/prune_storage.py" in prune_script
    assert "DGTEAM_KEEP_CLOUD_DEPLOYMENTS" in prune_script
    assert "DGTEAM_MAX_STAGING_AGE_HOURS" in prune_script
    assert "--restore-deployments-to" in restore_script
    assert "--restore-automation-state-to" in restore_script
    assert "dgteam-query.service" in restore_script
    assert "dgteam-publish.service" in restore_script
    assert "trusted_runner_preflight.py" in runner_preflight
    assert "trusted_runner_register.py" in runner_register
    assert "DGTEAM_GITHUB_REPOSITORY" in runner_env
    assert "DGTEAM_TRUSTED_RUNNER_SOURCE_DB" in runner_env
    assert "ExecStart=/srv/dgteam/deploy/linux/bin/dgteam-prune.sh" in prune_service
    assert "04:15:00" in prune_timer
    assert "ExecStart=/srv/dgteam/runtime/cloud/github_runner/run.sh" in runner_service
    assert "DGTEAM_KEEP_CLOUD_DEPLOYMENTS=20" in env_example
    assert "DGTEAM_KEEP_AUTOMATION_RUN_STATES=30" in env_example
    assert "DGTEAM_MAX_STAGING_AGE_HOURS=6" in env_example
