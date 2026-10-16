# All registered personal products — lean CI, trusted workflow maintenance

Status: **PREPARATION / OFFLINE ADMISSION IMPLEMENTED / NOT OWNER-EXECUTABLE** (2026-10-16 KST). Parent issue: [DEV-CONTROL #310](https://github.com/ChoiWonJae850610/DEV-CONTROL/issues/310). No registered product, Runner job, PR merge, external Provider, credential or Production change is performed by this PR.

## Source of truth and dynamic scope

Load active products **at use time** from the exact current `ChoiWonJae850610/DEV-CONTROL/main:PROJECTS.yaml`. Do not code a product allowlist. The registered repository, development branch and `ci.validation.name/path` are the only candidate targets; `ci.deployment` is never a validation workflow candidate. A future newly enrolled product with a canonical validation workflow must be selected without editing this Runner module.

2026-10-16 source snapshot (each `cloud-dev-v1` branch; re-read before subsequent Owner execution):

| Product | Current SHA | Canonical validation workflow | Focused default (future product CI work) | Current heavy work to remove from routine CI |
| --- | --- | --- | --- | --- |
| WAFL | `addd1792f1924764ea2f3173005532ff63645df4` | `.github/workflows/wafl-cloud-validation.yml` | Changed SQL/G1-V1 tests and targeted app checks; security/RLS when affected | Two npm workspace installs, Chromium/Playwright, double Node setup, historic 328+ tests, unrelated Next/Expo build |
| CLASSMO | `6df7d54401bca708e1089fe9a0b806cff2e0ef50` | `.github/workflows/classmo-cloud-validation.yml` | Targeted affected domain tests and relevant type/Expo smoke only on mobile changes; Auth/Storage when affected | Unconditional npm installation, broad tests and Expo export |
| ESC | `7615e84d6e13da0e5a48b3d7a8eea30ad6097346` | `.github/workflows/validate.yml` | Affected event/Cloudflare config tests and changed-file lint, mobile smoke only on native changes | Repeated npm tests/lint, unrelated Expo config and iOS bundle export |
| MUVEL | `efce748ac13c80d0178a740a5038d45c4906ff55` | `.github/workflows/cloud-dev.yml` | Focused .NET compile/domain tests for changed logic; Godot/export smoke only on platform/package changes | Full domain regressions, snapshot regeneration, Godot template download, complete Windows debug export and artifact round-trip |

**CLASSMO caveat:** Product source PR #141 is currently open. Do not start an overlapping writer or forcibly merge/remove it. ESC and MUVEL existing Owner physical QA steps are independent and must not be reclassified as passed or removed. Real data, Auth, RLS, Production, deployment, credentials and signing still require their separately scoped Owner gates.

## Shared minimal-check selector (this Runner PR)

- `src/wonjae_dispatcher_runner/lean_ci_maintenance.py` reads `PROJECTS.yaml` dynamically and classifies changed Git paths as `docs`, `affected_source`, `sensitive_source` or `workflow_maintenance`. Returned checks are recommendations for future canonical jobs, **not executed test results**; never default to the full historic suite solely because a file is unfamiliar.
- For docs or execution-state changes: syntax, links/state and identity. For source: affected tests plus minimal necessary type/build. For DB/Auth/RLS/permission/provider paths: relevant security/target contract only. For workflow edits: restricted route and exact-head CI; never silently treat workflow source as ordinary source.
- The Runner's existing Direct Worker keeps `.github/**`, `.wonjae/**`, `AGENTS.md`, `PROJECT_RULES.md` read-only and mutation-protected. None of those protections are relaxed or bypassed.

## Restricted workflow-maintenance admission — read-only first stage

`scripts/workflow_maintenance_preflight.py` is a **local read-only** CLI intended for a future independently authorized trusted Runner workflow. It loads the exact registered control checkout and product checkout and verifies:

1. Exact 40-character control/product source SHA and clean product worktree; product identity and canonical validation workflow come from the registry, not an arbitrary request path.
2. Exact current/proposed workflow SHA-256 content digests and a regular non-escaping registered workflow file.
3. Well-formed YAML with no duplicate mapping keys; unchanged canonical workflow name, triggers, top-level permissions/concurrency/environment, job identities and job-level runner/permissions/environment; no new external Actions references and no secret expression introduced by the proposed workflow.
4. A bounded **preview receipt** identifying proposed content hashes and the smallest relevant check set. No workflow, source, Git refs, Secrets or remote state is written or changed; no checkout, push, PR, CI, deployment or Runner dispatch is attempted by this command.

These syntactic/invariant checks **do not prove semantic safety of arbitrary run shell commands**. A future trusted implementation must obtain an authenticated SANJINWORKS Owner action for exact project/source/workflow/effect, review deterministic candidate content on an explicit path allowlist, independently re-fetch exact Git authority, and apply one fully reviewed change with existing PR-head/integrated-SHA source-validation rules. Do not bridge this command directly to a mutation endpoint or interpret a JSON input flag as authorization. A workflow file itself is code, and editing it is a more sensitive boundary than ordinary UI source.

## Remaining implementation sequence

1. **This PR:** common Runner offline selector, read-only admission, small focused tests, and four-product rollout specification. Open a normal common-system PR and observe its exact PR-head CI; leave main unchanged.
2. **Separate explicit authorization:** design and integrate the authenticated Owner-to-trusted Runner workflow-maintenance *writer* and signed evidence boundary. Keep the ordinary Direct Worker .github sandbox locked; restrict executable changes to the registered `ci.validation.path` only, using reviewed path-specific diffs.
3. **Four separate product deliveries:** register each product's bounded maintenance source task through its own docs/state route and Owner action, then product-specific canonical CI PR and integrated validation, maintaining correct security/provider checks. Do not use a one-size-fits-all Node/.NET/Godot command.
4. **Future products:** pick up canonical validations automatically from `PROJECTS.yaml`; apply the same reviewed Owner route without hardcoded new project names.
5. **Optional full suites:** explicit manual/major-change/release workflows only, not an ordinary DEV gate. Preserve exact evidence and do not turn unrun tests into PASS.

No new GitHub Actions workflow, webhook, credential, permission, Production deploy or generic product source writer is activated in this PR. The Writer and Owner-control hookup are deliberately **NOT IMPLEMENTED** until independently authorized.
