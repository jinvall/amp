# Project Audit Report — AMP Audio Streamer

**Scope:** `android-app/` (Kotlin foreground service + UI), `server/audio_receiver.py`,
root Python senders (`audio_streamer.py`, `simple_audio_stream.py`), shell launcher,
configs, and docs.
**Date:** 2026-08-13
**Reviewer:** Kilo

---

## 1. Executive Summary

The project is a functional mic→TCP→server pipeline with on-device pre-gain, a 5-band
EQ, amplification, segmented WAV storage, and a (primitive) breathing detector. The
recent pre-gain/EQ work **compiles and runs** but is **uncommitted** and **not fully
wired end-to-end** (the server ignores EQ/pre-gain, and the client config line omits
them). The codebase is small and readable, but has real **security, privacy,
reliability, and correctness** gaps that should be addressed before relying on it.

| Area | Rating | Notes |
|------|--------|-------|
| Security | **F** | Plaintext TCP, no auth, binds `0.0.0.0`, no size limits, no TLS. |
| Privacy / Consent | **D** | No consent flow, mic always-on when streaming, no at-rest protection. |
| Reliability | **C** | Unbounded buffer growth, no reconnect, no inactivity timeout, single client. |
| Code Quality | **C+** | Two dead/legacy senders, doc/code drift (MP3 vs WAV), dead code. |
| Performance | **B-** | Per-sample double biquad + byte packing is OK but wasteful; debug logging hot. |
| Maintainability | **B** | Good structure; needs tests, lint, CI, and a single source of truth. |

---

## 2. Architecture Overview

```
Android (AudioRecord 44.1k/16b/mono)
   → pre-gain → 5× biquad EQ → amplification → clamp (on device)
   → TCP raw PCM ────────────────────────────────► Silver server
                                                    ├─ ring buffer
                                                    ├─ WAV segments (FIFO ~1h)
                                                    └─ breathing detector (FFT band energy)
```

Transport is **raw, unauthenticated PCM over TCP**. The server trusts any client that
connects to `0.0.0.0:8080`.

---

## 3. Critical Findings (must fix)

### F-1. No transport security or authentication (CRITICAL)
`audio_receiver.py` binds `HOST='0.0.0.0'`, accepts any TCP client, and applies
whatever JSON config the client sends (segment duration, breathing threshold/cooldown).
Anyone on the LAN (or the internet, if port-forwarded) can:
- Flood the server with audio / fill the disk (no per-client quota).
- Spoof config to change storage/behavior.
- Intercept raw mic audio in cleartext (privacy violation).

**Fix:** Wrap in TLS (`ssl.wrap_socket` or a reverse proxy / WireGuard), require a
pre-shared token in the handshake, and bind to a specific interface or `127.0.0.1` +
tunnel. At minimum, add a shared-secret challenge before accepting audio.

### F-2. Disk exhaustion via unbounded storage (HIGH)
`StorageManager._prune()` keys off `segment_duration_sec`, which the **client can set**
via `segment_duration_min` (clamped 1–60 min). A client can request 60-min segments;
FIFO still caps at 1h of *segments*, but the **ring buffer** in `_process_audio` is
capped only by `segment_bytes + step_bytes`. More importantly, `_save_segment` writes
one file per `step_bytes`; with a 60-min segment and 5s step that is a huge single file
and the FIFO math still works, but there is **no total byte cap and no free-space check**.
WAV is ~5.3 MB/min → 1h ≈ 318 MB, which is fine, but there is no guard if the dir is on
a small partition.

**Fix:** Enforce a byte budget (`MAX_STORAGE_BYTES`), check `shutil.disk_usage` and stop
accepting when free space < threshold, and ignore absurd client segment sizes
(server-side max, not client-controlled).

### F-3. Unbounded receive / config buffer and silent resource growth (HIGH)
- `_read_config` caps at 4096 bytes — OK.
- `_handle_client` reads `conn.recv(65536)` and pushes to a 50-slot queue; if the
  worker (`_process_queue`) falls behind (e.g., breathing FFT on large windows), the
  queue drops oldest but the **client keeps sending at full mic rate**, and the server
  never backpressures. CPU for FFT can spike.
