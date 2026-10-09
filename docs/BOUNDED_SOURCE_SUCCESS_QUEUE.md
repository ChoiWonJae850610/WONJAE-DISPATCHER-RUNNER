# Predeclared multi-stage source continuation (shared Runner)

A source-stage queue is an optional **product-owned** extension to the existing
`.wonjae/execution-state.yaml` schema v1. It does not authorize any new product
work by itself, and it cannot be used for automatic provider calls or Production.

## Format

```yaml
schema_version: 1
project: EXAMPLE
next_action:
  type: SOURCE_READY
  title: Stage 1
  source_task_id: EXAMPLE-001
  source_scope: ["Implement approved scope 1."]
after_source_success:
  type: SOURCE_READY
  title: Stage 2
  source_task_id: EXAMPLE-002
  source_scope: ["Implement approved scope 2."]
source_success_queue:
  - type: SOURCE_READY
    title: Stage 3
    source_task_id: EXAMPLE-003
    source_scope: ["Implement approved scope 3."]
  - type: PROVIDER_GATE
    title: Separate provider approval
    source_scope: []
    gate: registered_approval_gate
```

Every bounded source task must still be launched by the authenticated
SANJINWORKS Owner button. Trusted Direct Worker runs its unchanged exact-HEAD
source admission, source scope checks, PR-head canonical validation, guarded
PR integration and integrated-SHA canonical validation. **Within the already
approved source task's trusted state-transition code**, the next action is
advanced one step and the first queued source-success stage becomes the next
`after_source_success`, leaving the other queued stages unchanged. When the
source task finishes successfully, SANJINWORKS sees a new current
`SOURCE_READY` on the integrated product branch and shows the next Owner
button without waiting for another ChatGPT-prepared document PR.

Limits: maximum eight queued future entries; every intermediate stage must be
`SOURCE_READY` with a nonempty **unique** source_task_id and bounded nonempty
source_scope. The queue must terminate at an explicit gate:
`PROVIDER_GATE`, `MANUAL_QA` with the existing immediate owner_checkout
actionability contract, `DECISION_REQUIRED` or `NONE`. A product may never
chain past a provider/physical/manual/release/decision gate automatically. The
registered gate and its own permission checks remain independent.

Fail-closed behavior: a SOURCE_READY successor without a terminating queue,
out-of-order gate, duplicate stage identity, unbounded list or missing manual QA
path contract is invalid and refuses the start/transition. Existing single
source stage → gate documents require no new field and preserve their exact
behavior. This shared Runner feature does not register a product plan, change a
product file, dispatch a product workflow, or permit ChatGPT to merge product
source. Product plan adoption is a separately approved product-owned
documentation/execution-state PR integrated through SANJINWORKS.
