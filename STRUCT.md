# STRUCT.md — AMP Silver System Structure (Source of Truth)

> Single source of truth for system namings, paths, ports, processes, and
> runtime truths. If an issue arises, compare against this. Keep in sync with
> reality after every change. Branding: **SRP — Slutty Rabbit Productions**.

## 1. Overview
AMP Silver is a single-process audio receiver + real-time visualizer stack
running on a 4-core / 16 GB RAM, CPU-only Ubuntu mini-PC (hostname `silver`,
10.0.0.147). It receives raw 16-bit PCM from an Android client, segments it,
optionally extracts stems, and pushes low-latency waveform/spectrogram frames
to a browser over WebSocket. Hard latency budget: **end-to-end ≤ 300 ms**.

## 2. Single Entry Point
- **`/home/jason/amp/start_receiver.sh`** — the ONLY supported way to start the
  stack. It:
  1. `cd`s to project root.
  2. Uses the venv Python (`server/venv/bin/python`) — never system `python3`.
  3. Pre-flight: kills any existing `amp.py` (TERM, then KILL if stuck).
  4. `exec`s `server/amp.py --host 0.0.0.0` (ports auto-hunt 8090..8099).
  5. Tees stdout to `logs/amp.log`.
- systemd unit: `server/amp-receiver.service` → `ExecStart=/home/jason/amp/start_receiver.sh`.
- `launchers/amp-visualizer.sh` and `launchers/amp-visualizer.desktop` delegate
  to `start_receiver.sh` (they do NOT launch python directly).

## 3. Processes
- One process: `server/venv/bin/python server/amp.py --host 0.0.0.0`.
- Owns (in one process, daemon threads): PCM TCP server, control (live config)
  TCP server, viz WebSocket server, web UI HTTP server, hot-reload loop.
- Current PID (2026-08-17): see `curl http://localhost:8093/health`.

## 4. Ports (auto-allocated in 8090..8099; current resolved values)
| Role            | Port | Protocol | Notes |
|-----------------|------|----------|-------|
| PCM audio       | 8090 | TCP      | Android client connects here |
| Control (config)| 8091 | TCP      | JSON-lines live config push; persisted |
| Viz WebSocket   | 8092 | WS       | waveform + spectrogram frames |
| Web UI          | 8093 | HTTP     | `web/index.html` |
Ports are resolved at startup via `allocate_ports()`/`find_free_port()` and
hunt for a free slot inside 8090..8099 if busy.

## 5. Directory Layout (project root `/home/jason/amp`)
| Path | Purpose | Canonical? |
|------|---------|-----------|
| `start_receiver.sh` | single entry point launcher | YES |
| `server/amp.py` | web UI + `/health` + `/stems` HTTP server, wires receiver | YES |
| `server/audio_receiver.py` | PCM receive, segment, breathing detect, viz feed, config apply, per-graph toggles + extraction batch gate | YES |
| `server/stem_manager.py` | capped LRU rotation of stems (uses `threading.RLock`) | YES |
| `server/stem_backends.py` | `DemucsBackend` (opt-in ML, real vocals/guitar/bass/drums + noise) + `FrequencyBandBackend` (default, instant RAM-free split into bass/vocals/guitar/drums + noise) | YES |
| `server/audio_separator_backend.py` | opt-in ML backend (audio-separator: vocals/instrumental, CPU) | YES |
| `server/audio_segments/` | **REMOVED** — was a stale duplicate | NO (deleted) |
| `server/audio_stems/` | **REMOVED** — was stale/unused | NO (deleted) |
| `audio_segments/` | WAV segments (FIFO, ~1h cap) | YES (single source) |
| `audio_stems/` | extracted stems (LRU cap, default 1800s) | YES (single source) |
| `config.json` | persisted settings (root) — see keys below | YES |
| `server/config.json` | server-side config mirror (stem keys) | YES |
| `logs/amp.log` | unified log (only log file; no others created) | YES |
| `web/index.html` | visualizer UI (SRP theme) | YES |
| `web/serve.py` | static server, serves `web/` dir | YES |
| `web/assets` -> `../srp-css-theme-pack/assets` | symlink to canonical SRP branding assets | YES (no copy) |
| `srp-css-theme-pack/` | canonical SRP theme + logo assets (logo-primary.svg/png, logo-alt.svg) | YES |
| `audio-separator-env/` | separate venv with audio-separator + models | YES |
| `android-app/` | Android client source (needs rebuild: broken-pipe crash fix) | YES |
| `PASSDOWN.md` / `KODE.md` / `STRUCT.md` / `CHANGELOG.md` | project docs | YES |

## 6. Config Keys (`config.json`)
- `amplification` (int)
- `breathing_sensitivity` (0..100) → threshold
- `breathing_cooldown` (s)
- `segment_duration_min` (min) → segment seconds = clamp(1..60)*60
- `stem_max_storage_seconds` (60..86400) → stem cap
- `stem_backend` ("frequency_band" | "audio_separator" | null)

## 7. Stem Rotation (critical)
- `StemManager` caps total stored stem *duration* at `max_seconds` (default 1800).
- Uses `threading.RLock()` — `extract()` holds lock and calls `_prune()`, which
  calls `remove()`; re-entrant lock prevents the prior self-deadlock.
- `_scan()` (at init) calls `_prune()` so any backlog over cap is trimmed on
  startup, not only on next extraction.
- Endpoints: `GET /stems` (stats + per-segment listings), `GET /health`.

## 8. Exception Policy (KODE mandate)
- NO silent `PASS`. Config apply returns bool and logs honest success/failure.
- `/health` builds a serializable payload; `connection_state()` errors are
  logged and surfaced as `connection_state_error`.
- `_probe_duration` logs corruption instead of swallowing.
- `apply_config_dict` safety net logs with traceback.

## 9. Resource Constraints
- CPU-only mini-PC. Do not load whole datasets at once (e.g. WGUI fetches
  stems lazily, never the full tree). Make expensive work count.
- Latency budget 300 ms is non-negotiable for viz frames.

## 10. Known Issues
- UI scrolling is choppy (smoothness deferred; graphs correct).
- Android app needs rebuild for broken-pipe crash fix.
- Both sides must run independently; critical failure → graceful shutdown +
  log offered. (Receiver side implemented; Android side pending rebuild.)
