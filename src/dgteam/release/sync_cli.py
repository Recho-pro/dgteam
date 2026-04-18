from __future__ import annotations

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Dict

from dgteam.agent.pipeline import run_pipeline
from dgteam.core.config import load_settings
from dgteam.core.console_encoding import configure_utf8_stdio
from dgteam.core.encoding_guard import assert_project_encoding_clean

from .builder import archive_release_bundle
from .deploy_state import DeploymentJournal, deployment_id_for_release
from .upload_client import activate_release, upload_release_bundle


def _default_project_root() -> Path:
    cwd = Path.cwd().resolve()
    if (cwd / "src" / "dgteam").exists():
        return cwd
    return Path(__file__).resolve().parents[3]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DGTEAM publish-and-sync helper")
    parser.add_argument("--server-url", required=True)
    parser.add_argument("--token", default="")
    parser.add_argument("--run-key", default="")
    parser.add_argument("--release-id", default="")
    parser.add_argument("--release-dir", default="")
    parser.add_argument("--project-root", default="")
    parser.add_argument("--no-activate", action="store_true")
    parser.add_argument("--skip-encoding-check", action="store_true")
    return parser.parse_args()


def sync_release(
    *,
    server_url: str,
    token: str = "",
    run_key: str = "",
    release_id: str = "",
    release_dir: Path | None = None,
    project_root: Path | None = None,
    activate: bool = True,
    skip_encoding_check: bool = False,
) -> Dict[str, Any]:
    root = Path(project_root or _default_project_root()).expanduser().resolve()
    settings = load_settings(project_root=root)
    local_state_root = settings.local_root / "publish_runs"
    local_state_root.mkdir(parents=True, exist_ok=True)
    resolved_release_dir = Path(release_dir).expanduser().resolve() if release_dir else None
    pipeline_result: Dict[str, Any] | None = None
    provisional_release_id = str(release_id or run_key or "auto_publish").strip() or "auto_publish"
    resolved_deployment_id = deployment_id_for_release(provisional_release_id, prefix="publish")
    journal = DeploymentJournal(local_state_root, resolved_deployment_id)
    journal.initialize(
        role="local",
        release_id=str(release_id or "").strip(),
        metadata={
            "project_root": str(root),
            "server_url": str(server_url or "").strip(),
            "activate": bool(activate),
        },
    )
    journal.event("sync_started", message="Starting local publish-and-sync flow.")

    try:
        if not skip_encoding_check:
            assert_project_encoding_clean(root)
            journal.event("encoding_check_passed", message="Encoding guard passed.")

        if resolved_release_dir is None:
            journal.event("publish_pipeline_started", message="No release_dir was provided, running publish pipeline.")
            pipeline = run_pipeline(
                "publish",
                project_root=root,
                run_key=str(run_key or ""),
                release_id=str(release_id or ""),
            )
            pipeline_result = dict(pipeline.__dict__)
            resolved_release_dir = Path(str(pipeline.release_dir or "")).expanduser().resolve()
            if not str(pipeline.release_dir or "").strip():
                raise ValueError("Publish completed without a release_dir.")
            journal.event(
                "publish_pipeline_completed",
                message="Publish pipeline produced a release bundle.",
                run_key=str(pipeline.run_key or ""),
                release_dir=str(resolved_release_dir),
            )

        if not resolved_release_dir.exists():
            raise FileNotFoundError(f"Release directory does not exist: {resolved_release_dir}")

        resolved_release_id = str(release_id or resolved_release_dir.name or "").strip()
        if not resolved_release_id:
            raise ValueError("Unable to resolve release_id for sync.")

        journal.update(
            status="running",
            step="release_ready",
            metadata={
                "release_id": resolved_release_id,
                "release_dir": str(resolved_release_dir),
            },
        )

        upload_response: Dict[str, Any]
        archive_summary: Dict[str, Any]
        with TemporaryDirectory(prefix="dgteam_sync_") as temp_dir:
            temp_archive = Path(temp_dir) / f"{resolved_release_id}.zip"
            journal.event("archive_started", message="Creating temporary release archive.", archive_path=str(temp_archive))
            archive_summary = archive_release_bundle(resolved_release_dir, temp_archive)
            journal.event(
                "archive_completed",
                message="Temporary release archive is ready.",
                archive=archive_summary,
            )
            journal.update(status="running", step="archived", archive=archive_summary)

            if activate:
                upload_only_response = upload_release_bundle(
                    server_url=server_url,
                    archive_path=temp_archive,
                    token=token,
                    release_id=resolved_release_id,
                    activate=False,
                )
                activate_response = activate_release(
                    server_url=server_url,
                    token=token,
                    release_id=resolved_release_id,
                )
                upload_response = {
                    "ok": True,
                    "flow": "upload_then_activate",
                    "imported": upload_only_response.get("imported", {}),
                    "activated": activate_response.get("activated", {}),
                    # Keep the legacy key so existing callers and tests do not break.
                    "deployed": activate_response.get("activated", {}),
                }
            else:
                upload_response = upload_release_bundle(
                    server_url=server_url,
                    archive_path=temp_archive,
                    token=token,
                    release_id=resolved_release_id,
                    activate=False,
                )

        archive_summary = {
            **archive_summary,
            "temporary": True,
            "removed_after_upload": True,
        }
        journal.event(
            "upload_completed",
            message="Cloud publish request completed.",
            response=upload_response,
        )
        journal.update(
            status="completed",
            step="deployed" if activate else "uploaded",
            ok=True,
            finished=True,
            release_id=resolved_release_id,
            release_dir=str(resolved_release_dir),
            archive=archive_summary,
            upload=upload_response,
            pipeline=pipeline_result or {},
        )
        return {
            "ok": True,
            "project_root": str(root),
            "release_id": resolved_release_id,
            "deployment_id": resolved_deployment_id,
            "release_dir": str(resolved_release_dir),
            "pipeline": pipeline_result or {},
            "archive": archive_summary,
            "upload": upload_response,
            "status_files": journal.note_paths(),
        }
    except Exception as exc:
        journal.event("sync_failed", level="error", message=str(exc))
        journal.update(
            status="failed",
            step="failed",
            ok=False,
            finished=True,
            last_error={"type": type(exc).__name__, "message": str(exc)},
        )
        raise


def main() -> None:
    configure_utf8_stdio()
    args = parse_args()
    response = sync_release(
        server_url=args.server_url,
        token=args.token,
        run_key=args.run_key,
        release_id=args.release_id,
        release_dir=Path(args.release_dir) if str(args.release_dir or "").strip() else None,
        project_root=Path(args.project_root) if str(args.project_root or "").strip() else None,
        activate=not bool(args.no_activate),
        skip_encoding_check=bool(args.skip_encoding_check),
    )
    print(json.dumps(response, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
