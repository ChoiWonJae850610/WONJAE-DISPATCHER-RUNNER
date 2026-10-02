# WONJAE-DISPATCHER-RUNNER

A small open-source runner for PC-free Codex automation using the published OpenAI Codex Python SDK and ChatGPT account authentication.

## Pilot status

This repository is in a control-plane pilot. It does **not** contain product source, product credentials, customer data, or deployment configuration.

The first acceptance path is deliberately read-only:

1. GitHub Actions starts on an explicitly trusted manual dispatch.
2. A private control repository is checked out with a separately scoped read token.
3. A protected Codex ChatGPT session is restored into an ephemeral `CODEX_HOME`.
4. Codex runs with `Sandbox.read_only` and `ApprovalMode.deny_all`.
5. The runner asks Codex to inspect the checkout and report the exact HEAD SHA.
6. The runner independently computes `git rev-parse HEAD` and requires an exact match.
7. If Codex refreshes its session, the resulting `auth.json` is written back to the protected Actions secret.
8. The ephemeral credential files are deleted from the runner.

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

After bootstrap, run **Codex Read-only Pilot**. It checks out the configured control repository and requires Codex's reported SHA to equal the actual checked-out SHA.

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
