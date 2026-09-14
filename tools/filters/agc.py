#!/usr/bin/env python3
"""Automatic Gain Control for AMP Silver audio tools.

Maintains a consistent output level by automatically adjusting gain.
Unlike a compressor, AGC works on the entire signal to normalize
loudness over time. Useful for field recordings with varying distance
from the sound source.
"""

import threading
import numpy as np
from tools.filters.base import BaseFilter


class AGCFilter(BaseFilter):
    """Automatic Gain Control.

    Maintains target RMS level by slowly adjusting gain. Prevents
    sudden level changes while keeping audio audible.
    """

    def __init__(self, sample_rate=44100, channels=1,
                 target_rms=0.1, attack_ms=200.0, release_ms=1000.0,
                 max_gain_db=30.0, min_gain_db=-20.0):
        super().__init__(sample_rate, channels)
        self.target_rms = float(target_rms)
        self.attack_ms = float(attack_ms)
        self.release_ms = float(release_ms)
        self.max_gain_db = float(max_gain_db)
        self.min_gain_db = float(min_gain_db)
        self._lock = threading.Lock()
        self._gain_db = 0.0

    def process(self, pcm_bytes):
        if not pcm_bytes or not self._enabled:
            return pcm_bytes
        with self._lock:
            samples = self._pcm_to_float(pcm_bytes)
            if samples.size == 0:
                return pcm_bytes

            # Compute current RMS
            current_rms = np.sqrt(np.mean(samples ** 2))
            if current_rms < 1e-10:
                return pcm_bytes

            # Desired gain in dB
            desired_gain_db = 20.0 * np.log10(self.target_rms / current_rms)

            # Clamp to safe range
            desired_gain_db = max(self.min_gain_db, min(self.max_gain_db, desired_gain_db))

            # Smooth the gain adjustment (slow attack, slower release)
            if desired_gain_db > self._gain_db:
                coeff = np.exp(-1.0 / (self.attack_ms * self.sample_rate / 1000.0))
            else:
                coeff = np.exp(-1.0 / (self.release_ms * self.sample_rate / 1000.0))

            self._gain_db = self._gain_db * coeff + desired_gain_db * (1.0 - coeff)

            # Apply gain
            gain_linear = 10.0 ** (self._gain_db / 20.0)
            processed = samples * gain_linear
            return self._float_to_pcm(processed)

    def reset(self):
        with self._lock:
            self._gain_db = 0.0

    def configure(self, **kwargs):
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, float(value))
        self.reset()
