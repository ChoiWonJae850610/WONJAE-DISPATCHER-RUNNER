# Runner Agent Rules

1. This repository contains only generic open-source dispatcher runner code. Never add private product source, customer data, credentials, OAuth tokens, signed URLs, deployment configuration, or copied private control documents.
2. Keep private repository topology and authorization in runtime configuration. Do not make a public runner file the source of truth for private project state.
3. Never print or upload `auth.json`, refresh/access tokens, GitHub tokens, or other secret values. Secret material may exist only in protected secret storage and ephemeral runner files with restrictive permissions.
4. Secret-using workflows must run only from trusted code on the default branch and must not use `pull_request_target` with untrusted pull-request content. Pull-request validation must run without private secrets.
5. The read-only pilot is complete. In the bounded write pilot, Codex provides a validated structured handoff and the trusted runner may materialize only the explicitly allowed DEV-CONTROL documentation path, then publish its single branch, commit, and Draft PR. It must not create or modify a product TASK, product repository, deployment, release, credential, runtime, database, signed build, device state, physical evidence, or KDN route.
6. Use the published `openai-codex` Python SDK. Keep Codex in `Sandbox.read_only` with `ApprovalMode.deny_all` for control-plane orchestration. Deterministic repository mutation belongs to the trusted runner after it validates the bounded model handoff.
7. Independently verify repository identity before and after every Codex turn. For the pilot, exact SHA and clean-worktree evidence come from Git itself; model prose, formatting, or tool choice is never repository-state evidence.
8. Persist a rotated Codex file credential only by replacing the protected GitHub Actions secret after a successful run. Never preserve the credential in artifacts, caches, logs, source, commit history, or PR comments.
9. Keep authentication, private-repository access, and future source-write authority as separate credentials with the narrowest practical permissions.
10. A failed or missing external gate is reported as a gate. Do not fake PASS evidence or broaden authority to work around it.
11. Every source-writing Dispatcher v2 workflow must expose the canonical progress phases in `docs/ACTIONS_PROGRESS.md`. Progress output is observability only: never print secrets or arbitrary private task prose, and never treat a progress marker as authority or as evidence for a stage that was not independently verified.
