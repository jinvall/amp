# Profiles — Loadable Configuration System

Profiles are JSON files that bundle API keys, model settings, filter chains,
and processing presets into named, swappable configurations. They let you
switch between "modes" without touching code:

- `default.json` — local processing only (no API keys needed)
- `musify-dev.json` — Musify API integration (set your key)
- `jamjam-studio.json` — full studio: stems + pitch + chords + MIDI
- `transcribe-only.json` — whisper transcription optimized for low RAM

## Schema

```json
{
  "name": "My Profile",
  "version": 1,
  "description": "...",
  "active": true,

  "audio": {
    "sample_rate": 44100,
    "channels": 1,
    "bit_depth": 16
  },

  "stems": {
    "backend": "frequency_band",
    "max_storage_seconds": 1800
  },

  "filters": {
    "enabled": ["noise_cancellation", "compressor"],
    "params": {
      "noise_cancellation": {"strength": 0.6},
      "compressor": {"threshold_db": -18, "ratio": 3}
    }
  },

  "analysis": {
    "pitch": {"algorithm": "yin", "min_freq": 60, "max_freq": 4000},
    "chords": {"profiles": "standard"},
    "transcription": {
      "backend": "faster-whisper",
      "model_size": "base",
      "device": "cpu",
      "language": null
    }
  },

  "apis": {
    "musify": {
      "enabled": false,
      "base_url": "https://api.musify.example/v1",
      "key": "",
      "rate_limit_per_min": 60
    },
    "audD": {
      "enabled": false,
      "key": "",
      "rate_limit_per_min": 30
    }
  },

  "midi": {
    "output_enabled": false,
    "virtual_port": "AMP Output",
    "channel": 0
  },

  "ui": {
    "theme": "srp-dark",
    "editor_default_tab": "filters"
  }
}
```
