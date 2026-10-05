# Runner Agent Rules

1. This repository contains only generic open-source dispatcher runner code. Never add private product source, customer data, credentials, OAuth tokens, signed URLs, deployment configuration, or copied private control documents.
2. Keep private repository topology and authorization in runtime configuration. Do not make a public runner file the source of truth for private project state.
3. Never print or upload `auth.json`, refresh/access tokens, GitHub tokens, or other secret values. Secret material may exist only in protected secret storage and ephemeral runner files with restrictive permissions.
4. Secret-using workflows must run only from trusted code on the default branch and must not use `pull_request_target` with untrusted pull-request content. Pull-request validation must run without private secrets.
5. The read-only and historical control-write pilots are complete. The legacy control-write pilot workflows are retired and the runner no longer requires a `CONTROL_WRITE_TOKEN`. A Dispatcher v2 product run may write one private product repository only when an exact private DEV-CONTROL work order names the product, source SHA, allowed paths, exclusions, validation workflow, and integration authority. Product credentials remain dedicated per product. A Dispatcher v2 product run never grants Production, deployment, database migration, EAS, signing/device, release, credential, destructive, cross-product, or KDN authority unless an exact work order and product rules explicitly permit that separate stage.
6. Use the published `openai-codex` Python SDK. Keep Codex in `Sandbox.read_only` with `ApprovalMode.deny_all` for control-plane orchestration. Deterministic repository mutation belongs to the trusted runner after it validates the bounded model handoff.
7. Independently verify repository identity before and after every Codex turn. For the pilot, exact SHA and clean-worktree evidence come from Git itself; model prose, formatting, or tool choice is never repository-state evidence.
8. Persist a rotated Codex file credential only by replacing the protected GitHub Actions secret after a successful run. Never preserve the credential in artifacts, caches, logs, source, commit history, or PR comments.
9. Keep authentication, private-repository access, and future source-write authority as separate credentials with the narrowest practical permissions.
10. A failed or missing external gate is reported as a gate. Do not fake PASS evidence or broaden authority to work around it.
11. Every source-writing Dispatcher v2 workflow must expose the canonical progress phases in `docs/ACTIONS_PROGRESS.md`. Progress output is observability only: never print secrets or arbitrary private task prose, and never treat a progress marker as authority or as evidence for a stage that was not independently verified.
12. Product code generation uses read-only Codex inspection plus a structured unified-diff handoff. The trusted runner must validate every patch path against the private work order before applying it, establish Draft-PR STARTED before validation, and bind PR-head and integrated validation to exact Git SHAs. Model prose and patch intent are not evidence by themselves.


13. Dispatcher v2 wake identity is product- and task-generic but must remain owner-authored and exact-SHA bound. Each product workflow may derive only its own private control-record path `tasks-v2/<PROJECT>/<TASK-ID>.json`; private scope and authority remain in that record, not in public workflow code.
14. Before any product v2 write run, reject another open same-product `job/` pull request targeting that product's active development branch. One source-writing task per product remains mandatory; different products may run independently and concurrently.

15. Provider-action Dispatcher v2 runs are a separate execution class from source-writing runs. They must not fabricate a product diff or Draft PR. They require an exact private control record with `operation_type: provider_action`, exact current product SHA, successful exact-SHA source validation, and an allowlisted provider action. Credential mutation, device mutation, and destructive authority remain false. Production authority remains false by default and may be true only for a repository-owned GitHub workflow dispatch whose exact workflow path matches the product adapter's registered production-workflow allowlist. New-build authority remains false except for a separately validated, project-scoped build action whose control contract explicitly requires it; the initial exception is CLASSMO `eas_build` preview iOS only.
16. Common provider orchestration may be shared, but provider credentials remain project-scoped and are passed only by that product's thin workflow adapter. Missing provider credentials terminate as `MANUAL_REQUIRED`; never reuse another project's credential or broaden a product source token into provider authority.
17. Provider completion evidence proves only the named provider stage. EAS update publication never proves device receipt; GitHub-hosted cloud build/export never proves Windows physical QA; Cloudflare deployment never proves iOS behavior. Physical and device stages remain separate.

18. Long external provider queues must not keep the launch runner blocked. Capture
the exact external run ID, record QUEUED evidence on the wake issue, and exit
without terminal PASS. A later exact Owner `[PROVIDER-STATUS]` issue comment may
query that same run ID; status reconciliation must never create or resubmit the
provider action.


