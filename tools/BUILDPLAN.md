# BUILDPLAN.md — AMP Silver Audio Tools

## Objective

Build a modular audio processing toolkit at `~/amp/tools/` that provides
real-time and batch audio filters for the AMP Silver stack. The primary goal
is **clear, chop-free audio** — noise reduction, voice isolation/removal,
ambient removal, and spectral processing that can run both on live monitoring
and on saved segments.

## Assumptions (correct me if wrong)

1. **"Feature options"** = configurable filter parameters (thresholds, window
   sizes, attenuation levels) AND audio feature extraction (MFCC, spectral
   centroid, zero-crossing rate, spectral flux) for analysis.
2. **"Spectral difference"** = spectral subtraction — estimate a noise
   profile, subtract it from the signal spectrum. Classic denoising.
3. **"Selected audio"** = existing WAV/FLAC segments in `audio_segments/`
   that the user selects for batch processing.
4. **Live monitoring save** = whatever filters are active on the live monitor
   feed get baked into the saved recording. No filters = raw save.
5. **Architecture** = standalone Python module in `~/amp/tools/` that
   `server/audio_receiver.py` imports and calls. Keeps things modular.
6. **Noise cancelation** = adjustable strength. User controls how aggressive.

## Architecture

```
~/amp/tools/
├── BUILDPLAN.md          # This file
├── TODO.md               # VS Code task list
├── README.md             # Overview + usage
├── __init__.py           # Package init, public API
├── filters/
│   ├── __init__.py
│   ├── base.py           # BaseFilter interface (process/reset/configure)
│   ├── noise_cancellation.py   # Multi-method noise cancellation
│   ├── spectral_difference.py  # Spectral subtraction denoiser
│   ├── voice_removal.py        # Remove voice band
│   ├── voice_isolation.py      # Isolate voice band
│   ├── ambient_removal.py      # Adaptive ambient noise removal
│   └── feature_options.py      # Feature extraction + filter param config
├── io/
│   ├── __init__.py
│   ├── reader.py         # Read WAV/FLAC, resample, normalize
│   └── writer.py         # Write WAV/FLAC with metadata
├── live/
│   ├── __init__.py
│   └── live_processor.py # Live monitoring filter chain
└── web/
    └── tools_ui.html     # UI panel (SRP-themed)
```

## Core Design

### Base Filter Interface

Every filter implements the same contract (consistent with existing
`noise_suppression.NoiseSuppressor`):

```python
class BaseFilter:
    def __init__(self, sample_rate=44100, channels=1):
        self.sample_rate = sample_rate
        self.channels = channels
        self._lock = threading.Lock()

    def process(self, pcm_bytes: bytes) -> bytes:
        """Process a chunk of PCM audio and return filtered audio."""
        raise NotImplementedError

    def reset(self):
        """Reset internal state (noise profiles, buffers, etc.)."""
        pass

    def configure(self, **kwargs):
        """Update filter parameters live."""
        for k, v in kwargs.items():
            if hasattr(self, k):
                setattr(self, k, v)
```

### Filter Chain

A `FilterChain` holds an ordered list of filters and runs them sequentially:

```python
class FilterChain:
    def __init__(self):
        self.filters = []

    def add(self, filt):
        self.filters.append(filt)

    def remove(self, filt):
        self.filters.remove(filt)

    def process(self, pcm_bytes):
        data = pcm_bytes
        for f in self.filters:
            data = f.process(data)
        return data

    def reset(self):
        for f in self.filters:
            f.reset()
```

### Two Modes

1. **Live mode** — `FilterChain` is attached to `VisualizerFeed`. Every PCM
   chunk from the Android client passes through the chain before being sent
   to the live monitor and visualizer. Whatever is active here is what gets
   saved.

2. **Batch mode** — `FilterChain` is applied to a selected WAV/FLAC file
   from `audio_segments/`. User picks a file, selects filters, previews,
   then saves the result.

## Filters to Implement

### 1. Noise Cancellation (`noise_cancellation.py`)

Multi-method noise cancellation with adjustable strength.

**Methods:**
- **Spectral Gating** (existing `SpectralGateSuppressor` — extend it)
- **Wiener Filtering** — estimate clean signal using Wiener filter
- **RNNoise** (existing — wrap it here too)

**Parameters:**
- `strength` (0.0–1.0) — overall aggressiveness
- `noise_floor_db` — noise floor threshold
- `attenuation_db` — how much to attenuate noise
- `method` — which algorithm to use

### 2. Spectral Difference (`spectral_difference.py`)

Spectral subtraction denoiser. Estimates noise profile from silent frames,
subtracts from signal spectrum.

