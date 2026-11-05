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


@pytest.mark.parametrize("checks_pass", [True, False])
def test_published_version_moves_only_after_hosted_validation(repository, tmp_path, monkeypatch, checks_pass):
    from personal import sync

    root = repository
    official_base = git(root, "rev-parse", "HEAD")
    manifest = root / "personal/upstream.json"
    manifest.write_text(json.dumps({"repository": "NousResearch/hermes-agent", "tag": "v1.2.3",
                                    "commit": official_base}) + "\n", encoding="utf-8")
    (root / "mine.txt").write_text("personal\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-m", "personal configuration")
    original = git(root, "rev-parse", "HEAD")
    git(root, "branch", "personal/stable")
    published = tmp_path / "published.git"
    git(root, "clone", "--bare", str(root), str(published))
    git(root, "checkout", "official")
    (root / "official.txt").write_text("official feature\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-m", "official release")
    git(root, "tag", "v1.2.4")
    monkeypatch.setattr(sync, "UPSTREAM", str(root))
    monkeypatch.setattr(sync, "DESTINATION", str(published))
    real_run = sync.run

    def run(args, cwd, **kwargs):
        if args[:4] == ["gh", "api", "user", "--jq"]:
            return "YusenZhang0601"
        if args[:3] == ["gh", "api", "repos/NousResearch/hermes-agent/releases/latest"]:
            return json.dumps({"tag_name": "v1.2.4", "draft": False, "prerelease": False})
        return real_run(args, cwd, **kwargs)

    def check(candidate_root, branch, head):
        assert git(published, "rev-parse", "personal/stable") == original
        assert git(published, "rev-parse", branch) == head
        if not checks_pass:
            raise RuntimeError("Hosted check failed")

    monkeypatch.setattr(sync, "run", run)
    monkeypatch.setattr(sync, "hosted_check", check)
    result = sync.synchronize(root, {})
    current = git(published, "rev-parse", "personal/stable")
    if checks_pass:
        assert result["status"] == "ready"
        assert current == result["personal_commit"]
        assert git(published, "show", f"{current}:mine.txt") == "personal"
        assert git(published, "show", f"{current}:official.txt") == "official feature"
    else:
        assert result["status"] == "blocked"
        assert current == original
