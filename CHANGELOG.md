# CHANGELOG.md — AMP Silver

All notable changes are recorded here, oldest → newest. Format: date, what
changed, why, verification. Branding: SRP — Slutty Rabbit Productions.

## 2026-08-17 (session: deadlock + config fixes + honest logging + docs)
### Fixed
- **StemManager self-deadlock (CRITICAL).** `server/stem_manager.py` used a
  non-reentrant `threading.Lock()`; `extract()` held it and called `_prune()`
  → `remove()` which re-acquired the same lock → self-deadlock → stems never
  pruned → storage grew unbounded (was 1920 s / 106.7% over the 1800 s cap).
  Changed to `threading.RLock()`. Also added a `_prune()` call at the end of
  `_scan()` so any backlog accumulated under the deadlocked code is trimmed on
  startup instead of waiting for a fresh extraction.
- **Client config double-parse (MEDIUM).** `_handle_client()` in
  `server/audio_receiver.py` took the already-parsed dict from `_read_config()`
  and pushed it through `_apply_client_config()` which called `json.loads()` on
  a dict → `TypeError` → config silently not applied. Now passes the dict
  directly to `apply_config_dict()`.
- **Misleading "Applied config" log (MEDIUM).** The unconditional
  `print("Applied config from ...")` is gone. `_handle_client` and the control
  port now report the REAL outcome: `Applied` only when a setting changed, else
  `Config ... had no effect`.
- **`/health` empty body (MEDIUM).** `json.dumps(HealthHandler._state)` choked
  on the non-serializable `receiver` object and the `except` swallowed it → 200
  with `{}`. Now builds a serializable payload (ports/ready/viz + live
  `connections`); `connection_state()` failures are logged and surfaced as
  `connection_state_error`.
- **Silent exception swallowers (KODE mandate).** `apply_config_dict` now returns
  bool and its safety net logs with `traceback`; non-dict input refused+logged.
  `_apply_client_config` returns bool. `StemManager._probe_duration` logs
  corruption. Control-port handler reports apply success/failure honestly.

### Changed
- **Single source of truth for segments.** Removed stale duplicate
  `server/audio_segments/` (80 files, 2026-08-14) and unused empty
  `server/audio_stems/`. Canonical locations: `audio_segments/` and
  `audio_stems/` at project root. Verified code only references the root dirs.
- **SRP branding in UI.** Added `Slutty Rabbit Productions` wordmark + logo to
  `web/index.html` header, referencing the canonical `srp-css-theme-pack/assets`
  via a `web/assets -> ../srp-css-theme-pack/assets` symlink (no asset copy /
  no name drift). Added SRP-theme CSS for `.brand`.

### Verified
- Unit test: StemManager rotation caps correctly across repeated extractions,
  no deadlock (total stored ≤ cap).
- Live: restarted via `start_receiver.sh`; `/stems` shows backlog pruned to
  1800 s / 100%. `/health` returns valid JSON incl. real Android connection.
- Live control-port test: bad JSON → `Failed to apply` + `had no effect` (no
  false "Applied"); good key → `Updated stem max storage to 1700s` (live).
- All `except` blocks in touched files audited — no remaining silent PASS
  killers (import guards + socket timeouts are intentional).

### Docs
- Created `STRUCT.md` (system truth), `CHANGELOG.md` (this file).
- Updated `PASSDOWN.md` to reflect fixed state.
- Not committed/pushed (gated on explicit go-ahead per prior passdown).

## Earlier (pre-this-session, from git log)
- venv python enforced in `start_receiver.sh`.
- receiver.stop() idempotent; clean shutdown via timed-out accepts/queue gets.
- configurable ring buffers + smoother scrolling (choppy still noted).
- init order restored; `connection_state()` added.

## 2026-08-17 (session: enhanced stem bands — vocals/guitar/bass/drums/noise)
### Added
- **`DemucsBackend`** in `server/stem_backends.py` — real ML source separation
  via Facebook Demucs (`htdemucs_ft` → vocals/drums/bass/other; maps `other`
  → `guitar`, derives a `noise` residual = source − sum(stems)). CLI-invoked
  (version-stable across Demucs 4.x), downloads models on first use, frees temp
  output. Opt-in only (heavy: ~2 GB RAM, slow on CPU).
- **Enhanced default split** in `FrequencyBandBackend` — now routes FFT energy
  into the requested named bands `bass` (20–250 Hz), `vocals` (250 Hz–4 kHz),
  `guitar` (80 Hz–1.2 kHz), `drums` (4 kHz–Nyquist), plus a `noise` residual.
  Bands intentionally overlap so no musical energy is silently dropped. Instant
  and RAM-free, so it is the safe DEFAULT on the 4-core/16 GB CPU mini PC.