- `BreathingDetector.ring` grows by `window_samples` (2s = 88200 bytes) and is trimmed
  by `hop_samples` each loop, but `feed()` is called per chunk; under burst it can
  balloon. There is no max cap.

**Fix:** Add a hard cap on `ring`, drop frames under backlog, and move FFT off the
hot path (timer-based windows, not per-recv).

### F-4. No reconnection / no server-side inactivity timeout (HIGH, reliability)
- Client: if the socket drops, `connectThread` catches and `stopSelf()` — the stream
  dies permanently; no retry with backoff.
- Server: a connected client that stops sending (but keeps the socket open) holds the
  thread forever; no `settimeout` on `recv`, no keepalive enforcement beyond TCP default.

**Fix:** Client: exponential backoff reconnect loop. Server: `conn.settimeout(...)`,
track last-recv time, close stale clients.

---

## 4. Correctness & Wiring Bugs (should fix)

### B-1. EQ / pre-gain never sent to server (the new feature is half-wired)
`AudioStreamerService.sendConfig()` (lines 217–232) sends only:
`amplification, breathing_sensitivity, breathing_cooldown, segment_duration_min`.
It does **not** send `pre_gain` or `eq_bands`. The server has **no code path** to apply
EQ/pre-gain server-side, and the client applies them on-device — so functionally it
works *on the device*, but:
- The README/docs and the config protocol imply server awareness.
- If you ever move processing server-side, it is absent.

**Recommendation:** Either (a) document that EQ/pre-gain are client-only (simplest,
correct today), or (b) extend the config JSON to include them for future server-side
processing. At minimum, include them in the config line for telemetry/debugging.

### B-2. `amplify()` is dead code (correctness/clarity)
`AudioStreamerService.amplify()` (lines 351–376) is no longer called — `processAudio()`
does the work. Leftover code invites confusion and divergence.

**Fix:** Delete `amplify()`.

### B-3. `producer` thread variable unused warning
`startRecordingLoop` assigns `val producer = Thread {...}.also { it.start() }` (line 271)
but never references `producer`. Harmless but should be cleaned up; also the producer is
never explicitly interrupted on stop (relies on `isStreaming` flag check — OK, but lacks
a `thread.join`/interrupt on `stopStreaming`).

### B-4. Doc/code drift: README says MP3, code writes WAV
README ("Encodes incoming PCM to MP3 using `lameenc`", "5-minute MP3 segments") and the
module docstring ("Encodes to MP3") contradict the actual `wave.open(...)` WAV output.
`lameenc` is imported but never used. This misleads operators.

**Fix:** Update README + docstring to state WAV; remove `lameenc`/`HAS_LAME` if MP3 is
truly abandoned, or re-add MP3 export as an optional post-process.

### B-5. `simple_audio_stream.py` and `audio_streamer.py` are legacy stubs
Both generate **fake/sine audio** and do not use the real mic path. `simple_audio_stream.amplify_audio`
amplifies **each byte independently** (breaks 16-bit samples → corrupts audio). These
are misleading if run.

**Fix:** Delete them or clearly label as "simulation only / not for production". They
should not ship in the repo root next to the real app.

### B-6. Breathing threshold mapping is fragile
`threshold = 1e8 * pow(1e-5, val/100.0)` (line 290): at `val=0` → 1e8, at `val=100` →
1e3. The default `BREATHING_ENERGY_THRESHOLD = 1.2e3` and default `breathing_sensitivity
= 100.0` from the app map to `1e3`, so the app's "100" almost disables detection. Also
spectral energy scales with `window size²` and sample rate; constants are not
normalized, so thresholds are physically meaningless and untunable.

**Fix:** Normalize energy by `N` (mean power), expose a clear dB threshold, and decouple
from raw FFT magnitude.

---

## 5. Performance Notes

