"""The personal installation consumes its tested branch through the normal updater."""

import pytest


@pytest.mark.parametrize(("repository", "branch"), [
    ("YusenZhang0601/hermes-agent", "personal/stable"),
    ("NousResearch/hermes-agent", "main"),
    ("someone/hermes-agent", "main"),
])
def test_main_channel_uses_repository_branch(repository, branch):
    from hermes_cli.source_releases import resolve_source_target

    target = resolve_source_target("main", repository=repository)
    assert target.repository == repository
    assert target.branch == branch
    assert target.commit is None
