#!/usr/bin/env python3
"""Prepare and publish official stable updates inside the authorized GitHub workflow."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import signal
import subprocess

REPOSITORY = "YusenZhang0601/hermes-agent"
BRANCH = "personal/stable"
UPSTREAM = "https://github.com/NousResearch/hermes-agent.git"
DESTINATION = f"ssh://git@ssh.github.com:443/{REPOSITORY}.git"
GIT = ["git", "-c", "user.name=github-actions[bot]", "-c",
       "user.email=41898282+github-actions[bot]@users.noreply.github.com"]


def run(args: list[str], cwd: Path, *, timeout: int = 180, env=None) -> str:
    process = subprocess.Popen(args, cwd=cwd, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, start_new_session=True)
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=10)
        raise RuntimeError(f"Timed out after {timeout}s: {args[0]}") from None
    if process.returncode:
        raise RuntimeError(f"Command failed ({process.returncode}): {' '.join(args)}\n{output[-12000:]}")
    return output.strip()


def verify_cloud_context() -> None:
    """The user authorized this workflow's bot for this repository on 2026-11-06."""
    workflow = f"{REPOSITORY}/.github/workflows/personal-sync.yml@refs/heads/"
    allowed = {workflow + BRANCH, workflow + "personal/sync/cloud-migration",
               f"{REPOSITORY}/.github/workflows/personal-schedule.yml@refs/heads/personal/control"}
    if (os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("GITHUB_REPOSITORY") != REPOSITORY
            or os.environ.get("GITHUB_ACTOR") != "YusenZhang0601"
            or os.environ.get("GITHUB_WORKFLOW_REF") not in allowed):
        raise RuntimeError("Cloud synchronization requires the authorized repository workflow and actor")


def merge_release(candidate: Path, release: str, tag: str) -> None:
    """A failed merge stays in the disposable candidate, away from the published branch."""
    run([*GIT, "merge", "--no-ff", "--no-edit", release], candidate)
    manifest = candidate / "personal/upstream.json"
    manifest.write_text(json.dumps({"repository": "NousResearch/hermes-agent", "tag": tag,
                                    "commit": release}, indent=2) + "\n", encoding="utf-8")
    run([*GIT, "add", "--", "personal/upstream.json"], candidate)
    run([*GIT, "commit", "-m", f"chore(personal): record official base {tag}"], candidate)


def prepare_cloud(root: Path, *, verify_only: bool, run_id: str) -> dict:
    """The runner checkout is disposable. The published branch stays unchanged during validation."""
    if not re.fullmatch(r"\d+", run_id):
        raise ValueError("A GitHub run id is required")
    release = json.loads(run(["gh", "api", "repos/NousResearch/hermes-agent/releases/latest"], root))
    tag = release["tag_name"]
    if release["draft"] or release["prerelease"] or not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise RuntimeError(f"Unexpected official stable release: {tag}")
    run([*GIT, "fetch", "--no-tags", DESTINATION,
         f"refs/heads/{BRANCH}:refs/remotes/personal/stable"], root)
    base = run([*GIT, "rev-parse", "refs/remotes/personal/stable"], root)
    if not verify_only and run([*GIT, "rev-parse", "HEAD"], root) != base:
        raise RuntimeError("The personal branch changed after checkout; retry with the new version")
    manifest = json.loads((root / "personal/upstream.json").read_text(encoding="utf-8"))
    if manifest["tag"] == tag and not verify_only:
        return {"status": "current", "official_release": tag, "personal_commit": base}
    if manifest["tag"] != tag:
        run([*GIT, "fetch", "--no-tags", UPSTREAM, f"refs/tags/{tag}"], root)
        release_commit = run([*GIT, "rev-parse", "FETCH_HEAD^{commit}"], root)
        run([*GIT, "merge-base", "--is-ancestor", manifest["commit"], release_commit], root)
        merge_release(root, release_commit, tag)
    if verify_only:
        probe = root / "personal/cloud-probe.json"
        probe.write_text(json.dumps({"run_id": run_id}) + "\n", encoding="utf-8")
        run([*GIT, "add", "--", "personal/cloud-probe.json"], root)
        run([*GIT, "commit", "-m", "test(personal): verify cloud publication credential"], root)
    head = run([*GIT, "rev-parse", "HEAD"], root)
    return {"status": "prepared", "official_release": tag, "personal_commit": head,
            "base_commit": base, "verify_only": verify_only, "run_id": run_id,
            "source_root": str(root)}


def publish_cloud(root: Path, prepared: dict) -> dict:
    head = prepared["personal_commit"]
    if (prepared.get("status") != "prepared" or prepared.get("source_root") != str(root)
            or os.environ.get("PERSONAL_SYNC_VALIDATED_HEAD") != head
            or run([*GIT, "rev-parse", "HEAD"], root) != head):
        raise RuntimeError("The exact prepared source must be validated before publication")
    run([*GIT, "diff", "--exit-code", "HEAD"], root)
    branch = f"personal/cloud-probe-{prepared['run_id']}" if prepared["verify_only"] else BRANCH
    run([*GIT, "push", DESTINATION, f"{head}:refs/heads/{branch}"], root)
    actual = run(["gh", "api", f"repos/{REPOSITORY}/git/ref/heads/{branch}",
                  "--jq", ".object.sha"], root)
    if actual != head:
        raise RuntimeError("Published commit readback did not match the validated source")
    if prepared["verify_only"]:
        run([*GIT, "push", DESTINATION, "--delete", branch], root)
    return {**prepared, "status": "verified" if prepared["verify_only"] else "ready"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument("--prepare", action="store_true")
    operation.add_argument("--publish", action="store_true")
    parser.add_argument("--verify-only", action="store_true", help="Validate and test-push without promoting")
    args = parser.parse_args()
    verify_cloud_context()
    root = Path(__file__).resolve().parents[1]
    args.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    receipt = args.state_dir / "status.json"
    if args.prepare:
        result = prepare_cloud(root, verify_only=args.verify_only, run_id=os.environ.get("GITHUB_RUN_ID", ""))
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
            output.write(f"has_update={str(result['status'] == 'prepared').lower()}\n")
            output.write(f"head={result['personal_commit']}\n")
    else:
        result = publish_cloud(root, json.loads(receipt.read_text(encoding="utf-8")))
    result["checked_at"] = datetime.now(UTC).isoformat()
    temporary = receipt.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(receipt)
    print(json.dumps(result, ensure_ascii=False))
    with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as summary:
        summary.write(f"Personal sync `{result['status']}` for official `{result['official_release']}`.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