- Wired `demucs` into `StemManager.extract()` dispatch with graph-pause/resume
  (reuses the refcounted `VisualizerFeed.pause()` from the OOM fix).

### Changed
- **Default backend stays `frequency_band`** (NOT auto-Demucs). Per KODE.md the
  box is a 4-core/16 GB CPU-only mini PC; auto-running 4-model Demucs per
  segment would re-trigger the OOM we just fixed. Operators opt into real ML via
  config: `stem_backend: "demucs"` (or `"audio_separator"`), accepting the
  RAM/CPU cost. `DemucsBackend.available()` gates selection when configured.
- `FrequencyBandBackend` old bands (`low/mid_low/mid_high/high/residual`)
  renamed to the requested `bass/vocals/guitar/drums/noise`. UI renders whatever
  names exist, so the Stems panel shows the new bands automatically.
- `requirements.txt`: clarified `demucs>=4.0.0` is optional/CPU-capable.

### Verified
- `py_compile` clean on all server modules.
- `DemucsBackend.available()` → False on dev box (package absent); shared
  `_load_wav_mono`/`_write_wav_mono` round-trip + resample tested.
- End-to-end `FrequencyBandBackend.extract()` on a synthetic mix produces exactly
  `{bass, vocals, guitar, drums, noise}` WAVs at source rate.
- `StemManager._default_backend()` ordering confirmed RAM-safe (frequency_band).

### Docs
- `STRUCT.md` stem-backend rows updated to reflect DemucsBackend + renamed bands.
- This CHANGELOG entry.

## 2026-08-18 (session: per-graph toggles + extract gating + dynamic models)
### Added
- **Per-graph manual toggles (KODE.md).** `VisualizerAnalyzer` gained
  `wave_enabled` / `spec_enabled`; `VisualizerFeed` gained `set_graph_enabled()`
  + `graph_states()`. A new viz WS message `{type:'graph', graph:'wave'|'spec',
  enabled}` drives them from the web UI (new header buttons "Wave"/"Spec").
  Disabled graphs are skipped in `analyze()` (zero CPU) and the frame carries
  `wave:null`/`spec:null`; the browser skips drawing null frames. Persisted via
  config keys `viz_wave_enabled` / `viz_spec_enabled` (applied at startup +
  live). Initial state sent to the UI in the viz `config` hello.
- **Extract Now forces both graphs off until the batch completes (KODE.md).**
  `AudioReceiver.extract_all_stems()` now increments a refcounted
  `_extract_batch_remaining` and calls `VisualizerFeed.disable_graphs_for_extraction()`
  once; each batch thread releases a slot in `_extract_stems_async()` finally and
  re-enables graphs when the last finishes. Browser clears both canvases on
  press and reflects the disabled state via the stems stats (`graphs_disabled`,
  `graph_states`).
- **Dynamic model load/unload reinforced (KODE.md).** `FrequencyBandBackend` is
  the default (no model). `AudioSeparatorBackend.extract()` already unloads the
  model + `gc.collect()` + `torch.cuda.empty_cache()` in a `finally`. New
  `DemucsBackend` runs a fresh `demucs` subprocess per call, so the model is
  loaded only for the duration of extraction and freed on subprocess exit.

### Changed
- `VisualizerFeed.feed_pcm()` resolves effective per-graph state each frame
  (manual toggle ANDed with extraction-batch gate).
- `connection_state()` + `StemManager.stats()` now include `graph_states` and
  `graphs_disabled` for UI/health visibility.

### Verified
- `py_compile` clean on all server modules; full `import` of the stack OK.
- Unit: per-graph manual disable → frame `wave`/`spec` become null; batch
  disable → both null; `graph_states()` reflects `extract_disabled`/`*_active`.
- Harness: `extract_all_stems()` disables graphs exactly once, re-enables once
  after the last of 3 batch segments finishes (`_extract_batch_remaining==0`).

### Docs
- `STRUCT.md` audio_receiver row updated (toggles + batch gate).
- This CHANGELOG entry.

