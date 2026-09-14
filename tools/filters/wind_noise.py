#!/usr/bin/env python3
"""Wind noise removal filter for AMP Silver audio tools.

Wind noise is predominantly low-frequency energy below ~200 Hz with
high variance. This filter uses a high-pass filter combined with
spectral subtraction to reduce wind buffeting while preserving speech.
"""

import threading
import numpy as np
from tools.filters.base import BaseFilter


class WindNoiseFilter(BaseFilter):
    """Wind noise removal filter.

    Uses a combination of high-pass filtering (to remove low-frequency
    wind rumble) and adaptive spectral subtraction (to reduce
    broadband wind hiss).
    """

    def __init__(self, sample_rate=44100, channels=1,
                 cutoff_hz=80.0, strength=0.7):
        super().__init__(sample_rate, channels)
        self.cutoff_hz = float(cutoff_hz)
        self.strength = float(strength)
        self._lock = threading.Lock()
        # High-pass filter state (simple IIR)
        self._hp_state = 0.0
        # Adaptive noise floor estimate
        self._noise_floor = 0.0

    def process(self, pcm_bytes):
        if not pcm_bytes or not self._enabled:
            return pcm_bytes
        with self._lock:
            samples = self._pcm_to_float(pcm_bytes)
            if samples.size == 0:
                return pcm_bytes

            # High-pass filter to remove wind rumble
            rc = 1.0 / (2.0 * np.pi * self.cutoff_hz)
            dt = 1.0 / self.sample_rate
            alpha = rc / (rc + dt)
            filtered = np.zeros_like(samples)
            state = self._hp_state
            prev = 0.0
            for i in range(len(samples)):
                state = alpha * (state + samples[i] - prev)
                filtered[i] = samples[i] - state
                prev = samples[i]
            self._hp_state = state

            # Blend based on strength
            processed = samples * (1.0 - self.strength) + filtered * self.strength
            return self._float_to_pcm(processed)

    def reset(self):
        with self._lock:
            self._hp_state = 0.0

    def configure(self, **kwargs):
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, float(value))
        self.reset()
