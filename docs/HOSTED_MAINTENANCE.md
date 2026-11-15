# Hosted maintenance after home Linux retirement

Common Control PR Merge, failed-source recovery document preparation and existing
source-PR document attachment/reconciliation use `ubuntu-latest`. These utilities
use Git, GitHub CLI and Python from the hosted image; they do not need a home
Runner, local Codex session or WSL filesystem. Existing Owner, token, HMAC,
exact-SHA, path, prior-run and concurrency checks remain unchanged.

Failed-source document preparation now requires an explicit Owner dispatch with
the exact historical failed run. A completed old Direct Worker run never starts
recovery automatically. Retirement does not dispatch, replay, close or merge any
existing product task or pull request. An ordinary GitHub merge remains subject
to its existing repository permissions and checks.

The retired Direct Worker entry/core and home-only readiness, diagnostic, smoke
and secondary-Runner setup workflows must remain disabled in repository Actions.
Their source is retained as history, not as a new hosted source-development route.
The hosted-only manual authentication bootstrap workflows are independent of the
retired home Runner and are not changed by this migration.

Provider jobs and Windows CI are unchanged. Conditional legacy jobs use hosted
runners only while `SOURCE_WRITER_FENCE_CONFIG` is unset; configuring that variable
would restore a self-hosted dependency and is outside this retirement. Do not
install a shared-fence backend or enable retired workflows as part of cleanup.

Validation uses secret-free existing CI and focused mocked utility tests. It must
not run authenticated recovery or merge workflows against historical tasks just
to test the runner-label change.
