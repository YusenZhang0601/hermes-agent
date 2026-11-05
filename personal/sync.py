#!/usr/bin/env python3
"""Promote official stable releases after personal regression checks and hosted CI."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile

REPOSITORY = "YusenZhang0601/hermes-agent"
BRANCH = "personal/stable"
UPSTREAM = "https://github.com/NousResearch/hermes-agent.git"
DESTINATION = f"https://github.com/{REPOSITORY}.git"
GIT = ["git", "-c", "user.name=YusenZhang0601", "-c", "user.email=zhangyswx@163.com",
       "-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential"]


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


def verify_identity(root: Path) -> None:
    login = run(["gh", "api", "user", "--jq", ".login"], root)
    if login != "YusenZhang0601":
        raise RuntimeError(f"Expected YusenZhang0601, authenticated as {login}")


def merge_release(candidate: Path, release: str, tag: str) -> None:
    """A failed merge stays in the disposable candidate, away from the published branch."""
    run([*GIT, "merge", "--no-ff", "--no-edit", release], candidate)
    manifest = candidate / "personal/upstream.json"
    manifest.write_text(json.dumps({"repository": "NousResearch/hermes-agent", "tag": tag,
                                    "commit": release}, indent=2) + "\n", encoding="utf-8")
    run([*GIT, "add", "--", "personal/upstream.json"], candidate)
    run([*GIT, "commit", "-m", f"chore(personal): record official base {tag}"], candidate)


def hosted_check(root: Path, branch: str, head: str) -> None:
    """Wait for the push-triggered run on this exact source commit."""
    import time

    for _ in range(12):
        records = json.loads(run(["gh", "run", "list", "--repo", REPOSITORY,
                                  "--branch", branch, "--event", "push", "--limit", "10",
                                  "--json", "databaseId,headSha,workflowName"], root))
        matching = [entry for entry in records if entry["headSha"] == head
                    and entry["workflowName"] == "Personal regression checks"]
        if matching:
            run(["gh", "run", "watch", str(matching[0]["databaseId"]), "--repo", REPOSITORY,
                 "--exit-status", "--interval", "30"], root, timeout=1800)
            return
        time.sleep(10)
    raise RuntimeError("The hosted validation run was not registered within two minutes")


def synchronize(root: Path, previous: dict, *, retry: bool = False) -> dict:
    verify_identity(root)
    release = json.loads(run(["gh", "api", "repos/NousResearch/hermes-agent/releases/latest"], root))
    tag = release["tag_name"]
    if release["draft"] or release["prerelease"] or not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise RuntimeError(f"Unexpected official stable release: {tag}")
    run([*GIT, "fetch", "--no-tags", DESTINATION,
         f"refs/heads/{BRANCH}:refs/remotes/personal/stable"], root)
    base = run([*GIT, "rev-parse", "refs/remotes/personal/stable"], root)
    manifest = json.loads(run([*GIT, "show", f"{base}:personal/upstream.json"], root))
    if manifest["tag"] == tag:
        return {"status": "current", "official_release": tag, "personal_commit": base}
    if (previous.get("status") == "blocked" and previous.get("official_release") == tag
            and previous.get("personal_commit") == base and not retry):
        return previous
    run([*GIT, "fetch", "--no-tags", UPSTREAM, f"refs/tags/{tag}"], root)
    release_commit = run([*GIT, "rev-parse", "FETCH_HEAD^{commit}"], root)
    run([*GIT, "merge-base", "--is-ancestor", manifest["commit"], release_commit], root)
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    branch = f"personal/sync/{tag}-{base[:10]}-{stamp}"
    with tempfile.TemporaryDirectory(prefix="hermes-personal-") as scratch:
        candidate = Path(scratch) / "candidate"
        run([*GIT, "worktree", "add", "--detach", str(candidate), base], root)
        try:
            merge_release(candidate, release_commit, tag)
            head = run([*GIT, "rev-parse", "HEAD"], candidate)
            verify_identity(root)
            run([*GIT, "push", DESTINATION, f"{head}:refs/heads/{branch}"], root)
            hosted_check(root, branch, head)
            verify_identity(root)
            # A concurrent personal change makes this normal fast-forward push refuse promotion.
            run([*GIT, "push", DESTINATION, f"{head}:refs/heads/{BRANCH}"], root)
            run([*GIT, "push", DESTINATION, "--delete", branch], root)
            return {"status": "ready", "official_release": tag, "personal_commit": head,
                    "installation": "Use the Hermes desktop Update button when idle"}
        except (RuntimeError, OSError, ValueError) as exc:
            return {"status": "blocked", "official_release": tag, "personal_commit": base,
                    "candidate_branch": branch, "error": str(exc)}
        finally:
            run([*GIT, "worktree", "remove", "--force", str(candidate)], root)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--retry", action="store_true", help="Retry a previously blocked release")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    args.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (args.state_dir / "sync.lock").open("a", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        receipt = args.state_dir / "status.json"
        previous = json.loads(receipt.read_text(encoding="utf-8")) if receipt.exists() else {}
        try:
            result = synchronize(root, previous, retry=args.retry)
        except (RuntimeError, OSError, ValueError) as exc:
            result = {"status": "error", "error": str(exc)}
        result["checked_at"] = datetime.now(UTC).isoformat()
        temporary = receipt.with_suffix(".tmp")
        temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(receipt)
        print(json.dumps(result, ensure_ascii=False))
        return int(result["status"] in {"blocked", "error"})


if __name__ == "__main__":
    raise SystemExit(main())
