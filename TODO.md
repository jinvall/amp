# AMP Deployment Readiness TODO

## Status
Current state: working local prototype, not yet deploy-ready.

## Priority 0 — Baseline and freeze
- [ ] Confirm current working-tree state and exact startup path
- [ ] Capture a clean baseline health check from http://localhost:8093/health
- [ ] Decide the target deployment model: private LAN only or private tunnel/VPN
- [ ] Freeze scope for the deployment-readiness milestone

## Priority 1 — Security hardening
- [ ] Bind receiver to a trusted interface only
- [ ] Add auth or shared-secret trust on PCM/control connections
- [ ] Restrict unauthenticated config writes
- [ ] Document the allowed network exposure model
- [ ] Verify no public-facing ports are left open by default

## Priority 2 — Reliability and resilience
- [ ] Add Android reconnect with backoff logic
- [ ] Add server-side socket timeout and stale-client cleanup
- [ ] Add queue/backpressure handling under overload
- [ ] Ensure graceful shutdown saves partial data safely
- [ ] Validate health output remains serializable and useful during failures

## Priority 3 — Storage safety
- [ ] Add free-disk checks before writes
- [ ] Enforce explicit storage caps for segments and stems
- [ ] Verify pruning logic under cap and startup conditions
- [ ] Confirm retention never exceeds configured policy

## Priority 4 — Android readiness
- [ ] Rebuild Android app with broken-pipe fix
- [ ] Validate app reconnect behavior on device or emulator
- [ ] Confirm microphone permissions and foreground service behavior
- [ ] Verify the app matches current server port/config expectations

## Priority 5 — Operational readiness
- [ ] Verify single entry point is the supported startup path
- [ ] Confirm start_receiver.sh and logs/amp.log work reliably
- [ ] Check health endpoint and UI against target deployment conditions
- [ ] Document restart / rollback process for edge host operators

## Priority 6 — Release candidate checks
- [ ] Run smoke test on startup
- [ ] Verify audio capture and segmentation path end-to-end
- [ ] Verify stem backend behavior if enabled
- [ ] Test disconnect/reconnect and recovery behavior
- [ ] Validate system holds steady during a sustained run

## Priority 7 — Deployment gate
- [ ] All security gates pass
- [ ] Storage guardrails pass
- [ ] Android client is stable
- [ ] Health endpoint is green in deployment conditions
- [ ] Documentation is complete and accurate
- [ ] Deployment is approved for private/trusted-environment use only

## Notes
- This project is functional for local prototype use, but not yet deploy-ready for broader or public exposure.
- The main risks remain: insecure audio transport, storage growth, and incomplete client/server resilience.
- See [BUILDPLAN.me](BUILDPLAN.me), [AUDIT.md](AUDIT.md), [PASSDOWN.md](PASSDOWN.md), and [STRUCT.md](STRUCT.md) for supporting context.
