# PASSDOWN — AMP Silver Session
**Time:** 2026-08-17 00:46 PDT (updated 2026-08-17 03:05 PDT)  
**Working directory:** `/home/jason/amp`  
**Branch:** `master` (local changes uncommitted)  
**Status:** CRITICAL BUGS RESOLVED & VERIFIED this session. Docs (STRUCT/CHANGELOG) created. Not yet committed/pushed.

---

## CURRENT STATE

### What is running
- Single AMP receiver process (PID varies; verify via `curl http://localhost:8093/health`)
  - Command: `/home/jason/amp/server/venv/bin/python server/amp.py --host 0.0.0.0`
  - Ports: 8090 PCM, 8091 control, 8092 viz WebSocket, 8093 web UI
- Android client connects from `10.0.0.91` (confirmed `android_connected: true` in /health)
- System started via `start_receiver.sh` (single entry point)
- `audio-separator` venv exists at `/home/jason/amp/audio-separator-env/` with models downloaded

### What is NOT running
- No active audio-separator extraction jobs at the moment
- No GUI service-control buttons yet
- Android app NOT rebuilt for broken-pipe crash fix (pending)

---

## KNOWN BUGS — RESOLVED THIS SESSION (verified)

### 1. StemManager cap deadlock — FIXED (Critical)
**Files:** `server/stem_manager.py`
- Changed `threading.Lock()` → `threading.RLock()` so `extract()`→`_prune()`→`remove()`
  no longer self-deadlocks.
- Added `_prune()` call at end of `_scan()` so any over-cap backlog is trimmed on
  startup (was 1920 s / 106.7%; now 1800 s / 100% after restart).
- Unit test + live restart confirm rotation caps correctly, no deadlock.

### 2. Client config double-parse — FIXED (Medium)
**File:** `server/audio_receiver.py`
- `_handle_client()` now passes the parsed dict from `_read_config()` directly to
  `apply_config_dict()` (no `json.loads` on a dict). Control-port path still uses
  `_apply_client_config()` with raw JSON strings (correct).

### 3. Misleading "Applied config" + silent PASS — FIXED (Medium/KODE)
- `_handle_client` and control port report real outcome (Applied vs "had no effect").
- `apply_config_dict` returns bool; safety net logs with traceback.
- `/health` serialization killer fixed (now serializable payload + `connection_state_error`).
- `StemManager._probe_duration` logs corruption.
- No remaining silent exception swallowers in touched files.

### 4. Single entry point — VERIFIED clean
- `start_receiver.sh` → venv Python → `server/amp.py`. `launchers/amp-visualizer.sh`
  and `server/amp-receiver.service` delegate to `start_receiver.sh`. No stray
  `python3 amp.py` launch path remains.

---

## RECENT CHANGES (uncommitted)

### Modified files
- `config.json` — added stem config keys
- `launchers/amp-visualizer.desktop` — updated Exec path
- `launchers/amp-visualizer.sh` — delegates to single entry point
- `server/amp-receiver.service` — points to `start_receiver.sh`
- `server/amp.py` — added `/stems` endpoint, health state includes receiver ref
- `server/audio_receiver.py` — added StemManager init, async extraction, live config for stem cap
- `server/config.json` — added `stem_max_storage_seconds`, `stem_backend`
- `server/requirements.txt` — noted optional demucs
- `start_receiver.sh` — uses venv Python, pre-flight cleanup
- `web/index.html` — ring buffers, gauges, stems panel

### New untracked files
- `audio-separator-env/` — separate venv with audio-separator package and models
- `audio_segments/` — root-level segment dir (should be verified if still needed)
- `audio_stems/` — root-level stem dir (contains 32 segment dirs, 809MB)
- `server/audio_separator_backend.py` — real ML backend using audio-separator
- `server/audio_stems/` — server-side stem dir (empty)
- `server/stem_backends.py` — frequency-band fallback backend
- `server/stem_manager.py` — capped rotation manager (HAS DEADLOCK BUG)

---

## WHAT WORKS

