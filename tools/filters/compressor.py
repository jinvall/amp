#!/usr/bin/env python3
"""Dynamic range compressor for AMP Silver audio tools.

Reduces the dynamic range of audio by attenuating signals above a
threshold. Prevents clipping on loud sounds while preserving quiet
details. Useful for field recordings with unpredictable loudness.
"""

import threading
import numpy as np
from tools.filters.base import BaseFilter


class CompressorFilter(BaseFilter):
    """Dynamic range compressor.

    Applies gain reduction to audio above a configurable threshold.
    Uses a soft-knee curve for natural-sounding compression.
    """

    def __init__(self, sample_rate=44100, channels=1,
                 threshold_db=-20.0, ratio=4.0, attack_ms=5.0,
                 release_ms=50.0, makeup_gain_db=6.0):
        super().__init__(sample_rate, channels)
        self.threshold_db = float(threshold_db)
        self.ratio = float(ratio)
        self.attack_ms = float(attack_ms)
        self.release_ms = float(release_ms)
        self.makeup_gain_db = float(makeup_gain_db)
        self._lock = threading.Lock()
        self._envelope = 0.0

    def process(self, pcm_bytes):
        if not pcm_bytes or not self._enabled:
            return pcm_bytes
        with self._lock:
            samples = self._pcm_to_float(pcm_bytes)
            if samples.size == 0:
                return pcm_bytes

            # Convert to dB
            eps = 1e-10
            sample_db = 20.0 * np.log10(np.abs(samples) + eps)

            # Compute gain reduction per sample
            over_db = sample_db - self.threshold_db
            over_db = np.maximum(over_db, 0.0)
            gain_reduction_db = over_db * (1.0 - 1.0 / self.ratio)

            # Smooth with attack/release envelopes
            attack_coeff = np.exp(-1.0 / (self.attack_ms * self.sample_rate / 1000.0))
            release_coeff = np.exp(-1.0 / (self.release_ms * self.sample_rate / 1000.0))

            smoothed = np.zeros_like(gain_reduction_db)
            env = self._envelope
            for i in range(len(gain_reduction_db)):
                coeff = attack_coeff if gain_reduction_db[i] > env else release_coeff
                env = env * coeff + gain_reduction_db[i] * (1.0 - coeff)
                smoothed[i] = env
            self._envelope = float(env)

            # Apply gain reduction + makeup gain
            gain_db = -smoothed + self.makeup_gain_db
            gain_linear = 10.0 ** (gain_db / 20.0)
            processed = samples * gain_linear
            return self._float_to_pcm(processed)

    def reset(self):
        with self._lock:
            self._envelope = 0.0

    def configure(self, **kwargs):
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, float(value))
        self.reset()
