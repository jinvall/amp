# AMP Deploy Readiness Build Plan

## Objective
Define the work required to move AMP from a working local prototype to a deployable, monitored, and operationally safe system for a trusted edge/LAN environment.

## Current status (verified)
- Receiver is responding at http://localhost:8093/health with `ready: true`.
- Resolved service ports: PCM 8090, control 8091, viz 8092, web 8093.
- Android client is not currently connected (`android_connected: false`).
- Repo is not in a clean committed state; there are active changes and untracked generated artifacts.
- The system is functional for local testing, but it is not yet hardened for deployment readiness.

## Deployment posture
This project is not yet production-ready for public exposure. The intended operating model is a private/controlled network deployment on a trusted host, with the Android client connecting over a private LAN or a VPN/private tunnel.

## Readiness decision
Status: Not deploy-ready yet.

Go-live requires the following gates to pass:
- secure transport and trust model
- disk/storage guardrails
- client and server resilience
- Android rebuild and validation
- release hygiene and operational readiness

## Deployment target assumptions
- Target host: Silver-style edge Linux node running AMP service
- Network model: private LAN or controlled tunnel; not internet-open by default
- Audio paths: Android mic -> TCP PCM -> receiver -> segment storage + optional stems
- Visualizer: local browser or trusted internal UI access only
- Storage: project-root audio_segments/ and audio_stems/

## Requirements before deployment

### 1) Security hardening (must pass)
- Bind receiver to trusted interface only, not a broad public-facing exposure.
- Add strong authentication or shared-secret trust model for all client connections.
- Protect raw audio transport from interception and spoofing.
- Validate client config payloads with strict bounds and ignore untrusted inputs.
- Remove or restrict any path that accepts unauthenticated configuration changes.

Exit criteria:
- no unauthenticated audio clients accepted
- config values are clamped and server-side validated
- deployment only on trusted network or encrypted tunnel

### 2) Reliability and resilience (must pass)
- Add safe reconnect logic for Android client.
- Add server-side socket timeout and stale-client teardown.
- Add backpressure / frame dropping during overload instead of silent queue buildup.
- Ensure graceful shutdown and final segment flushing.
- Ensure receiver logs and health stay serializable and useful under failure conditions.

Exit criteria:
- dropped network connections recover cleanly
- no stuck sockets from silent disconnects
- service remains alive under segmenting / extraction pressure

### 3) Storage safety (must pass)
- Enforce size limits and free-space checks before writes.
- Ensure old data is purged within defined caps and does not grow without bound.
- Keep segment and stem storage within explicit total-duration or byte budgets.
- Validate config values before they affect storage behavior.

Exit criteria:
- no unbounded disk growth in normal operation
- low-disk conditions result in safe shutdown or pause
- retention policy matches configured cap

### 4) Android readiness (must pass)
- Rebuild the Android app with the broken-pipe crash fix.
- Verify audio stream remains stable across disconnect/reconnect events.
- Validate the app still works with the current server ports and config flow.
- Confirm foreground service / permissions / networking behavior is correct.

Exit criteria:
- app starts and streams without crashing on socket errors
- reconnect behavior is validated on a real device or emulator
- app is consistent with current server config model

### 5) Operational readiness (must pass)
- Confirm single entry point is the supported startup path.
- Verify `start_receiver.sh` starts the service cleanly and logs to the unified log file.
- Confirm `/health` returns expected state and ports.
- Validate service management under systemd and launcher flows.
- Keep explicit deployment notes for rollback and restart procedures.

Exit criteria:
- health endpoint is green before deployment
- startup is repeatable and documented
- logs are usable for incident response

## Phase plan

### Phase 0: Freeze and baseline
- Confirm current branch and working tree state.
- Capture the exact currently working startup command and health output.
- Decide whether the deployment will be a local private-host deployment or a hosted edge deployment.

### Phase 1: Security and trust
- enforce private-network binding
- add auth or secret challenge for client access
- block unauthenticated config writes
- document network exposure assumptions

### Phase 2: Stability and resilience
- reconnect logic for Android
- stale-client reaper
- queue/backpressure limits
- better shutdown paths

### Phase 3: Storage and retention safety
- enforce free-space checks
- segment and stem cap enforcement
- prune logic verification

### Phase 4: Android rebuild and validation
- rebuild app
- validate broken-pipe fix
- verify end-to-end audio flow

### Phase 5: Release candidate checks
- install dependencies from requirements
- run smoke tests
- validate health endpoint and UI
- check logs and disk behavior for one sustained run

### Phase 6: Deployment
- deploy to the target edge host
- run the startup script under the intended configuration
- verify services, ports, and health output
- confirm live audio path works end-to-end

## Concrete verification checklist

### Service startup
- Run: ./start_receiver.sh
- Confirm logs recorded in logs/amp.log
- Confirm HTTP health returns `ready: true`
- Confirm ports are resolved and not colliding

### Audio path
- Connect Android client
- Confirm audio is being received and segmented
- Check that files are written under audio_segments/
- Verify expected timestamps and segment rotation behavior

### Stem path
- Enable a supported stem backend only if validated
- Confirm storage remains within configured cap
- Verify pruning does not deadlock or create large stale backlog

### Failure path
- Disconnect client
- Confirm server handles the issue without hanging
- Restart service and confirm recovery

## Go / No-Go gates

### GO only when all of the following are true
- trusted-network or encrypted transport model is in place
- raw audio is not exposed without authentication
- no disk growth runaway
- app reconnects/restarts cleanly
- health endpoint and startup are stable
- deployment docs are complete and tested

### NO-GO if any of the following remain
- unauthenticated public exposure
- no cap on storage or stale clients
- client crash on socket error is unresolved
- health endpoint is unreliable
- start/restart procedure is unclear or inconsistent

## Recommended next milestones
1. Harden security and trust boundaries.
2. Fix Android reconnect and broken-pipe handling.
3. Add storage and free-disk guardrails.
4. Verify end-to-end live operation on the target host.
5. Cut a release candidate once the gates above pass.

## Summary
AMP is in a promising working state for local validation, but it is not yet ready for general deployment because the repo still exposes the main deployment risks identified in the project audit: untrusted network access, storage growth risk, and incomplete Android/client robustness. The recommended next step is to treat this as an edge/private deployment candidate only after the hardening and validation gates above are closed.