**Parameters:**
- `noise_frames` — number of initial frames to use for noise profile
- `reduction_amount` (0.0–1.0) — how much noise to remove
- `smoothing` — temporal smoothing factor to reduce musical noise

### 3. Voice Removal (`voice_removal.py`)

Remove/attenuate the human voice band (80 Hz – 4 kHz).

**Parameters:**
- `voice_low_hz` / `voice_high_hz` — voice band edges
- `attenuation_db` — how much to cut the voice band
- `method` — spectral gate or center-channel inversion (if stereo)

### 4. Voice Isolation (`voice_isolation.py`)

Isolate the human voice band, attenuate everything else.

**Parameters:**
- `voice_low_hz` / `voice_high_hz` — voice band edges
- `preserve_db` — how much of the voice band to keep
- `attenuate_db` — how much to cut non-voice frequencies

### 5. Ambient Removal (`ambient_removal.py`)

Adaptive ambient noise removal. Learns the ambient noise profile over time
and removes it. Good for HVAC, fan noise, room tone.

**Parameters:**
- `adaptation_rate` — how quickly the noise profile adapts
- `reduction_db` — how much ambient to remove
- `sensitivity` — how sensitive the detector is

### 6. Feature Options (`feature_options.py`)

Two parts:

**A) Filter parameter configuration** — a registry of all configurable
parameters for each filter, with valid ranges, defaults, and descriptions.
Used by the web UI to render sliders/controls.

**B) Audio feature extraction** — compute features from PCM:
- RMS energy
- Zero-crossing rate
- Spectral centroid
- Spectral rolloff
- Spectral flux
- MFCCs (if librosa available, optional)

## I/O Layer

### Reader (`io/reader.py`)

- Read WAV/FLAC files from `audio_segments/`
- Convert to mono float64 at target sample rate
- Handle resampling (linear interpolation, no scipy dependency)

### Writer (`io/writer.py`)

- Write processed audio as WAV or FLAC
- Include metadata: processing chain applied, timestamp, parameters
- Save to `audio_segments/` or a new `audio_processed/` directory

## Live Integration

### Live Processor (`live/live_processor.py`)

Wraps `FilterChain` for the live monitoring path:

```python
class LiveProcessor:
    def __init__(self, sample_rate=44100):
        self.chain = FilterChain()
        self.sample_rate = sample_rate
        self.is_active = False

    def add_filter(self, filt):
        self.chain.add(filt)

    def remove_filter(self, filt):
        self.chain.remove(filt)

    def process(self, pcm_bytes):
        if not self.is_active or not self.chain.filters:
            return pcm_bytes
        return self.chain.process(pcm_bytes)

    def set_active(self, active):
        self.is_active = active
```

### Integration with `audio_receiver.py`

The `VisualizerFeed.feed_pcm()` method will pass PCM through the
`LiveProcessor` before sending to the audio monitor. This means:
- If filters are active → saved audio includes filters
- If no filters → saved audio is raw

## Web UI

### Tools Panel (`web/tools_ui.html`)

A new panel in the web interface (SRP-themed) with:
- Filter enable/disable toggles
- Per-filter parameter sliders
- Live/batch mode toggle
- File selector for batch mode
- Preview button (hear the effect before saving)
- Save button (save processed audio)
- Visual feedback (before/after waveform, feature readouts)

## Resource Constraints

- CPU-only mini-PC (4 cores, 16 GB RAM)
- No heavy ML models by default
- All filters use numpy FFT (fast, low memory)
- Filter chain runs in the audio worker thread — must be fast
- If a filter is too slow, it drops frames rather than blocking

## Phase Plan

### Phase 1: Foundation
- Base filter interface
- Filter chain
- I/O reader/writer
- Unit tests

### Phase 2: Core Filters
- Noise cancellation (spectral gating + Wiener)
- Spectral difference
- Voice removal
- Voice isolation

### Phase 3: Advanced Filters
- Ambient removal
- Feature extraction
- Feature parameter registry

### Phase 4: Live Integration
- Live processor
- Wire into `audio_receiver.py`
- Config persistence

### Phase 5: Web UI
- Tools panel
- Filter controls
- Batch processing UI
- Save functionality

### Phase 6: Polish
- Performance optimization
- Edge case handling
- Documentation
- Integration tests

## Verification

- Unit tests for each filter (synthetic audio, known results)
- Integration test: filter chain on a real segment
- Live test: apply filter, verify output sounds clean
- Resource test: verify CPU/RAM stay within bounds
- Save test: verify saved file includes filters when active

## Key Principles

- **No silent failures** — log everything (KODE mandate)
- **No placeholders** — every function works
- **No hard-coded config** — everything is configurable
- **Chop-free audio** — smooth transitions, no clicks/pops
- **Resource-conscious** — make it count on the mini PC
