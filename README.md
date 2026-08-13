# Audio Streamer

Android app that streams microphone audio to a server (Silver) for segmented MP3 storage.

## Architecture

```
Android Device ──TCP/PCM──► Silver (see local-config.properties)
                                 │
                                 ├─ 5-minute MP3 segments
                                 ├─ 5-second overlap between segments
                                 └─ FIFO storage (~1 hour total)
```

### Components

 1. **Android App** (`android-app/`)
    - Captures microphone audio via `AudioRecord` (44.1 kHz, 16-bit mono PCM)
    - Applies configurable amplification on-device
    - Streams raw PCM over TCP to Silver
    - UI to configure server IP, port, amplification, and breathing detection settings
    - Uses SRP theme pack colors (`srp-css-theme-pack/`)
    - Foreground service for reliable background streaming

 2. **Silver Receiver** (`server/audio_receiver.py`)
    - TCP server listening for PCM audio
    - Encodes incoming PCM to MP3 using `lameenc`
    - Segments audio into 5-minute files
    - Maintains 5-second overlap between consecutive segments
    - FIFO cleanup: removes oldest segments when total exceeds 1 hour
    - Optional real-time breathing detection using band-limited energy analysis

## Requirements

### Silver (Receiver)
- Python 3.8+
- `lameenc` Python package (requires `liblame` system library)

### Android
- Android 8.0 (API 26) or higher
- Permissions: `RECORD_AUDIO`, `INTERNET`, `FOREGROUND_SERVICE`

## Setup

### Silver Setup
```bash
cd /home/jason/amp/server
pip install -r requirements.txt
python3 audio_receiver.py --host 0.0.0.0 --port 8080 --output-dir ./audio_segments
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
│   ├── audio_receiver.py
│   ├── audio_segments/
│   └── requirements.txt
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
python3 audio_receiver.py --no-breathing
```

Detections are printed to stdout alongside segment logs.
