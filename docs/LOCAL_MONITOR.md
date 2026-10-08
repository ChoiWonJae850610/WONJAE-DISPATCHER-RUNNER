# Home-Linux Local Heartbeat — Stage 5

**Prepared source implementation / no runtime installation or credential configuration yet.**

This module is a liveness supplement for the registered Direct Worker Codex step, **not** a work scheduler, alternative Owner dispatch, validation verdict, or GitHub status authority. Only SANJINWORKS authenticated Owner action initiates registered product source work. The approved GitHub Actions trusted workflow still writes source, tests, PRs and integration.

## Files and cadence

- `.github/workflows/direct-worker-core.yml` wraps only the already-authorized `run_direct_worker_recovery.py` Codex stage with `python scripts/local_monitor.py supervise ... -- python scripts/run_direct_worker_recovery.py ...`. It passes the unchanged command/exit code through.
- For each unique run/attempt, the local runner user writes a private `/var/tmp/wonjae-monitor/<run_id>-<run_attempt>/` directory at mode 0700 (files 0600): `meta.json`, `phase.json`, atomic `heartbeat`, and terminal `result.json`.
- Heartbeat is updated every **1 second**, on local filesystem only. The worker wrapper checks the actual child PID and supervisor PID identities using Linux `/proc/<pid>/stat` start ticks and does not print environment tokens.
- The separate, optional `bash scripts/monitor.sh` checks local state every **2 seconds**. `abnormal` is a process-health diagnostic when PID/start ticks do not match or heartbeat is older than 15 seconds; `error` is a child process exit code, **not** a canonical GitHub FAILED. Normal completion/idle is neutral.
- Optional authenticated HTTPS relay is enabled only after the Owner separately provisions `WONJAE_MONITOR_ENDPOINT` and a distinct `WONJAE_MONITOR_SECRET` of at least 32 bytes for the monitor service. It sends a bounded job snapshot immediately on status change and otherwise every **30 seconds**. No per-second network/DB writes, provider credentials or database tables.
- Remote body `{version:1,host:"home-linux",sentAt:<unix seconds>,nonce:<random>,jobs:[{project,runId,runAttempt,sourceSha,phase,state}]}` uses `X-Wonjae-Monitor-Signature: sha256=<HMAC-SHA256 raw body>`. Only status/identity, no source, customer details, access tokens or arbitrary log/prose.
- Without endpoint/secret, the watcher runs **local-only**. If local job-file registration fails, trusted code reports a warning and still invokes the approved source command; monitor failure never changes source execution authority or its return code.
- For a one-shot read (no connection): `bash scripts/monitor.sh --once`. To run continuously: `bash scripts/monitor.sh` under a separately configured supervised Ubuntu service. Do **not** start as an unbounded GitHub Actions job or install/enable a systemd service from ChatGPT.
- Old finished records are ignored after 120 seconds; unclean abnormal records after one hour. Disk cleanup of old monitor-only records, if needed, is a separate approved safe maintenance task, never a broad root cleanup.

## Acceptance gates

1. Runner Validation Ruff, pytest, compileall and existing Linux isolation regression at exact PR head; automatic CI triggered by the PR is not a SANJINWORKS product job.
2. A real authorized Direct Worker run on home Linux actually produces private heartbeat/meta/result files; confirm no source/test/merge behavior change. This **cannot** be claimed from unit tests alone.
3. SANJINWORKS matching signed relay endpoint + per-host ephemeral observation + red border are a separately reviewed common-system code PR. The SANJINWORKS Worker uses a separately approved monitor secret; no cryptographic trust before that.
4. A real subprocess crash or missing heartbeat should display red **process health** only on a matching GitHub active run; queued but not yet started, successful terminal, missing monitor configuration, unsupported host, DO-restart unknown and manual QA do **not** become red FAILED.
5. No home Ubuntu service registration or Cloudflare Production deploy is part of this PR.

**Note:** This first wrapper intentionally only observes the source-Codex step. Product repository Windows validation and GitHub-hosted Provider Gates are still observed through their separate official GitHub Actions evidence. A heartbeat cannot prove source progress or job completion.


## Restricted egress bridge (Stage 6 source proposal)

See [RESTRICTED_MONITOR_BRIDGE.md](RESTRICTED_MONITOR_BRIDGE.md) for the opt-in
Unix-socket process-health relay. The installed local watcher remains
\`PrivateNetwork=true\`; the separate signer uses a fixed HTTPS destination,
an exact Linux UID gate and a protected systemd credential. This stage does not
configure secrets, register services or perform an actual Production check.
Application-level fixed-host egress must not be represented as a kernel-level
DNS allowlist; a stricter outbound policy requires separate verification.
