# Personal Hermes

This branch combines official stable releases with independently committed personal fixes.
The repository is `YusenZhang0601/hermes-agent` and the published branch is `personal/stable`.
The official base is recorded in `upstream.json`. The fork's contribution branches remain separate.

## Current changes

- Custom auxiliary endpoints use the matching main credential before considering an OpenAI-host
  credential. Regression coverage inspects the Authorization header using fake keys and a mock transport.
- This fork's native `main` update channel resolves to `personal/stable`. That branch advances after
  official stable releases pass personal checks. The desktop Update button installs it when requested.

## Maintenance

`sync.py` periodically reads the latest official stable release. It builds a disposable candidate,
merges the release, publishes a candidate branch, and waits for the hosted `Personal regression checks`
workflow on the exact candidate commit. That workflow builds the candidate's locked test environment
and runs the personal regressions and source checks on a standard GitHub runner.
Only a passing candidate advances `personal/stable`, using a normal fast-forward push. Merge
conflicts and failed checks leave the published version available and record a blocked receipt.
The authenticated GitHub account and Git authorship are `YusenZhang0601`.

The host schedules the standard-library-only script with a private state directory.
Credentials remain in the host's existing GitHub credential store.
No model API call is used for ordinary synchronization. Installation is initiated with the desktop
Update button, including its existing drain, validation and restart behavior.

```sh
python personal/sync.py --state-dir /absolute/path/to/private/state
```

The host's `status.json` records the last check and any blocked candidate. `--retry` retries a blocked
release after investigation. New personal fixes should include behavior tests in the same commit.
When an official release provides an equivalent fix, review and retire the overlapping personal change.
