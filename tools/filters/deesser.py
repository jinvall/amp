#!/usr/bin/env python3
"""De-esser filter for AMP Silver audio tools.

Reduces sibilance (harsh "s" and "sh" sounds) in vocal recordings.
Uses a band-pass filter to detect sibilant frequencies (4-10 kHz)
and applies gain reduction only when those frequencies are prominent.
"""

import threading
import numpy as np
from tools.filters.base import BaseFilter


class DeEsserFilter(BaseFilter):
    """De-esser filter.

    Detects sibilant frequencies and applies dynamic gain reduction
    only to those frequencies, leaving the rest of the audio untouched.
    """

    def __init__(self, sample_rate=44100, channels=1,
                 freq_low_hz=4000.0, freq_high_hz=10000.0,
                 threshold_db=-30.0, reduction_db=12.0):
        super().__init__(sample_rate, channels)
        self.freq_low_hz = float(freq_low_hz)
        self.freq_high_hz = float(freq_high_hz)
        self.threshold_db = float(threshold_db)
        self.reduction_db = float(reduction_db)
        self._lock = threading.Lock()
        self._prev_gain = 1.0

    def process(self, pcm_bytes):
        if not pcm_bytes or not self._enabled:
            return pcm_bytes
        with self._lock:
            samples = self._pcm_to_float(pcm_bytes)
            if samples.size == 0:
                return pcm_bytes

            # FFT to detect sibilant energy
            n = len(samples)
            spectrum = np.fft.rfft(samples)
            freqs = np.fft.rfftfreq(n, d=1.0 / self.sample_rate)
            magnitude = np.abs(spectrum)

            # Find sibilant band energy
            mask = (freqs >= self.freq_low_hz) & (freqs <= self.freq_high_hz)
            if not np.any(mask):
                return pcm_bytes

            sibilant_energy = np.mean(magnitude[mask])
            total_energy = np.mean(magnitude) + 1e-10
            sibilance_ratio = sibilant_energy / total_energy

            # Convert threshold to ratio
            threshold_ratio = 10.0 ** (self.threshold_db / 20.0)

            # Compute gain reduction
            if sibilance_ratio > threshold_ratio:
                excess = sibilance_ratio / threshold_ratio - 1.0
                target_gain = max(10.0 ** (-self.reduction_db / 20.0), 1.0 - excess * 0.5)
            else:
                target_gain = 1.0

            # Smooth gain
            alpha = 0.1  # smoothing factor
            gain = self._prev_gain * alpha + target_gain * (1.0 - alpha)
            self._prev_gain = gain

            # Apply only to sibilant frequencies in frequency domain
            processed_spectrum = spectrum.copy()
            processed_spectrum[mask] *= gain
            processed = np.fft.irfft(processed_spectrum, n=n)
            return self._float_to_pcm(processed)

    def reset(self):
        with self._lock:
            self._prev_gain = 1.0

    def configure(self, **kwargs):
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, float(value))
        self.reset()
