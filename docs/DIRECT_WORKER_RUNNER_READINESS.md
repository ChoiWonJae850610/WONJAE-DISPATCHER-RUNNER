# Direct Worker nonsecret Runner readiness — Owner action

Status: **prepared code / not an authenticated Codex turn / not a source run**.

SANJINWORKS may offer `Runner 환경 진단` for a product whose current GitHub-owned execution state is a blocked `DECISION_REQUIRED` requiring Runner/Codex readiness. Its authenticated Owner action dispatches `.github/workflows/direct-worker-runner-readiness.yml` on trusted Runner `main` with the current registered product, product HEAD and DEV-CONTROL HEAD.

This workflow runs on the existing Linux direct-worker runner and checks the OS, Python/Git availability, free memory/disk, and optional unauthenticated DNS/HTTPS to a public API hostname. It intentionally injects **no Codex Auth secret**, does **not run Codex**, and **does not rotate tokens**, make product Git changes, create a product PR or invoke a provider/build/OTA. The GitHub job summary is a quick clue to host readiness only; a green job is not proof that authenticated Codex, model turn or the original timeout is fixed.

The original `DECISION_REQUIRED` gate persists. Restoring a smaller preapproved product `SOURCE_READY` stage takes a **separate product document/state PR** and authenticated SANJINWORKS document Owner merge after sufficient diagnostic evidence. The source writer remains a separate later Owner action.

SANJINWORKS button must be disabled or absent on unrelated QA/Provider/Production decisions. Server must recheck registered project, exact current product HEAD, control HEAD and explicit recovery-gate wording before dispatching. All detected permission/source identity conflicts fail closed. Root-cause uncertainty remains visible and can be discussed in GPT at the Owner's choice.

This phase prioritizes speed and low ceremony; it does not pretend to provide every missing recovery executor or QA workflow.
