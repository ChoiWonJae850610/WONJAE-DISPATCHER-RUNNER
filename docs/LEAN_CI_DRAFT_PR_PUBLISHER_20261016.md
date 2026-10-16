# All-product lean CI — signed Owner draft PR publisher (preparation)

**State 2026-10-16:** CODE PR PREPARED / NOT ENABLED / NOT USED ON PRODUCTS. Source authority is current DEV-CONTROL `PROJECTS.yaml`, all active personal products, including future registrations. Control tracking: [DEV-CONTROL #310](https://github.com/ChoiWonJae850610/DEV-CONTROL/issues/310). Runner baseline: `9431e5b60d782b76ac619fc3195f648918c5dc99` / exact Runner Validation #38855948438 SUCCESS. Existing read-only preflight and signed ephemeral stage remain protected.

## Owner-only publication path

This PR adds a separate, **default-disabled** `.github/workflows/lean-ci-owner-publish.yml`. The sole trigger is `workflow_dispatch`; the job condition requires Runner/main, the repository Owner as initiating actor, and **a separately approved, still unconfigured** `LEAN_CI_OWNER_PUBLISH_ENABLED=true` Actions variable. `permissions: contents: read` stays unchanged. It reuses the pre-existing Owner GitHub PAT Secret only if a future credential/activation decision independently permits this exact PR-only scope. This PR **does not create or update** that secret, variable or any credentials.

The dedicated `registered-lean-ci-pr-publish-v1` signature domain differs from the prior stage-only Owner claim. A stage-only HMAC cannot authorize a PR. Before any private checkout the Runner checks a ten-minute signed exact project/repository/branch/registered-workflow/control/product/old/new SHA-256 claim. It independently checks latest control/product SHA, repository origins, clean source and registered canonical validation workflow, strict YAML/permission/action invariants and exactly one dirty workflow file in a disposable checkout. It blocks if **any current product PR** targets the registered development branch (e.g. CLASSMO #141), or if its deterministic dedicated lean-ci branch already exists.

After a future **separate authenticated Owner approval**, the restricted publisher can use GitHub Git data API to create one exact validation YAML change on a dedicated `lean-ci/` branch and open one **draft** product PR. It must not edit a nonregistered `.github/**` file, `.wonjae/**` or any product source; it must not delete a conflicting existing PR. It does not auto-merge, auto-retry, run a Provider, change a credential or deploy. Canonical product CI will still run according to the product's existing PR workflow; the publisher records **PR_HEAD_CI_NOT_VERIFIED** and never fakes SUCCESS. A future separately approved integration/CI gate checks the exact PR-head and integrated SHA before marking any product stage complete.

## Delivery constraints

- Every existing/future active registered product is discovered from **live** DEV-CONTROL `PROJECTS.yaml`; no WAFL-only module and no hardcoded four-product permission map.
- Normal Direct Worker `.github/**` and `.wonjae/**` protection is untouched. The publisher is a separate trusted Owner-only workflow and never exposes arbitrary paths.
- CLASSMO #141 remains open and blocks publication. WAFL, CLASSMO, ESC and MUVEL product CI are **unchanged** by this preparation PR.
- A YAML structural diff check cannot prove arbitrary shell commands safe. Product-specific lean candidates must be reviewed in SANJINWORKS UI and scoped to affected checks. Sensitive Auth/RLS/SQL/permission modifications retain real risk checks.
- An incomplete/ambiguous GitHub push/PR response must remain FAILED/MANUAL_REQUIRED; never automatically retry or delete a branch. No token or proposal text in logs.
- Real Owner review UI is prepared separately in a SANJINWORKS source PR. **Neither PR may be merged/activated by this task.** SANJINWORKS main integration has an independent Cloudflare Production approval boundary.

Future rollout: first authorize both common PRs independently (SANJINWORKS production-effect approval is separate), then explicitly review future stage/PR activation without bypassing credentials/permissions. Finally prepare product-specific lean YAML candidates and start one approved Owner product CI PR at a time; stop for existing source PRs and required real provider gates. Existing full suites become optional major change/release diagnostics rather than normal daily gating **after actual product CI PRs integrate**, not before.
