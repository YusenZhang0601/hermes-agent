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
- Metadata and pricing probes of a keyed custom gateway carry the key config declares for it.
  The Desktop model-info route (`GET /api/model/info`) and auxiliary-call pricing used to probe
  `/models` without a key, and the gateway answered 401. The key comes from the named provider's or
  `model` section's `api_key` / `key_env` for the same origin. It is read without side effects, since
  the route is public: no `key_cmd`, credential pool rotation or OAuth refresh.
- A failed `/models` probe of a custom endpoint keeps the window that endpoint last published. A
  401/403/407/429, 5xx or transport failure uses the last successful answer, which the disk memo now
  keeps for 30 days in any credential slot. Without one, a catalog *substring* match above the 256K
  default (the hosted `qwen3.8-flash` 1M entry for the self-hosted `qwen3.8-flash-next`, 262,144)
  yields to the warned default. Exact catalog ids and 404/405/410/501 "no `/models`" answers keep the
  catalog. Upstream issue #147574 and PR #147575 address the catalog side of the same symptom.
  When an official release includes #147575, review this clamp for retirement.
  The personal imports in `agent/model_metadata.py` sit below the upstream import block. This keeps
  the official merge of upstream `f15f71411f` (per-credential memo slots) conflict-free.
- This fork's native `main` update channel resolves to `personal/stable`. That branch advances after
  official stable releases pass personal checks. The desktop Update button installs it when requested.

## Probe-fix validation (2026-11-10)

On personal base `2fef947ed85c554a91068bda312dcad71bf19c75`, the new probe suite reports
12 failures and 1 pass. With the port, ten related test files report 230 passes and no failures,
including the existing auxiliary authentication and personal-update regressions. The local HTTP
fixtures exercise inherited proxy bypass and hosted proxy retention without calling a model.

## Context-probe validation (2026-11-13)

On personal base `dfa11c6aa706a52b74b866087d52c5e465384a7a`, the three affected suites report 19
failures and 10 passes. The failures show the field value of 1,000,000, a stale window or a missing
`Authorization`. The passes are the 4 existing auxiliary-auth tests and 6 guards: 404/405/501 keep the
catalog, an exact catalog id survives, no key goes to another origin, and the public route runs no
`key_cmd`. With the fix all 29 pass, and `scripts/check` passes 15 of 15. 365 related files report
6,945 passes. The 6 failures in `tests/tui_gateway/test_tui_gateway_server.py` occur identically on
the unmodified base. After upstream `f15f71411f` is applied, 228 tests across the affected and memo-key
suites pass. Every changed file also merges cleanly with upstream `main` of the same date.

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
