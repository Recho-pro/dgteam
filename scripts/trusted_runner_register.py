from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from dgteam.ops.trusted_runner import DEFAULT_RUNNER_ENV_FILE, build_preflight_report, read_runner_env_file


LATEST_RUNNER_RELEASE_API = "https://api.github.com/repos/actions/runner/releases/latest"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download and configure the DGTEAM GitHub self-hosted trusted runner.")
    parser.add_argument("--project-root", default=str(PROJECT_ROOT))
    parser.add_argument("--workflow-path", default=str(PROJECT_ROOT / ".github" / "workflows" / "release_rehearsal.yml"))
    parser.add_argument("--runner-env-file", default=str(DEFAULT_RUNNER_ENV_FILE))
    parser.add_argument("--source-db", default="")
    parser.add_argument("--asset-url", default="")
    parser.add_argument("--registration-token", default="")
    parser.add_argument("--registration-token-file", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force-download", action="store_true")
    return parser.parse_args()


def _resolve_latest_asset_url() -> str:
    request = urllib.request.Request(
        LATEST_RUNNER_RELEASE_API,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "dgteam-trusted-runner"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    for asset in list(payload.get("assets") or []):
        name = str(asset.get("name") or "")
        if "linux-x64" in name and name.endswith(".tar.gz"):
            return str(asset.get("browser_download_url") or "").strip()
    raise RuntimeError("Unable to resolve the latest linux-x64 GitHub Actions runner asset.")


def _download_and_extract(*, asset_url: str, runner_root: Path, force_download: bool) -> dict[str, Any]:
    runner_root.mkdir(parents=True, exist_ok=True)
    archive_path = runner_root / "actions-runner.tar.gz"
    binary_ready = (runner_root / "config.sh").is_file() and (runner_root / "run.sh").is_file()

    if force_download or not binary_ready:
        if archive_path.exists():
            archive_path.unlink()
        with urllib.request.urlopen(
            urllib.request.Request(asset_url, headers={"User-Agent": "dgteam-trusted-runner"}),
            timeout=60,
        ) as response:
            archive_path.write_bytes(response.read())
        with tarfile.open(str(archive_path), "r:gz", encoding="utf-8") as archive:
            archive.extractall(path=runner_root)
        binary_ready = (runner_root / "config.sh").is_file() and (runner_root / "run.sh").is_file()

    return {
        "asset_url": asset_url,
        "archive_path": str(archive_path),
        "runner_binary_ready": bool(binary_ready),
    }


def _registration_token(args: argparse.Namespace) -> str:
    if str(args.registration_token).strip():
        return str(args.registration_token).strip()
    if str(args.registration_token_file).strip():
        return Path(args.registration_token_file).expanduser().read_text(encoding="utf-8").strip()
    return ""


def main() -> int:
    args = parse_args()
    project_root = Path(args.project_root).expanduser().resolve()
    workflow_path = Path(args.workflow_path).expanduser().resolve()
    runner_env_file = Path(args.runner_env_file).expanduser().resolve()
    env_values = read_runner_env_file(runner_env_file)
    source_db = Path(args.source_db).expanduser().resolve() if str(args.source_db).strip() else None
    preflight = build_preflight_report(
        project_root=project_root,
        workflow_path=workflow_path,
        mode="real-source",
        runner_env_file=runner_env_file,
        source_db=source_db,
    )

    asset_url = str(args.asset_url).strip() or str(env_values.get("DGTEAM_GITHUB_RUNNER_ASSET_URL") or "").strip()
    if not asset_url:
        asset_url = _resolve_latest_asset_url()

    runner_root = Path(preflight["runner_root"]).expanduser().resolve()
    runner_workdir = Path(preflight["runner_workdir"]).expanduser().resolve()
    runner_workdir.mkdir(parents=True, exist_ok=True)
    download = _download_and_extract(asset_url=asset_url, runner_root=runner_root, force_download=bool(args.force_download))
    token = _registration_token(args)

    plan = {
        "ok": bool(preflight.get("gate_ready")) and bool(download.get("runner_binary_ready")),
        "contract_version": preflight["contract_version"],
        "preflight": preflight,
        "download": download,
        "runner_root": str(runner_root),
        "runner_workdir": str(runner_workdir),
        "token_supplied": bool(token),
        "dry_run": bool(args.dry_run),
        "register_command_preview": [
            str(runner_root / "config.sh"),
            "--unattended",
            "--replace",
            "--url",
            f"https://github.com/{preflight['registration_inputs']['repository'] or '<owner/repo>'}",
            "--token",
            "<registration-token>",
            "--name",
            preflight["registration_inputs"]["runner_name"] or "<runner-name>",
            "--labels",
            preflight["registration_inputs"]["runner_labels"] or "<labels>",
            "--work",
            str(runner_workdir),
            "--runnergroup",
            preflight["registration_inputs"]["runner_group"] or "Default",
        ],
    }
    if args.dry_run:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0 if plan["ok"] else 1

    if not preflight["registration_ready"] or not token:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 1

    config_args = [
        str(runner_root / "config.sh"),
        "--unattended",
        "--replace",
        "--url",
        f"https://github.com/{preflight['registration_inputs']['repository']}",
        "--token",
        token,
        "--name",
        preflight["registration_inputs"]["runner_name"],
        "--labels",
        preflight["registration_inputs"]["runner_labels"],
        "--work",
        str(runner_workdir),
        "--runnergroup",
        preflight["registration_inputs"]["runner_group"],
    ]
    subprocess.run(
        config_args,
        cwd=runner_root,
        env={"RUNNER_ALLOW_RUNASROOT": "1", **os.environ},
        check=True,
    )
    plan["configured"] = (runner_root / ".runner").is_file()
    plan["ok"] = bool(plan["configured"])
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0 if plan["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
