#!/usr/bin/env python3
"""AMP Silver Audio Tools — modular audio processing toolkit.

Provides real-time and batch audio filters for noise cancellation, voice
isolation/removal, ambient removal, spectral processing, and feature
extraction. Designed to run on the CPU-only mini PC alongside the main
AMP Silver receiver.

Two modes:
  - Live mode: filters applied to the live PCM stream before monitoring
  - Batch mode: filters applied to saved WAV/FLAC segments

All filters implement the BaseFilter interface: process(pcm_bytes) -> bytes.
"""

from tools.filters.base import BaseFilter
from tools.filters.chain import FilterChain
from tools.live.live_processor import LiveProcessor

__all__ = [
    'BaseFilter',
    'FilterChain',
    'LiveProcessor',
]