- **Per-sample double biquad + manual byte packing** (lines 455–483) is correct but
  allocates nothing in the loop — good. However it runs at ~44.1k×5 filters/sample in
  Kotlin; fine for mono. If you later go stereo or raise rate, move to `FloatArray` +
  `android.media.AudioEffect`/`DynamicsProcessing` EQ instead of manual biquads.
- **Debug logging is on the hot path**: `Log.d` every 200 chunks and a `println` per
  recv (line 239–240) under a modulo that still fires ~1/sec. Use sampling or a ring
  buffer; gate behind `BuildConfig.DEBUG`.
- **Server FFT per chunk** is the biggest CPU risk. Batch into fixed windows on a timer.

---

## 6. Hardening & Upgrade Recommendations (prioritized)

### Tier 1 — Security & Safety (do first)
1. **TLS + PSK** for the TCP link (or VPN/WireGuard). Never expose `0.0.0.0` to untrusted nets.
2. **Server-side config limits**: ignore/ clamp client segment duration, breathing
   params; never let the client fully control storage sizing.
3. **Free-disk guard**: `shutil.disk_usage` check before writing; stop + alert.
4. **Consent UX**: explicit "this app records and streams microphone audio" notice and
   a persistent indicator; honor `android:allowBackup="false"` (see M-2).
5. **Input validation** on `_read_config` JSON (already dict-checked, but add bounds).

### Tier 2 — Reliability
6. **Client reconnect with backoff** (network blips are common on Wi-Fi/cell).
7. **Server recv timeout + stale-client reaper**.
8. **Backpressure**: if processing falls behind, drop frames (already partially done)
   and expose a "behind by Ns" metric.
9. **Graceful shutdown**: join threads, flush, ensure last partial segment is saved.

### Tier 3 — Quality / Maintainability
10. Remove dead code (`amplify`, legacy senders, unused `lameenc`).
11. Fix README/code drift (WAV vs MP3).
12. Add **unit tests** (biquad math, byte<->sample packing, segment logic) and a
    **lint/CI** (GitHub Actions: `./gradlew lint`, `pyflakes`/`mypy` on server).
13. **Single source of truth** for audio format constants shared client/server.
14. Replace `println` with `logging` module (levels, rotation).
15. **Pin dependencies**: `requirements.txt` only has `numpy>=1.24.0`; add `lameenc`
    (if used) with a version, and consider `pyproject.toml` + `ruff`.

### Tier 4 — Upgrades / Nice-to-have
16. **Server-side optional MP3/Opus** export (ffmpeg/opussenc) for space savings.
17. **Web playback / live monitor** (small FastAPI + WebSocket) so Silver can listen
    without pulling WAVs.
18. **Metadata sidecar** (JSON per segment: ts, gain, eq, rms, detections).
19. **Android `DynamicsProcessing`** for EQ (hardware-accelerated, less battery).
20. **Configurable sample rate** (48k capture → resample to 44.1k server) to match
    the ffmpeg/local ALSA 48k source noted earlier.

---

## 7. Immediate Action Checklist

- [ ] Bind server to a trusted interface or add TLS+PSK (F-1).
- [ ] Add disk-free guard + byte budget (F-2).
- [ ] Cap `BreathingDetector.ring`; batch FFT (F-3).
- [ ] Client reconnect + server recv timeout (F-4).
- [ ] Delete `amplify()` and legacy senders; fix README MP3→WAV (B-2, B-4, B-5).
- [ ] Decide EQ/pre-gain server awareness; update `sendConfig` + docs (B-1).
- [ ] **Commit the in-progress pre-gain/EQ work** (currently uncommitted, see §8).
- [ ] Add `logging`, tests, and a minimal CI.

---

## 8. Working-Tree Status (informational)

The pre-gain + 5-band EQ feature is implemented and builds, but **uncommitted**:
```
 M android-app/.../AudioStreamerService.kt  (+162)
 M android-app/.../MainActivity.kt          (+66)
 M android-app/.../activity_main.xml        (+653/-218)
?? srp-css-theme-pack.zip
```
Recommend committing the feature with a clear message and then applying the Tier-1
hardening. `local-config.properties` (contains `server.ip=10.0.0.147`) is correctly
gitignored.
