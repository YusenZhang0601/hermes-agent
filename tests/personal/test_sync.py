"""Real Git merges retain personal changes and isolate conflicting official changes."""

import json
from pathlib import Path
import subprocess

import pytest

from personal.sync import merge_release


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


@pytest.fixture
def repository(tmp_path):
    git(tmp_path, "init", "-b", "personal")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "commit.gpgsign", "false")
    (tmp_path / "personal").mkdir()
    (tmp_path / "personal/upstream.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "shared.txt").write_text("base\n", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "base")
    git(tmp_path, "branch", "official")
    return tmp_path


def test_official_merge_retains_personal_changes(repository):
    root = repository
    (root / "mine.txt").write_text("personal preference\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-m", "personal change")
    git(root, "checkout", "official")
    (root / "official.txt").write_text("new feature\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-m", "official release")
    release = git(root, "rev-parse", "HEAD")
    git(root, "checkout", "personal")

    merge_release(root, release, "v1.2.4")

    assert (root / "mine.txt").read_text(encoding="utf-8") == "personal preference\n"
    assert (root / "official.txt").read_text(encoding="utf-8") == "new feature\n"
    assert json.loads((root / "personal/upstream.json").read_text(encoding="utf-8"))["commit"] == release
    assert git(root, "status", "--porcelain") == ""


def test_merge_conflict_leaves_personal_head_unchanged(repository):
    root = repository
    (root / "shared.txt").write_text("personal choice\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-m", "personal change")
    original = git(root, "rev-parse", "HEAD")
    git(root, "checkout", "official")
    (root / "shared.txt").write_text("official choice\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-m", "official release")
    release = git(root, "rev-parse", "HEAD")
    git(root, "checkout", "personal")

    with pytest.raises(RuntimeError, match="CONFLICT"):
        merge_release(root, release, "v1.2.4")

    assert git(root, "rev-parse", "HEAD") == original
