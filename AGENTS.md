# Runner Agent Rules

1. This repository contains only generic open-source dispatcher runner code. Never add private product source, customer data, credentials, OAuth tokens, signed URLs, deployment configuration, or copied private control documents.
2. Keep private repository topology and authorization in runtime configuration. Do not make a public runner file the source of truth for private project state.
3. Never print or upload `auth.json`, refresh/access tokens, GitHub tokens, or other secret values. Secret material may exist only in protected secret storage and ephemeral runner files with restrictive permissions.
4. Secret-using workflows must run only from trusted code on the default branch and must not use `pull_request_target` with untrusted pull-request content. Pull-request validation must run without private secrets.
5. The first pilot is read-only. Do not create or modify a private product TASK, branch, commit, PR, deployment, release, credential, runtime, database, signed build, device state, or physical evidence.
6. Use the published `openai-codex` Python SDK. Run the pilot with `Sandbox.read_only` and `ApprovalMode.deny_all`.
7. Independently verify every Codex claim that is used as evidence. For the pilot, compare the model's reported SHA with `git rev-parse HEAD`; do not infer repository state from prose.
8. Persist a rotated Codex file credential only by replacing the protected GitHub Actions secret after a successful run. Never preserve the credential in artifacts, caches, logs, source, commit history, or PR comments.
9. Keep authentication, private-repository access, and future source-write authority as separate credentials with the narrowest practical permissions.
10. A failed or missing external gate is reported as a gate. Do not fake PASS evidence or broaden authority to work around it.