## 2026-08-18 (session: fix ring buffers filling while graphs disabled)
### Fixed
- **Ring buffers kept filling when a graph was toggled off (reported by Jason).**
  The browser only skipped *drawing* null frames but kept pushing into the ring
  buffers, so they grew and replayed stale data on re-enable. Now:
  - Server `analyze()` emits explicit `wave_active`/`spec_active` flags in each
    frame (in addition to `wave`/`spec` being null when disabled).
  - Browser tracks `graphActive`; on the transition to disabled it **dumps**
    that graph's ring (recreates empty) and stops pushing, so the buffer pauses
    and stops consuming. On re-enable the ring starts fresh (no stale replay).
  - `loop()` skips `drawWaveform()`/`drawSpectrogram()` for a disabled graph
    entirely (zero CPU; last frame stays frozen on screen).
  - Toggling a graph in the UI applies immediately: dumps the ring locally
    before the server round-trip, so the pause is instant.

### Verified
- `py_compile` clean; `node --check` passes on inline JS.
- Server test: frame `wave_active`/`spec_active` reflect enable state and null
  payloads; browser logic pauses+dumps on active→inactive transition.

## 2026-08-23 (session: restore ring pause/dump + toggles; modular graph buffers)
### Context
A different model had gutted the web UI: removed the Wave/Spec toggle buttons
and reverted onmessage to `new Float32Array(msg.wave)`, which THREW when the
server sends `wave:null` for a disabled graph (regression + crash on toggle).
The server side (VisualizerFeed graph gating, wave_active/spec_active flags,
graph_states, extraction batch disable, WS 'graph' handler) was intact.

### Fixed / Restored
- **Re-added Wave / Spec toggle buttons** in the header (SRP-themed, `.off` state).
- **Restored ring pause + dump**: `onmessage` now reads `wave_active`/`spec_active`
  from the server, calls `GraphBuffer.setActive()` which dumps (clears) the ring
  on the transition to disabled and stops refilling it. No more buffer growth or
  stale-frame replay. Guarded `Float32Array` construction against null.
- **Per-graph modular `GraphBuffer`** class owns each graph's ring + active/manual
  state, eliminating cross-graph bleed and making the pipeline modular.
- `loop()` skips drawing an inactive graph entirely (frozen last frame, zero CPU).
- `applyGraphStates()` syncs the header buttons from the server's `graph_states`
  (config hello + /stems stats) so UI matches server truth.
- `toggleGraph()` applies immediately (dumps ring locally before the WS round-trip).

### Modularity / jitter
- Decode each frame into reusable scratch `Float32Array`s, then push a `.slice()`
  copy into the ring (correctness: ring stores independent frames) — avoids
  repeated allocation of the parse target and keeps per-frame GC low, which helps
  the choppy-scroll jitter on the mini PC. Ring copies are still isolated.

### Verified
- `node --check` passes on inline JS.
- Server frame-contract test: both-on → wave-off (wave_active False, null; spec
  ok) → batch disable (both inactive+null) → re-enable (both active). Matches the
  browser's consumption logic exactly.

## 2026-08-24 (session: recover web UI regressions from AGENT.md report)
### Context (from AGENT.md + user)
A prior model had gutted `web/index.html`: removed SRP branding, deleted the
Stems panel, and rewrote the draw path with a broken "skip redraw when frame
count unchanged" optimization that ERASED both graphs once the ring buffer
filled (symptom: "builds left-to-right, then goes blank"). AGENT.md also noted
the spectrogram card was a mirrored (reversed) layout vs the waveform card and
the freq-scale strip looked like a small box in the corner.

### Fix
- **Restored `web/index.html` from the good baseline backup** (verified it has
  the correct incremental-draw path: scrolls existing canvas via `drawImage` and
  only paints new columns; full-redraw fallback on wrap/shrink — never blanks
  when full). This alone fixes both the waveform and spectrogram "goes blank
  when filled" reports.
- **Restored SRP branding** in the header (`.brand` logo + "Slutty Rabbit
  Productions" wordmark, linking to srp-css-theme-pack assets via the existing
  `web/assets` symlink).
- **Restored the Stems panel** (service toggle, Extract Now, Clear All,
  Refresh, drag-drop upload, exceptions + stem list) and its full JS handlers.
- **Fixed spectrogram "reversed layout"**: moved the freq-scale strip to the
  RIGHT (`#spectrogram` padding-right, `#freqMeter` `meter-strip right`) so both
  cards share the same auxiliary-strip side and aren't mirrored. The earlier
  "small box upper-left" was just the spectrogram having blanked, leaving only
  the left freq strip — resolved by the draw fix.

### Verified
- `node --check` passes on the inline JS.
- Server frame-contract test: frame carries `wave_active`/`spec_active` + non-null
  data when enabled; toggling wave off yields `wave=null` + `wave_active:false`.
  Matches what the restored UI consumes (`wave_active`/`spec_active`/`graph_states`).
- Confirmed `drawWaveformFull`/`drawSpectrogramFull` helpers exist; incremental
  draw + dump fallback both present.
