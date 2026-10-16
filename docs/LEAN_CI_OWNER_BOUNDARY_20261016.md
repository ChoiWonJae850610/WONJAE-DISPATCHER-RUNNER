# Signed Owner lean CI maintenance — common Runner handoff

**Status: CODE PR PREPARED / DEFAULT DISABLED / NO PRODUCT GIT PUBLICATION**, 2026-10-16. DEV-CONTROL [all-products plan #310](https://github.com/ChoiWonJae850610/DEV-CONTROL/issues/310) and integrated Runner #298 `c1f66256fddfdde9e284d245fbffe577a835f030` / [Validation #38852627230](https://github.com/ChoiWonJae850610/WONJAE-DISPATCHER-RUNNER/actions/runs/38852627230) SUCCESS are the prerequisites.

## Dynamic registered boundary

Load **all active/future products** from exact current `DEV-CONTROL/main:PROJECTS.yaml`. Trust only each product's registered `repository`, `branch`, and `ci.validation.name/path` (not deployment workflow). The earlier `lean_ci_maintenance.py` enforces SHA/owner/repository origin, clean local worktrees, matching old/new SHA-256, unchanged workflow name/triggers/permissions/jobs/runner class and no new arbitrary external Action `uses`. No permanent hardcoded project names.

## Stage-only, never Direct Worker .github unlock

- `src/wonjae_dispatcher_runner/lean_ci_owner_stage.py`: requires a 10-minute maximum signed Owner selection, exact SHA/repo/branch/path/old/new SHA256, and approved candidate; after read-only preflight it **only changes one workflow YAML inside a disposable local checkout**. It never commits, pushes, creates or merges PRs, invokes product validation or touches a Provider. The ordinary Direct Worker still mounts `.github/**` read-only and rejects modifications.
- `scripts/check_lean_ci_owner_claim.py`: validates a signed JSON claim **before** any private product clone; only after valid attestation may it write sanitized identity fields to GitHub Actions environment.
- `scripts/stage_lean_ci_owner_workflow.py`: local stage receipt, never a PASS or external effect.
- `.github/workflows/lean-ci-owner-stage.yml`: independent manually-dispatched **read-only** GitHub job, default disabled by absent `LEAN_CI_OWNER_STAGE_ENABLED=true` GitHub Actions variable and dedicated `LEAN_CI_APPROVAL_SECRET`. It uses existing GitHub access only to read exact control/product source and has `permissions: contents: read`. No webhooks, push triggers, PR creation, source execution, credentials or Production effect. A GitHub-only direct dispatch lacking the SANJINWORKS Owner signature is rejected.
- Dedicated signing secret in Worker and Runner (same value across separately protected stores) is a **future separate Owner credential approval**, not created, populated or activated by this PR. Do not reuse an unrelated Provider, GitHub PAT or common PR merge HMAC as the maintenance HMAC.

A staging-only job can report `STAGED_LOCALLY_NOT_PUBLISHED` but must **never be reported as changed product CI, product CI PASS or a completed feature rollout**. It is a code prerequisite; not a functioning product workflow publisher.

## Required follow-up for actual CI changes

A separately reviewed, Owner-authenticated trusted publisher must attach a reviewed proposal to the **exact registered validation workflow path only**, block concurrent `direct/` or other product source PRs, create one exact product PR, observe exact PR-head canonical CI, merge only with separately approved integration scope, then validate integrated SHA. Semantics of shell command diffs also require explicit human or trusted review; invariant-preserving YAML parsing alone cannot prove a script safe. Do not treat the Owner staging mode flag as authorization to publish. Do not modify existing Direct Worker protection or automatically expose the staging control as an executable production button.

Current target-specific work after separate product Owner authorization: WAFL skip default Chromium/mobile/full historic contracts; CLASSMO affected-domain checks rather than mandatory Expo export; ESC narrow API/ui checks rather than repeat Expo export; MUVEL focused .NET/Godot checks rather than full Windows export/snapshots. Keep Auth/RLS/tenant/permission or release/Provider safety checks when those paths actually change.

No Runner/main merge, product source/CI modification, Owner workflow dispatch, credential mutation, permission expansion, Production or Provider operation in this PR.
