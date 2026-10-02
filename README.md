# WONJAE-DISPATCHER-RUNNER

A small open-source runner for PC-free Codex automation using the published OpenAI Codex Python SDK and ChatGPT account authentication.

## Pilot status

This repository is in a control-plane pilot. It does **not** contain product source, product credentials, customer data, or deployment configuration.

The first acceptance path is deliberately read-only:

1. GitHub Actions starts on an explicitly trusted manual dispatch.
2. A private control repository is checked out with a separately scoped read token.
3. A protected Codex ChatGPT session is restored into an ephemeral `CODEX_HOME`.
4. Codex runs with `Sandbox.read_only` and `ApprovalMode.deny_all`.
5. The runner independently proves the exact Git HEAD and a clean worktree before the Codex turn.
6. Codex completes an authenticated read-only turn against that exact checkout.
7. The runner independently proves the same Git HEAD and unchanged clean worktree after the turn; model prose is not repository-state evidence.
8. If Codex refreshes its session, the resulting `auth.json` is written back to the protected Actions secret.
9. The ephemeral credential files are deleted from the runner.

No product TASK, branch, PR, deployment, release, or physical validation is part of this pilot.

## Why GitHub Actions

Standard GitHub-hosted runners for public repositories are currently free and unlimited. The runner itself stays generic and public; private repositories are accessed only with narrowly scoped credentials stored as GitHub Actions secrets.

## Required repository configuration

The secret-using workflows are intentionally `workflow_dispatch` only during the pilot.

Repository variable:

- `CONTROL_REPOSITORY`: the private control repository in `owner/name` form.

Repository secrets:

- `CONTROL_READ_TOKEN`: fine-grained GitHub token limited to read-only access to the private control repository.
- `SECRET_ROTATION_TOKEN`: fine-grained GitHub token limited to this runner repository with Actions **Secrets: write** permission.
- `CODEX_AUTH_JSON`: created by the bootstrap workflow after one-time ChatGPT device authorization. Do not create or paste this manually unless recovering a known-good session.

## One-time ChatGPT bootstrap

After the two GitHub tokens above are configured, run **Bootstrap Codex ChatGPT Auth** from the Actions tab.

The job prints only the OpenAI verification URL and short-lived device code. Open that URL, enter the code, and approve the ChatGPT account. The workflow stores the resulting `CODEX_HOME/auth.json` directly as the protected `CODEX_AUTH_JSON` repository secret; it is never uploaded as an artifact or intentionally printed.

## Read-only pilot

After bootstrap, run **Codex Read-only Pilot**. It checks out the configured control repository at an exact SHA, completes one authenticated Codex turn with `Sandbox.read_only` and `ApprovalMode.deny_all`, and independently verifies that Git HEAD and the clean worktree are unchanged before and after the turn.

The workflow does not use `pull_request_target`, and pull-request validation receives no private secrets.

## Security boundary

See [SECURITY.md](SECURITY.md). GitHub and the exact checked-out commit remain the evidence source of truth; a model response is never treated as proof by itself.

## Development

```bash
python -m pip install -e ".[dev]"
ruff check .
pytest
```

Python 3.11+ is required.


## Bounded write pilot

After the read-only pilot passes, **Codex Write Pilot** tests a deterministic private-repository publication path. Codex reviews bounded control context in read-only mode and returns a strict structured handoff. The trusted runner validates that handoff, materializes only `docs/pilots/DISPATCHER_V2_SIWC_WRITE_001.md`, independently rejects any other changed path, then creates one commit, publishes the fixed pilot branch, opens a Draft PR to `main`, and reads the PR back before reporting success. This avoids making orchestration correctness depend on whether a model elects to call a particular filesystem tool.

The workflow requires the additional `CONTROL_WRITE_TOKEN` repository secret, scoped only to the private control repository with the minimum Contents and Pull requests write authority required for the pilot. It does not merge the resulting PR.

## Actions progress view

Dispatcher v2 workflows use the canonical phases in `docs/ACTIONS_PROGRESS.md`. The reusable `scripts/actions_progress.py` helper writes compact phase/state notices and a run summary so an Owner can open the GitHub Actions run and see `WAKE -> CONTROL -> PRODUCT -> CODEX -> WRITE -> STARTED -> VALIDATE -> INTEGRATE -> RESULT` without reading raw logs. Exact GitHub evidence remains authoritative; the progress timeline is observability only.
