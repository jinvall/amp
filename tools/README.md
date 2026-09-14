# AMP Silver Audio Tools

Modular audio processing toolkit for AMP Silver. Provides real-time and batch
audio filters for noise cancellation, voice isolation/removal, ambient
removal, spectral processing, and feature extraction.

## Purpose

The goal is **clear, chop-free audio** — noise reduction, voice
isolation/removal, ambient removal, and spectral processing that can run both
on live monitoring and on saved segments.

## Architecture

```
tools/
├── filters/          # Audio filter implementations
│   ├── base.py       # Base filter interface
│   ├── chain.py      # Filter chain (sequential processing)
│   ├── noise_cancellation.py
│   ├── spectral_difference.py
│   ├── voice_removal.py
│   ├── voice_isolation.py
│   ├── ambient_removal.py
│   └── feature_options.py
├── io/               # Audio file I/O
│   ├── reader.py     # Read WAV/FLAC segments
│   └── writer.py     # Write processed audio
├── live/             # Live monitoring integration
│   └── live_processor.py
└── web/              # Web UI
    └── tools_ui.html
```

## Two Modes

### Live Mode
Filters are applied to the live PCM stream from the Android client before
it reaches the audio monitor and visualizer. **Whatever filters are active
during monitoring get baked into saved recordings.**

### Batch Mode
Filters are applied to a selected WAV/FLAC file from `audio_segments/`.
User picks a file, selects filters, previews, then saves the result.

## Filter Interface

Every filter implements the same contract:

```python
class BaseFilter:
    def process(self, pcm_bytes: bytes) -> bytes:
        """Process a chunk of PCM audio and return filtered audio."""
        ...

    def reset(self):
        """Reset internal state."""
        ...

    def configure(self, **kwargs):
        """Update filter parameters live."""
        ...
```

## Resource Constraints

- CPU-only mini-PC (4 cores, 16 GB RAM)
- No heavy ML models by default
- All filters use numpy FFT
- Filter chain must be fast — drops frames rather than blocking

## Integration

The server imports the tools module:

```python
from tools.live.live_processor import LiveProcessor
from tools.filters.noise_cancellation import NoiseCancellationFilter

# Create live processor
processor = LiveProcessor(sample_rate=44100)
processor.add_filter(NoiseCancellationFilter(sample_rate=44100, strength=0.7))
processor.set_active(True)

# In the audio feed path:
processed = processor.process(pcm_bytes)
```

## Configuration

Filter states are persisted to `config.json`:

```json
{
  "tools_enabled": true,
  "tools_filters": ["noise_cancellation", "voice_isolation"],
  "noise_cancellation_strength": 0.7,
  "noise_cancellation_method": "spectral_gate"
}
```

## Key Principles

- **No silent failures** — log everything
- **No placeholders** — every function works
- **No hard-coded config** — everything is configurable
- **Chop-free audio** — smooth transitions, no clicks/pops
- **Resource-conscious** — make it count on the mini PC