1. **Single entry point startup** — `start_receiver.sh` starts/stops cleanly, uses venv Python
2. **Web UI** — serves at `http://0.0.0.0:8093/` with SRP theme, ring buffers, gauges, stems panel
3. **WebSocket viz** — waveform + spectrogram rendering in browser
4. **Android → PCM** — client connects, sends audio, segments are saved to `audio_segments/`
5. **Health endpoint** — `/health` returns JSON with ports and status
6. **Stems API** — `/stems` endpoint returns JSON with segment/stem listings
7. **Frequency-band backend** — instant, works for testing
8. **Audio-separator backend** — downloads models, runs inference (CPU, slow but functional)

---

## WHAT IS BROKEN (remaining)

1. **Android app** — needs rebuild for `Broken pipe` crash fix (stream thread
   must catch all exceptions). Receiver side is robust; Android is not.
2. **UI smoothness** — waveform/spectrogram scrolling is choppy (correct, but
   not buttery). Deferred by Jason; graphs are accurate.
3. **GUI service-control buttons** — start/stop/reconnect not yet in UI.
4. **Graceful cross-side shutdown** — receiver shuts down gracefully on signal;
   both-side coordinated graceful shutdown + log offering on critical failure is
   partial (receiver done, Android pending rebuild).

## RESOLVED THIS SESSION (see KNOWN BUGS — RESOLVED above)
- StemManager deadlock (RLock + startup prune) — VERIFIED
- Client config double-parse — VERIFIED
- Misleading "Applied config" + silent PASS killers — VERIFIED
- Single source of truth for segments (removed `server/audio_segments`,
  `server/audio_stems`) — VERIFIED
- `/health` serialization — VERIFIED (valid JSON)

---

## NEXT STEPS (in priority order)

### Must do next (guiding point)
1. **Decide on commit/push** — fixes are verified; prior passdown gates push on
   explicit go-ahead. Not pushed yet.
2. Add GUI service-control buttons (start/stop/reconnect) via `/health` state.
3. Test audio-separator end-to-end with real extraction in running system.
4. Improve UI scrolling smoothness within the 300 ms budget (deferred, but
   worth a pass).
5. Rebuild Android app with broken-pipe crash fix; verify both sides run
   independently and shut down gracefully.
6. Verify ring buffer sliders persist (localStorage) or are intentionally
   transient.

### Before commit/push
7. `git add -A && git commit -m "fix: stem rotation cap, config parse, honest logging, SRP branding, docs"`
8. Push only after the above verification passes.

---

## IMPORTANT CONTEXT

- **Single source of truth for segments:** `audio_segments/` at project root (relative path `audio_segments` from cwd `/home/jason/amp`)
- **Stems should live:** `audio_stems/` at project root, capped at `stem_max_storage_seconds` (default 1800s)
- **Config file:** `server/config.json` — contains `amplification`, `breathing_*`, `segment_duration_min`, `stem_max_storage_seconds`, `stem_backend`
- **Logs:** `logs/amp.log` — unified log, no more multiple log files
- **Android app:** needs rebuild with `Broken pipe` crash fix (stream thread catches all exceptions)
- **Web UI port:** 8093 (not 8082, not 8094)
- **Latency budget:** 300ms for viz frames

---

## DO NOT

- Do not push to remote until deadlock and config bugs are fixed
- Do not run `pkill` outside of `start_receiver.sh`
- Do not create additional log files
- Do not add placeholder code or snippets
- Do not assume files exist — check first

---

## TO START NEXT SESSION

```bash
cd /home/jason/amp
# 1. Check git status
git status

# 2. Review uncommitted changes
git diff --stat

# 3. Fix stem_manager.py deadlock
#    Change: self._lock = threading.Lock()
#    To:     self._lock = threading.RLock()

# 4. Fix audio_receiver.py config double-parse
#    In _handle_client(), pass dict directly to apply_config_dict()

# 5. Test
bash start_receiver.sh
# Wait for "Visualizer WebSocket listening" line
curl http://localhost:8093/health
curl http://localhost:8093/stems

# 6. Only after tests pass:
git add -A
git commit -m "fix: stem rotation cap, config parsing, verify single entry point"
git push origin master
```

---

*End of passdown.*
