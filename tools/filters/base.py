#!/usr/bin/env python3
"""Base filter interface for AMP Silver audio tools.

All filters implement the same contract:
  - process(pcm_bytes: bytes) -> bytes: process a chunk of PCM audio
  - reset(): reset internal state (noise profiles, buffers, etc.)
  - configure(**kwargs): update filter parameters live

Thread-safe via threading.Lock. Uses numpy for all signal processing.
"""

import threading
import numpy as np


class BaseFilter:
    """Base class for all audio filters.

    Subclasses must implement process(). reset() and configure() are optional
    but recommended for stateful filters.

    All filters operate on 16-bit signed PCM mono audio at a configurable
    sample rate (default 44100 Hz, matching the AMP Silver stream).
    """

    def __init__(self, sample_rate: int = 44100, channels: int = 1):
        self.sample_rate = sample_rate
        self.channels = channels
        self._lock = threading.Lock()
        self._enabled = True

    @property
    def enabled(self) -> bool:
        """Whether this filter is currently enabled."""
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool):
        self._enabled = bool(value)

    def process(self, pcm_bytes: bytes) -> bytes:
        """Process a chunk of PCM audio and return filtered audio.

        Must be implemented by subclasses. Must be thread-safe (acquire
        self._lock if using shared state). Must not raise on empty input.
        Must return bytes of the same length as input (or as close as
        possible — some filters may add small latency).
        """
        raise NotImplementedError

    def reset(self):
        """Reset internal state (noise profiles, buffers, overlap state)."""
        pass

    def configure(self, **kwargs):
        """Update filter parameters live.

        Only updates attributes that already exist on the class. Unknown
        keys are silently ignored (subclasses can override to log them).
        """
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)

    def _pcm_to_float(self, pcm_bytes: bytes) -> np.ndarray:
        """Convert 16-bit PCM bytes to float64 array in range [-1, 1]."""
        if not pcm_bytes:
            return np.zeros(0, dtype=np.float64)
        return np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float64) / 32768.0

    def _float_to_pcm(self, samples: np.ndarray) -> bytes:
        """Convert float64 array in range [-1, 1] back to 16-bit PCM bytes."""
        clipped = np.clip(samples * 32768.0, -32768, 32767).astype(np.int16)
        return clipped.tobytes()

    def _pcm_to_int16(self, pcm_bytes: bytes) -> np.ndarray:
        """Convert 16-bit PCM bytes to int16 array."""
        if not pcm_bytes:
            return np.zeros(0, dtype=np.int16)
        return np.frombuffer(pcm_bytes, dtype=np.int16).copy()

    def __repr__(self):
        return f"{self.__class__.__name__}(sample_rate={self.sample_rate}, enabled={self._enabled})"