19. A source-writing task that reaches final FAILED, MANUAL_REQUIRED, or CANCELLED
must not leave its exact Dispatcher Product PR open after bounded recovery is over.
Before close, bind project, task_id, attempt, Control SHA, source SHA, Product PR
metadata, terminal authority run, and final recovery state. Close only that exact PR;
never merge it and never delete its job branch, commits, checks, conversation, or
terminal evidence. Cleanup failure is residue on the same terminal result, not a
reason to upgrade or replace the result.
20. Startup concurrency remains fail-closed. Every open same-product job/* PR blocks
new source mutation. If exact prior terminal evidence proves the open PR is stale,
diagnose that residue explicitly but do not silently ignore or auto-close it from
startup; terminalization/terminal-guard cleanup owns PR closure.

21. Same-project sequential source work uses the GitHub-native persistent source queue.
Owner-authored source registrations remain durable runner issues; the queue controller, not a
ChatGPT foreground session, owns waiting and successor activation. Source product workflows
must not start directly from an issue-open event.
22. A queued successor may use only control schema v2
`source_base.mode: predecessor_integrated_sha`, bound to the exact predecessor Task-ID,
Control SHA, Attempt, and owner wake issue. The resolved source SHA must equal the exact
predecessor COMPLETED `integrated_sha`; arbitrary current-HEAD resolution is prohibited.
23. Successful source product jobs explicitly dispatch the queue controller from a separate
trusted finalizer job after exact COMPLETED, validation, closed merged-PR readback, and writer
release. `workflow_run.completed` is a fallback. Controller workflow_dispatch reconciliation
may re-evaluate an existing queue issue or exact source run without new source authority or a
polling loop. All paths share the persistent DISPATCHED fence; an uncertain POST receipt must
never release that fence or resubmit. Bind project, predecessor task/attempt/control/wake/run,
integrated SHA, and successor issue. Runner-local bounded source validation remains allowed.
24. Before any queued source mutation, the product workflow must re-read the owner queue
issue, exact private control record, predecessor terminal evidence when applicable, and exact
resolved source SHA. The active project branch must still equal that exact source SHA.
25. FAILED, MANUAL_REQUIRED, CANCELLED, timeout, cleanup residue, or any non-success source
run never advances a queued successor. Terminal Product PR cleanup and startup concurrency
remain fail-closed and use the same queue/source identity.

26. Only github_workflow_dispatch may wait up to 600 seconds inside the trusted launch
runner for its captured exact run, checking at 15-second intervals. Every read must match
repository, workflow, run ID, and source head SHA. Terminal success/failure/action-required
is persisted once; pending timeout retains QUEUED and Owner status reconciliation. EAS
workflow/build and other long external queues still hand off immediately without waiting.
27. A persisted provider terminal is an idempotent status/mail retry checkpoint, never a new
provider action. Exact Notification-Key readback precedes matching STARTED Trash cleanup;
verify both Trash presence and Inbox absence. Terminal RESULT stays for Owner review.
28. Bounded repairs must have real changes only in original allowed_paths. Required task
paths apply to the final cumulative source-base diff, including worktree changes, rather
than every repair commit. A later repair may restore an earlier revert. Before integration,
require the final cumulative required paths even if Actions validation passed.
29. Source repair must be the smallest COMPLETE repair of all actionable items reported
together, with dependencies closed. Before writing, reject plans that leave explicit
allowlisted missing artifacts or SQL/SHA256SUMS obligations unresolved. Recognized validator
edits require repository-verifiable evidence that its reported source condition is already
false; changing diagnostics alone is not a source repair. Use at most two replacement plan
generations inside the same repair attempt against the unchanged checkout. These rejected
plans consume no repair commit. Keep the existing maximum of two repair commits, exact
head/integrated validation, scope/security boundaries, terminal cleanup, and KDN exclusion.


Each read-only source repair-plan turn is bounded by a Linux main-thread 480-second
wall-clock deadline. Timeout closes its fresh SDK session and enters the existing same
repair pre-write regeneration loop: at most two replacements, no rejected writes or
commits. Committed validation repair maximum remains two; the outer job stays at 60 minutes.

Product Dispatcher Terminal Guard and reusable Source Terminal Finalizer Core own
external terminal reconciliation after source job failure/cancel/timeout. Triggers are
workflow_run.completed, explicit non-success handoff and exact Owner orphan reconciliation.
Each product workflow persists an exact run/PR/head checkpoint before STARTED mail and
updates it after each repair push. Verify exact workflow path/run/repository, queue claim,
private Control SHA, resolved Source SHA, project/task/attempt/revision, target and PR/head
before mutation. Dynamic run names are not workflow identity. Never search/adopt another PR.
A legacy orphan needs an Owner comment on its exact wake:
`[SOURCE-TERMINAL-RECONCILE] run=<RUN-ID> pr=<PR-NUMBER> head=<40-CHAR-SHA>`.
It never creates execution authority. Close only the exact unmerged failed PR, preserve
branch/history, reconcile one terminal checkpoint and close/read back the exact wake.
Deliver native Gmail RESULT by exact Notification-Key, read it back, then Trash only its
matching STARTED and verify Trash present / Inbox absent. Preserve partial-cleanup residue.
Retries must not duplicate comments, mail or close mutations. Never downgrade source
completion after a successful product job. KDN, source/provider/device writes, integration,
new attempts and ChatGPT foreground polling remain outside finalizer authority.
