# Security Policy

## Scope

This public repository must remain safe to inspect and fork. It contains runner logic only. Private repositories, product state, and secrets are external inputs.

## Secrets

Never commit, echo, upload, cache, or attach:

- Codex/ChatGPT `auth.json`
- access or refresh tokens
- GitHub personal access tokens or GitHub App private keys
- deployment credentials
- private product configuration or customer data

The bootstrap and execution workflows use ephemeral `CODEX_HOME` directories and GitHub Actions secrets. Credential files are removed in an `always()` cleanup step.

## Workflow trust boundary

Secret-using workflows are manual-only during the pilot and must execute code from the default branch. Pull-request validation receives no private secrets.

Do not introduce `pull_request_target` for code execution. Do not pass untrusted PR content, issue text, commit messages, or external messages to a privileged Codex turn.

## Token separation

Use separate credentials for:

- reading the private control repository;
- rotating secrets in this public runner repository;
- any later product source-writing authority.

The read-only pilot does not authorize product writes.

## Reporting

If a secret may have been exposed, revoke/rotate it first and then report the incident without including the secret value.


## Product-pilot credential boundary

Product source authority is never reused from the DEV-CONTROL credential. A product pilot uses a dedicated fine-grained token restricted to that product repository and the minimum Contents/Pull requests/Actions permissions needed for branch, PR, integration, and exact-SHA validation readback. The public wake event carries no credential and no executable product prose; the private control work order is fetched only after the trusted workflow starts.

Codex runs read-only against the product checkout and returns a patch as data. The trusted runner validates the patch path set before applying it. Secrets, auth files, product source contents, generated patches, and private work-order prose must not be uploaded as artifacts or echoed into public Actions logs.
