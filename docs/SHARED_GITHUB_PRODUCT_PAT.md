# Single GitHub PAT and single Cloudflare API token (Owner decision 2026-10-14)

**Status: SOURCE PREPARATION ONLY.** Workflow references and offline tests are
updated on this PR. No Secret values are created/rotated, no workflow is
dispatched, no source PR merged and no Production deployment is authorized.

## Exactly one Owner-managed GitHub PAT

The Owner's personal GitHub repositories are selected by GitHub Fine-grained
PAT Resource owner `ChoiWonJae850610` and Repository access `All repositories`.
The Owner has already regenerated the selected PAT in GitHub and must retain
the new value privately; source control never includes the token string.

| Secret store | Single GitHub PAT key | Consumers |
| --- | --- | --- |
| Runner repository Actions secrets | `OWNER_GITHUB_TOKEN` | Control checkout, source PR/CI, document PR, Codex auth Secret rotation, common PR merge, Provider-side GitHub reads/dispatch, recovery and reconciliation |
| SANJINWORKS Cloudflare Worker Secrets | `SANJINWORKS_GITHUB_TOKEN` | GitHub snapshots and Runner online-status reads, registered Actions dispatches, common Owner PR dispatch when independently activated |

These are **two Secret locations containing the same GitHub PAT value**,
not two separately generated PATs. Previously referenced Runner GitHub PAT
Secrets (`CONTROL_READ_TOKEN`, `SECRET_ROTATION_TOKEN`,
`COMMON_PR_MERGE_TOKEN`, `*_WRITE_TOKEN`,
`*_PROVIDER_GITHUB_TOKEN`) are no longer called by the active workflow
sources after integration; once confirmed unused they may be deleted.
The GitHub-provided `github.token` is a different automatically minted
workflow-scoped credential and is not a manually managed personal PAT.

Permissions needed for the chosen functionality depend on the guarded action:
Contents, Pull requests, Issues and Actions read/write; repository
Administration read for self-hosted Runner status; Actions Secrets write for
persisting refreshed Codex auth; GitHub workflow content write only for
separately approved workflow-file edits. A PAT's permissions do not replace
Owner button, exact-commit CI, trusted Runner, project registry or gated
provider/Production approvals. KDN and company repositories remain excluded.

## Exactly one Cloudflare API token

The distinct Cloudflare API token is recorded as `CLOUDFLARE_API_TOKEN`
where required (Runner Actions for the WAFL registered Cloudflare gate,
ESC repository Actions for its Cloudflare deployment). The token value is
the same across these separate GitHub Actions Secret stores. Account IDs,
R2 access keys, signed worker upload tokens and other non-Cloudflare-API
credentials are independent data, not duplicate Cloudflare API tokens.

## Owner-side rollout order

1. Integrate Runner and SANJINWORKS source PRs only after exact PR-head
   validation and any separate SANJINWORKS Production-effect approval.
2. Put the *regenerated* GitHub PAT once in Runner Actions
   `OWNER_GITHUB_TOKEN` and in the SANJINWORKS Worker
   `SANJINWORKS_GITHUB_TOKEN`. Do not paste token values in chat,
   logs, code, PR comments or screenshots.
3. Set the unified Cloudflare token as `CLOUDFLARE_API_TOKEN` in each
   GitHub Actions repository that actually calls the Cloudflare API.
4. Verify the authenticated `/api/equipment/runners` readout and separately
   Owner-triggered GitHub workflows; never use a product workflow merely
   to test this docs/source PR.
5. Only after valid readbacks, retire older personal PATs and obsolete
   secret names. No silent Secret deletion or credential mutation.

GitHub CLI or website API secret changes are an independent Owner action;
this source change does not try to retrieve encrypted values.
