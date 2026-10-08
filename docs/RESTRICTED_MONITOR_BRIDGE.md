# Restricted heartbeat egress bridge — GitHub source only

## Scope and current gate

**PREPARED source, not installed or activated.** The Owner's PC Codex reports that
WSL2 Ubuntu-24.04 runs `wonjae-heartbeat.service` as `choi_won_jae`, with
`PrivateNetwork=true`, and that it stopped before making any Secret/network
change. Keep the existing local watcher, Runner, Windows startup settings and
Production code unchanged. Production SANJINWORKS PR #48 was separately deployed;
this bridge PR does not redeploy it.

The earlier suggestion to add a domain entry to `IPAddressAllow` was invalid:
systemd IPAddressAllow matches addresses/CIDRs, not DNS names. Cloudflare
addresses can rotate and can be shared by multiple hostnames. Do not claim a
kernel-level domain allowlist based on this source PR.

## Boundary and flow

```text
Direct Worker supervised source process
  -> /var/tmp/wonjae-monitor/<run>-<attempt> (private 0700/0600)
  -> existing wonjae-heartbeat.service (PrivateNetwork=true, NO secret)
  -> AF_UNIX /run/wonjae-monitor-relay/relay.sock (bounded jobs only)
  -> distinct, unprivileged wonjae-monitor-egress.service
     - validates Linux SO_PEERCRED = exact Runner uid
     - revalidates frame schema, project identity, size, excludes KDN
     - signs data from root-protected systemd LoadCredential
     - http.client HTTPSConnection hard-coded to sanjinworks.com:443
     - POST /api/monitor/report only, no redirects or proxy support
  -> SANJINWORKS Production (HMAC and replay validation)
```

The networked broker **cannot read private heartbeat files** when installed
under a dedicated unprivileged Linux service account with `ProtectHome=true`.
It receives only a bounded status frame over an authenticated Unix socket.
The local watcher never receives `WONJAE_MONITOR_SECRET` and never obtains
a network interface. Linux `PrivateNetwork=true` permits filesystem AF_UNIX.

The broker has a single **application-level** HTTPS destination; no generic
network proxy, configurable URL or forwarding API exists. This is **not** a
kernel-enforced DNS allowlist: compromise of the broker interpreter or binary
could still initiate other egress. If exact host-level isolation is an
independent hard requirement, do not enable this unit until an audited
DNS-aware egress firewall/proxy or separate namespace with equivalent
controls has been supplied and empirically verified. Never loosen the local
watcher's `PrivateNetwork` to bypass the gate.

## Reviewed files

- `scripts/local_monitor.py`: optional `WONJAE_MONITOR_BRIDGE_SOCKET` output,
  enabled only when exactly `/run/wonjae-monitor-relay/relay.sock`. No Secret
  or external network requirement. Local failure never affects source status.
- `scripts/monitor_bridge.py`: separate Unix peer-validated fixed-target
  HTTPS signer. `CREDENTIALS_DIRECTORY/monitor_secret` is mandatory.
- `docs/examples/wonjae-monitor-egress.service.example`: a **non-installed**
  example of a dedicated unprivileged system service.
- `tests/test_monitor_bridge.py`: offline security and protocol regressions.

The legacy `WONJAE_MONITOR_ENDPOINT` direct-watch path remains for
backward compatibility but must **not** be enabled on the isolated watcher.
The code rejects simultaneous socket and direct-HTTP relay configuration.

## Proposed later PC installation — NOT AUTHORIZED BY THIS PR

1. Confirm currently installed watcher source matches the reviewed merged Runner
   SHA; compare systemd unit and service user, avoid touching Runner services.
2. Install only reviewed, immutable bridge code under
   `/opt/wonjae-monitor/scripts/`. Create a dedicated non-root egress account
   with no Runner/GitHub credentials, no login shell, and no private heartbeat
   directory access.
3. The Owner independently enters the same strong random secret as
   `SANJINWORKS_MONITOR_SECRET` in Cloudflare and a root-owned
   `/etc/wonjae-monitor/relay-secret` (0600). Never log its contents or
   pass it as a command-line argument or plaintext `Environment=` value.
   Confirm Cloudflare deployment and existing Worker secrets are preserved.
4. After separate privilege/network-boundary approval, review and adapt the
   service example with `systemd-analyze security`; enable only the egress
   service. Give the isolated watcher exactly
   `WONJAE_MONITOR_BRIDGE_SOCKET=/run/wonjae-monitor-relay/relay.sock` and
   keep `PrivateNetwork=true`.
5. Verify both processes, socket peer-UID rejection, TLS host validation,
   signed POST 202, invalid HMAC/replay rejection, authenticated GET, idle
   `jobs: []` and loss-of-bridge disconnect semantics. A real authorized
   Owner-started product Direct Worker run is still necessary to verify
   an actual job heartbeat and matching red process-health warning.
6. PC reboot readiness is conditional on Windows user login launching WSL;
   no actual reboot verification has yet occurred.

## Failure and approval boundaries

- Missing Secret, account, socket directory or stale monitor input -> fail
  closed. Do not infer official GitHub FAILED from bridge status.
- Socket producer failure -> local watcher continues; **no** source
  execution, dispatch or retry is started by the bridge.
- No writes to product source, state, GitHub dispatch, secrets or SANJINWORKS
  are part of this PR. Never report Production connectivity from unit tests.
- Restrict live network policy separately; do not silently remove
  `PrivateNetwork`, set unrestricted proxy variables or give the broker
  systemd root/Runner privileges.
