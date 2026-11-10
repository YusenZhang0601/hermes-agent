# Personal Hermes

This branch combines official stable releases with independently committed personal fixes.
The repository is `YusenZhang0601/hermes-agent` and the published branch is `personal/stable`.
The official base is recorded in `upstream.json`. The fork's contribution branches remain separate.

## Current changes

- Custom auxiliary endpoints use the matching main credential before considering an OpenAI-host
  credential. Regression coverage inspects the Authorization header using fake keys and a mock transport.
- Model-metadata probes connect directly to local, LAN and Tailscale engines while hosted providers
  retain environment proxy routing. This includes the shared reader used for llama.cpp properties.
  The original fix is `358076b4c46f1af3a69fb45b1c6a288e4e5894b0` from issue
  [#148849](https://github.com/NousResearch/hermes-agent/issues/148849). Its behavior regressions run
  both on personal changes and before every official-release promotion.
- This fork's native `main` update channel resolves to `personal/stable`. That branch advances after
  official stable releases pass personal checks. The desktop Update button installs it when requested.

## Probe-fix validation (2026-11-10)

On personal base `2fef947ed85c554a91068bda312dcad71bf19c75`, the new probe suite reports
12 failures and 1 pass. With the port, ten related test files report 230 passes and no failures,
including the existing auxiliary authentication and personal-update regressions. The local HTTP
fixtures exercise inherited proxy bypass and hosted proxy retention without calling a model.

## Maintenance

The `Maintain personal Hermes` GitHub workflow checks every six hours, at minute 23 UTC.
The repository's default branch is `personal/control`, containing only that scheduled entrypoint
and its README. The entrypoint calls the validated reusable sync workflow at a pinned commit.
Application code is published on `personal/stable`. The main branch and upstream contribution
branches remain separate. Checking, merging, testing and publishing run on standard GitHub runners
and continue independently of the desktop computer.

`sync.py --prepare` merges a new official release in the disposable runner checkout. The workflow
then builds that candidate's locked test environment, runs the personal regressions and source
checks, and invokes `sync.py --publish` for the exact validated commit. Only a passing candidate
advances `personal/stable`, using a normal fast-forward push and remote commit readback.
Conflicts and failed checks stop the workflow and retain the previously published version.

The user authorized `github-actions[bot]` on 2026-11-06 solely for this repository's automatic
official-release synchronization. Human-authored fixes retain `YusenZhang0601` authorship.
A repository-scoped write deploy key, stored as the encrypted `HERMES_SYNC_DEPLOY_KEY` Actions
secret, permits complete official merges including workflow changes. The built-in `GITHUB_TOKEN`
is used with read-only API access. The workflow loads a temporary SSH key, verifies GitHub's host
keys from its HTTPS metadata API, and removes the temporary credential on exit.

Use the workflow's `verify_only` manual option to run the complete validation and a disposable
test publication without advancing `personal/stable`. Successful test publications are removed
after remote readback. Workflow summaries and logs provide the run receipts. Installation is
initiated with the desktop Update button and its existing drain, validation and restart behavior.

New personal fixes should include behavior tests in the same commit. Changes to the reusable
workflow itself also update the pinned call in `personal/control` after its validation succeeds.
When an official release provides an equivalent fix, review and retire the overlapping personal change.
