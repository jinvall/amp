# AMP Silver — Project Overview

**AMP Silver** is a real-time audio streaming, processing, and visualization
system for a private LAN/trusted edge environment. An Android phone streams
microphone audio to a Linux server (Silver) which processes, stores, and
visualizes the audio in real time.

---

## Architecture

```
Android Phone ──TCP PCM (port 8090)──► Silver Server
                                           │
                                           ├─ Segments (WAV, 5 min, FIFO ~1h)
                                           ├─ Stems (separated audio tracks)
                                           ├─ Live Monitor (speakers via ffplay/aplay)
                                           └─ WebSocket (port 8092)
                                                 │
                                                 ▼
                                           Web UI (port 8093)
                                           waveform + spectrogram
```

---

## Core Features

### 1. Audio Capture & Transport
- **Android App** captures 44.1 kHz, 16-bit mono PCM via `AudioRecord`
- Raw PCM streamed over TCP to server
- Configurable amplification on-device
- Foreground service for reliable background streaming
- Controls for server IP, port, amplification, and breathing detection

### 2. Server Receiver
- TCP server listening for PCM data (auto-hunts ports 8090..8099)
- Segments audio into 5-minute WAV files with 5-second overlap
- FIFO cleanup: removes oldest segments when total exceeds 1 hour
- Optional real-time breathing detection (100 Hz – 3 kHz band energy)
- HTTP server (port 8093) serving the web UI + `/health` and `/stems` endpoints
- Control channel (port 8091) for live config push via JSON

### 3. Real-Time Visualization
- WebSocket feed (port 8092) for low-latency analysis frames
- **Latency budget:** 300 ms end-to-end (mic → analysis → browser paint)
- Waveform display (256 points per frame, 50 fps)
- Spectrogram display (256 bins, colormap selectable)
- RMS meter and latency indicator
- Per-graph enable/disable toggles

### 4. Audio Tools (Filter System)
Modular filter chain that can run on live monitoring and saved segments.
All filters use numpy FFT and operate on 16-bit PCM chunks.

| Filter | Description |
|--------|-------------|
| **Noise Cancellation** | Spectral gating to remove steady noise |
| **Spectral Difference** | Spectral subtraction with auto-calibration and adaptive tracking |
| **Voice Isolation** | Isolate voice frequencies, attenuate rest |
| **Voice Removal** | Remove voice frequencies, preserve music/ambience |
| **Ambient Removal** | Remove ambient background |
| **Feature Extraction** | Audio feature analysis |
| **Compressor** | Dynamic range compression |
| **AGC** | Automatic Gain Control |
| **Wind Noise Removal** | Remove wind/broadband noise |
| **De-Esser** | Reduce sibilance |
| **Equalizer** | 10-band parametric EQ (32 Hz – 16 kHz) |

**Filter features:**
- Live preview on streaming audio
- Batch processing on saved segments
- Per-filter parameter configuration via UI
- Chaining (filters applied sequentially)
- Enable/disable individual filters without removing

### 5. Stem Extraction
Separate mixed audio into component tracks (vocals, drums, bass, other).

| Backend | Speed | Quality | Notes |
|---------|-------|---------|-------|
| **Frequency Band** | Instant | Basic | RAM-free, splits by frequency range |
| **Audio-Separator** | Slow (CPU) | Good | ML-based, separate venv with models |
| **Demucs (opt-in)** | Slow (CPU) | Best | Real vocal/instrument separation |

- Capped rotation (default 1800 seconds / 30 minutes)
- `/stems` endpoint for segment/stem listings
- Extraction pauses graphs during processing to free CPU/RAM

### 6. Live Audio Monitor
Play streaming audio through system speakers in real time.
Uses `ffplay` (preferred) or `aplay` as output backend.
Auto-starts on server boot.

---

## Ports

| Port | Protocol | Role |
|------|----------|------|
| 8090 | TCP | PCM audio from Android |
| 8091 | TCP | Control (JSON config push) |
| 8092 | WebSocket | Visualization frames |
| 8093 | HTTP | Web UI + API |

All auto-allocate in the 8090..8099 range unless overridden.

---

## File Layout

```
amp/
├── start_receiver.sh          # Single entry point launcher
├── android-app/               # Android client (Kotlin)
├── server/
│   ├── amp.py                 # HTTP server, /health, /stems, web UI
│   ├── audio_receiver.py      # PCM receiver, segmentation, viz feed
│   ├── stem_manager.py        # Stem rotation/capping
│   ├── stem_backends.py       # Frequency-band + Demucs backends
│   ├── audio_separator_backend.py  # ML audio-separator backend
│   └── requirements.txt       # websockets, numpy
├── web/
│   ├── index.html             # Visualizer UI (waveform + spectrogram)
│   └── serve.py               # Static file server
├── tools/
│   ├── filters/               # Audio filter implementations
│   │   ├── base.py            # Base filter interface
│   │   ├── chain.py           # Filter chain (sequential)
│   │   ├── noise_cancellation.py
│   │   ├── spectral_difference.py
│   │   ├── voice_isolation.py
│   │   ├── voice_removal.py
│   │   ├── ambient_removal.py
│   │   ├── feature_options.py
│   │   ├── compressor.py
│   │   ├── agc.py
│   │   ├── wind_noise.py
│   │   ├── deesser.py
│   │   ├── equalizer.py
│   │   └── eq_filter.py
│   ├── live/
│   │   └── live_processor.py  # Live monitoring filter processor
│   ├── io/
│   │   ├── reader.py          # WAV/FLAC reader
│   │   └── writer.py          # Audio writer
│   └── analysis/
│       ├── pitch_detection.py
│       ├── chord_recognition.py
│       └── audio_transcribe.py
├── srp-css-theme-pack/        # Branding (SRP)
├── config.json                # Persisted settings
├── audio_segments/            # Saved WAV segments
├── audio_stems/               # Extracted stems
└── logs/amp.log               # Unified log
```

---

## Known Issues (Current)

1. **WebSocket connections from browser failing** — `location.hostname` is
   empty when opening HTML via `file://`, causing invalid WebSocket URL
2. **Filter system disconnected** — `LiveProcessor`, `FilterChain`, and all
   filter implementations exist but are completely unplugged from the server
3. **Audio monitor doesn't auto-start** — only starts when UI connects via
   WebSocket; never starts if UI can't connect
4. **JavaScript syntax errors** — HTML contains inline JS with syntax issues
   from incremental edits
5. **Android app needs rebuild** — broken-pipe crash fix pending
6. **UI scrolling is choppy** — graphs correct but not smooth (deferred)

---

## Project Status

| Component | State |
|-----------|-------|
| Audio capture (Android) | Working, needs rebuild |
| PCM transport (TCP) | Working |
| Segmentation & storage | Working |
| Breathing detection | Working |
| Web UI visualization | Working (when connected) |
| Audio monitor (speakers) | Not auto-starting |
| Filter/tools system | Built but unplugged |
| Stem extraction (freq band) | Working |
| Stem extraction (ML) | Working, slow |
| Deployment readiness | Not ready (security, resilience, storage) |

---

## What Needs to Happen

### Critical (breaks core functionality)
- Fix WebSocket connection for cross-machine access
- Plug `LiveProcessor` and filter system into the server
- Auto-start audio monitor on server boot
- Fix JavaScript syntax errors in HTML

### High (needed for full feature set)
- Rebuild Android app with broken-pipe fix
- Add calibrate button and adaptive toggle to spectral difference UI
- Wire `tools` and `tools_cmd` messages through WebSocket handler

### Medium (polish)
- Improve UI scrolling smoothness
- Add GUI service-control buttons
- Commit and push verified fixes

### Low (future)
- Deployment hardening (auth, storage guards, resilience)
- VPN/tunnel documentation
