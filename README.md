# Audio Streamer

Android app that streams microphone audio to a server (Silver) for segmented MP3 storage.

## Architecture

```
Android Device ──TCP/PCM (8090)──► Silver (see local-config.properties)
                                   │
                                   ├─ 5-minute WAV segments
                                   ├─ 5-second overlap between segments
                                   ├─ FIFO storage (~1 hour total)
                                   └─ VisualizerFeed (WebSocket :8092)
                                         │
                                         ► Browser UI (http://<silver>:8093/)
                                           waveform + spectrogram, ≤300 ms latency

Ports live in the 8090..8099 range (the 808x range was congested on the host).
Each listener auto-hunts for a free port in that range at startup unless an
explicit --port / --control-port / --viz-port is passed.
```

### Components

 1. **Android App** (`android-app/`)
    - Captures microphone audio via `AudioRecord` (44.1 kHz, 16-bit mono PCM)
    - Applies configurable amplification on-device
    - Streams raw PCM over TCP to Silver
    - UI to configure server IP, port, amplification, and breathing detection settings
    - Uses SRP theme pack colors (`srp-css-theme-pack/`)
    - Foreground service for reliable background streaming

  2. **Silver Receiver** (`server/audio_receiver.py`, run via `server/amp.py`)
     - Single entry point for the whole Silver stack is **`server/amp.py`** (one process:
       receiver + web UI, ports auto-allocated in 8090..8099, `/health` endpoint).
       `audio_receiver.py` is the receiver core and is imported by `amp.py`; running it
       directly delegates to `amp.py`, so there is exactly one startup path.
     - TCP server listening for PCM audio
     - Segments audio into 5-minute WAV files
     - Maintains 5-second overlap between consecutive segments
     - FIFO cleanup: removes oldest segments when total exceeds 1 hour
     - Optional real-time breathing detection using band-limited energy analysis
     - **Real-time visualization WebSocket feed** (default port `8092`): streams
       low-latency analysis frames (RMS + downsampled waveform + spectrogram column)
       for the web visualizer. See [Live Visualization](#live-visualization).

## Requirements

### Silver (Receiver)
- Python 3.8+
- `websockets` + `numpy` (`pip install -r server/requirements.txt`)

### Android
- Android 8.0 (API 26) or higher
- Permissions: `RECORD_AUDIO`, `INTERNET`, `FOREGROUND_SERVICE`

## Setup

### Single entry point (recommended)
Everything runs from **one process** via `server/amp.py` — the audio receiver
(PCM/control/viz WebSocket) and the web visualizer. No shell background jobs, no
orphaned children; ports auto-allocate in **8090..8099** unless overridden, and
a `/health` endpoint reports the resolved ports.

```bash
cd /home/jason/amp
pip install -r server/requirements.txt
./start_receiver.sh                 # execs server/amp.py; logs to logs/amp.log
# or directly:
python3 server/amp.py --host 0.0.0.0
```
Then open `http://<silver-ip>:8093/`. Override ports with `--pcm/--control/--viz/--web-port`
or `--no-viz`. Probe status with `curl http://<silver-ip>:8093/health`.

For an always-on service that survives reboots / SSH drops, the systemd unit invokes
the same entry point:
```bash
cp server/amp-receiver.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now amp-receiver.service
journalctl -u amp-receiver.service -f
```


### Android Setup
1. Open `android-app/` in Android Studio
2. Sync Gradle
3. Run on device or emulator
4. Grant microphone and notification permissions when prompted
5. Configure server IP in `local-config.properties`, port, and amplification
6. Tap **Start Streaming**

## Audio Format

- **Sample rate**: 44100 Hz
- **Channels**: 1 (mono)
- **Encoding**: PCM 16-bit signed little-endian
- **Transport**: TCP stream

## Segment Details

- **Duration**: 5 minutes per segment
- **Overlap**: 5 seconds between consecutive segments
- **Format**: WAV (44.1 kHz, 16-bit mono PCM)
- **Storage policy**: FIFO; oldest segments deleted when total exceeds 1 hour
- **Storage path**: `audio_segments/` (relative to receiver working directory)

## Files

```
amp/
├── android-app/
│   ├── app/
│   │   ├── build.gradle
│   │   └── src/main/
│   │       ├── AndroidManifest.xml
│   │       ├── java/com/amp/streamer/
│   │       │   ├── MainActivity.kt
│   │       │   └── AudioStreamerService.kt
│   │       └── res/
│   │           ├── drawable/
│   │           ├── layout/activity_main.xml
│   │           ├── mipmap-anydpi-v26/
│   │           ├── values/colors.xml
│   │           ├── values/strings.xml
│   │           └── values/themes.xml
│   ├── build.gradle
│   └── settings.gradle
├── server/
│   ├── amp.py                 # single entry point: receiver + web UI
│   ├── amp-receiver.service   # systemd unit (always-on)
│   ├── audio_receiver.py      # receiver core (used by amp.py)
│   ├── audio_segments/
│   └── requirements.txt
├── web/
│   ├── index.html             # visualizer UI (waveform + spectrogram)
│   └── serve.py               # static file server (imported by amp.py)
├── srp-css-theme-pack/
│   ├── css/
│   ├── js/
│   ├── assets/
│   └── *.md
└── README.md
```

## Theme

The Android app uses the **SRP CSS Theme Pack** (`srp-css-theme-pack/`) as its color guide.

### Mapped colors

| Token | Hex | Android color resource |
|------|-----|------------------------|
| `--color-bg` | `#0f0a18` | `@color/srp_bg` |
| `--color-surface` | `#1a1230` | `@color/srp_surface` |
| `--color-text` | `#f3f3f7` | `@color/srp_text` |
| `--color-text-muted` | `#c8c9d4` | `@color/srp_text_muted` |
| `--color-primary` | `#12f012` | `@color/srp_primary` |
| `--color-primary-contrast` | `#041105` | `@color/srp_primary_contrast` |
| `--color-secondary` | `#7a2fff` | `@color/srp_secondary` |
| `--color-accent` | `#00d1ff` | `@color/srp_accent` |
| `--color-border` | `#3d2d61` | `@color/srp_border` |

These are defined in:
- `android-app/app/src/main/res/values/colors.xml`
- `android-app/app/src/main/res/values/themes.xml`

The app theme is `Theme.SRP`, applied via `AndroidManifest.xml`.

## Notes

- The first segment has no leading overlap. Subsequent segments include the final 5 seconds of the previous segment.
- Total stored audio is approximately 1 hour of unique content (12 segments × ~5 min).
- The Android app sends raw PCM; segments are saved as WAV on Silver.

## Live Visualization

The receiver exposes a **real-time visualization WebSocket feed** so an incoming audio
stream can be drawn with end-to-end latency **≤ 300 ms** (mic → Silver → analysis →
WebSocket → browser paint).

### Latency budget

| Stage | Bound |
|-------|-------|
| Android mic capture (internal) | ~10–40 ms (device-dependent) |
| TCP PCM transport (LAN) | < 5 ms (`TCP_NODELAY` on) |
| Analysis (RMS + 256-pt waveform + FFT column) | ~1–3 ms/frame on Silver |
| WebSocket push | < 5 ms (LAN) |
| Browser paint (`requestAnimationFrame`) | < 8 ms |

The analysis is computed on **Silver** (not the browser) to keep the budget safe on weak
clients. One analysis frame is emitted per `VIZ_FRAME_SEC` (default **20 ms** → 50 fps).

### Run it

```bash
python3 server/amp.py           # receiver (8090/8091/8092) + web UI (:8093)
```

Then open `http://<silver-ip>:8093/` in a browser.

- The **Waveform** panel scrolls recent frames (click to freeze/unfreeze).
- The **Spectrogram** panel scrolls spectrogram columns (low → high freq top-to-bottom);
  colormap is selectable.
- The header shows live **RMS** and a **Latency** meter measured against the server
  frame timestamp. It turns yellow near the budget and red when exceeded.

### Tuning

Edit the constants near the top of `server/audio_receiver.py`:

- `VIZ_FRAME_SEC` — analysis frame duration (smaller = lower latency, more CPU)
- `VIZ_WAVEFORM_POINTS` — waveform resolution per frame
- `VIZ_SPECTROGRAM_BINS` — spectrogram column height
- `VIZ_MAX_LATENCY_MS` — the 300 ms budget (also sent to the client as `budget_ms`)
- `VIZ_PORT` — WebSocket port (override with `--viz-port`)

Disable the feed with `--no-viz`.

## Breathing Detection

The receiver can analyze audio in real time for breathing-like activity in the 100 Hz – 3 kHz band.

### How it works

- It slices incoming PCM into 2-second windows with 0.5-second hop.
- For each window it computes band-limited spectral energy.
- If the energy exceeds `BREATHING_ENERGY_THRESHOLD`, it emits a detection.
- Detections are throttled to at most one every `BREATHING_MIN_INTERVAL_SEC`.

### Tuning

Edit the constants at the top of `server/audio_receiver.py`:

- `BREATHING_BAND_LOW_HZ` / `BREATHING_BAND_HIGH_HZ`
- `BREATHING_ENERGY_THRESHOLD`
- `BREATHING_MIN_INTERVAL_SEC`
- `BREATHING_WINDOW_SEC` / `BREATHING_HOP_SEC`

### Android controls

The app exposes breathing detection controls via sliders:
- **Breathing Sensitivity** — mapped to detection energy threshold
- **Detection Cooldown** — mapped to minimum interval between detections

These are sent to the receiver as JSON config when streaming starts.

### Usage

Breathing detection is enabled by default. Disable it with:

```bash
python3 server/amp.py --no-breathing
```

Detections are printed to stdout alongside segment logs.