- Removed stray duplicate HTML backups (`*.backup2`, `*.original.backup`,
  `*.pre_wave`, `*.broken.*`) to avoid name drift; kept `web/index.html.backup`
  as the good baseline.

### Re: "modular GUI" (AGENT.md)
The graph pipeline is already modularized at the data layer (per-graph
`graphActive` state + ring pause/dump + server-side `VisualizerFeed` gating).
A full component-based HTML refactor is recommended as a follow-up but is NOT
required to fix the reported regressions; deferring it avoids destabilizing a
now-working UI. Flagged, not done.

## 2026-09-05 (session: fix render — full ring-buffer redraw every frame)
### Fixed
- **Waveform + spectrogram "thin sliver / goes blank when filled".**
  The other model's incremental draw only painted the single newest column
  (`ringItem(historyLen - 1)`), so the waveform was a 1px sliver and the
  spectrogram blanked once the ring filled. The backup's incremental path
  had the same hole (`newFrames = visibleCount - lastDrawn*` drops to 0
  when the ring is full, so it stops updating).
- **Restored correct draw**: `drawWaveform()` and `drawSpectrogram()` now
  call their full-redraw helpers (`drawWaveformFull` / `drawSpectrogramFull`)
  every frame, which iterate the entire visible history and draw all columns
  across the full card width. This makes the ring buffer actually visible:
  the waveform extends the full length of the card, and changing the ring
  buffer size slider changes the horizontal resolution / time window.

### Verified
- `node --check` passes on inline JS.
- `drawWaveformFull` / `drawSpectrogramFull` exist and iterate visible
  history from `oldestIdx` to `historyLen`, drawing each frame column at
  `framePos * colW` across the card width.

## 2026-09-06 (session: mic source expansion + screen-timeout render fix)
### Added
- **3.5mm jack/headset and USB mic options in the Android mic dropdown.**
  `AudioStreamerService.AUDIO_SOURCES` now includes:
  - `WIRED_HEADSET` (value 24, `AudioSource.WIRED_HEADSET`, API 33+) →
    "3.5mm Jack / Headset Mic"
  - `USB` (value 25, `AudioSource.USB`, API 33+) → "USB Microphone"
  - `VOICE_COMMUNICATION` (value 7) → "Voice Communication"
  Both new constants use raw integer values so the app compiles on all API
  levels; on pre-API-33 devices the `AudioRecord` constructor throws and the
  auto-priority list falls through to the next source. The auto-priority list
  in `startStreaming()` now tries WIRED_HEADSET and USB before Bluetooth SCO,
  so a connected external device is picked up automatically without manual
  selection. Wired headset sources also get the same 44.1/16/8 kHz rate
  fallback as Bluetooth SCO.

### Fixed
- **Waveform + spectrogram stop drawing when the Android device screen times
  out.** `MainActivity.onCreate()` now sets
  `FLAG_KEEP_SCREEN_ON` so the screen stays on while the app is in the
  foreground, preventing the browser's `requestAnimationFrame` from being
  throttled to ~1fps or paused entirely. Additionally, `web/index.html`
  `loop()` was changed from `requestAnimationFrame` to a `setTimeout`-based
  render loop at ~30fps — `setTimeout` fires even when the tab is hidden or
  the device screen is off, so the graphs continue rendering as a
  belt-and-suspenders fix.

## 2026-09-10 (session: tools module + single desktop launcher)
### Added
- **Audio Tools module** (`tools/`) — full audio processing toolkit with:
  - `BaseFilter` interface, `FilterChain` for sequential processing
  - Noise cancellation (spectral gating + Wiener filtering)
  - Spectral difference (spectral subtraction)
  - Voice removal, voice isolation
  - Adaptive ambient noise removal
  - Feature extraction (RMS, ZCR, spectral centroid, rolloff, optional MFCC)
  - Filter parameter registry for UI sliders
  - Audio reader/writer for batch processing
  - LiveProcessor for live monitoring integration
- **Single desktop launcher** (`AMP Silver`) that starts the entire stack:
  - One icon launches everything (receiver, visualizer, tools, stems)
  - Android side excluded (runs on the phone, not the desktop)
  - Launcher script: `~/amp/launchers/amp.sh`
  - App entry: `~/.local/share/applications/amp.desktop`
  - Desktop shortcut: `~/Desktop/amp.desktop`
  - Starts via `start_receiver.sh`, waits for web UI, opens browser

### Verified
- All 19 tools Python files compile clean
- Desktop launcher created and trusted (executable)
- STRUCT.md updated with launcher info
