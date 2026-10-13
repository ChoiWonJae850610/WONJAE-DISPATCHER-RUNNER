# One shared GitHub PAT for current and future Owner products (preparation)

**Status: PREPARED ONLY. No PAT created, Secrets edited, permissions changed,
product dispatched, PR merged, or Production deployed by this change.**

The Owner operates registered products in their personal GitHub account.
The current DEV-CONTROL PROJECTS.yaml registry is authoritative for which
products are allowed, and SANJINWORKS remains the only Owner source/provider
execution trigger. A PAT's broad GitHub access does NOT expand the list of
registered runnable projects or bypass exact SHA, CI and Owner admission.

## One-time Owner setup

1. Open https://github.com/settings/personal-access-tokens/new
   and create one **Fine-grained** token, e.g. WONJAE-ALL-PROJECTS.
2. Resource owner must be the account owning all registered product
   repositories, currently ChoiWonJae850610. Under **Repository access**
   choose **All repositories**. GitHub documents that this includes future
   repositories under the same personal owner, not just today’s list.
3. Repository permissions for the presently registered GitHub repository
   workflows (read/write where available):
   - Contents: Read and write (private checkout, source PR, state PR, merge)
   - Pull requests: Read and write (PR creation, review metadata, merge)
   - Actions: Read and write (exact validation, workflow dispatch/review)
   - Issues: Read and write (lifecycle and reconciliation)
   - Workflows: Write (only when actual source work is authorized to edit
     GitHub Actions workflow files in the source repository)
   - Metadata: auto Read.
   Do not grant Administration, Secrets, repository deletion, billing or
   unrelated organization/account permissions merely for this setup.
4. Generate once, copy value locally and enter it **only in the trusted
   Runner repository GitHub Actions Secret**:
   OWNER_GITHUB_TOKEN. Repository: ChoiWonJae850610/WONJAE-DISPATCHER-RUNNER.
   Never put the PAT in ChatGPT, screenshots, issue/PR comments, CI logs,
   plaintext files, browser URL parameters or source commits.

The new workflow aliases use one secret in all active registered product
source next/retry/resume, product document PR integration, Provider handoff,
reconciliation, and bounded recovery routes. Archived product-v2 source
flows also prefer the same secret. Every caller keeps the current
<PROJECT>_WRITE_TOKEN **fallback**, so a not-yet-created shared token
does not strand already working projects. The new secret is preferred only
after the Owner explicitly stores it.

The original per-project secrets are NOT deleted or rotated by this PR.
After the common PR is independently approved/merged, exact integrated
Runner Validation PASSES, the Owner configures the new Secret and a
subsequent normally Owner-started registered product action verifies it,
the Owner may revoke obsolete GitHub *product-write* PATs individually.
Do not delete while a workflow still actively references the fallback
and the central Secret has not been proven. Deleting a GitHub Secret name
without first verifying its usage cannot delete a PAT from the GitHub
account; actual token revocation is a separate Owner action.

## Credentials this deliberately does not combine

- CONTROL_READ_TOKEN is a distinct control-plane read-only boundary.
- COMMON_PR_MERGE_TOKEN/COMMON_PR_MERGE_APPROVAL_SECRET are a separately
  approved common-system PR merger/Owner gate, not normal product source.
- SECRET_ROTATION_TOKEN rotates Codex auth and is separately authorized.
- CODEX_AUTH_JSON, Gmail account/app password, Expo/EAS tokens,
  Supabase Management credentials, Cloudflare tokens, PostgreSQL password
  and SSL CA, iOS signing credentials are not GitHub repository PATs and
  cannot be replaced by a GitHub PAT.
- Historical *provider-v2* tokens (e.g. ESC_PROVIDER_GITHUB_TOKEN)
  retain their original boundaries until an independent, reviewed
  decommission/migration; the ordinary SANJINWORKS Provider gate already
  uses the new shared product-read alias.

This is not a single credential for every external platform. Keeping those
distinct avoids accidentally granting public/published/provider authority
to a general-purpose GitHub source-token workflow.

## Future products

The Fine-grained PAT's **All repositories** access covers repos created
later by the selected resource owner. The product must still be added to
DEV-CONTROL/main PROJECTS.yaml, its own canonical source validation and
execution state registered, and the common direct-worker/Provider caller
routing extended if necessary. Those **routing changes do not require
minting a new GitHub PAT or adding another project-specific GitHub Secret**.

A different resource owner (e.g. a separate GitHub organization) is not
included in this single-owner token; it requires a separate installation/
credential decision. Since a single leaked all-repos PAT affects more
repositories, store it only in the trusted Runner repository’s Actions
Secrets, never in the SANJINWORKS browser or per-product app runtime.
Rotate it in one place on expiration or suspected compromise.

Official GitHub docs:
https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens
