#!/usr/bin/env python3
"""Ambient noise removal filter for AMP Silver tools.

Adaptive ambient noise removal. Learns the ambient noise profile over time
and removes it. Good for HVAC, fan noise, room tone, and other steady-state
background noise.
"""

import threading
import numpy as np
from tools.filters.base import BaseFilter


class AmbientRemovalFilter(BaseFilter):
    """Adaptive ambient noise removal.

    Parameters:
        adaptation_rate: how quickly the noise profile adapts (default 0.05)
        reduction_db: how much ambient noise to remove (default 12)
        sensitivity: detection sensitivity for noise vs signal (default 1.5)
        window_size: FFT window size (default 2048)
    """

    def __init__(self, sample_rate=44100, channels=1,
                 adaptation_rate=0.05, reduction_db=12.0, sensitivity=1.5,
                 window_size=2048):
        super().__init__(sample_rate, channels)
        self.adaptation_rate = float(adaptation_rate)
        self.reduction_db = float(reduction_db)
        self.sensitivity = float(sensitivity)
        self.window_size = int(window_size)
        self.hop_size = self.window_size // 4
        self._window = np.hanning(self.window_size)
        self._noise_profile = None
        self._input_buffer = np.zeros(0, dtype=np.float64)
        self._overlap = np.zeros(self.hop_size, dtype=np.float64)
        self._lock = threading.Lock()

    def reset(self):
        with self._lock:
            self._noise_profile = None
            self._input_buffer = np.zeros(0, dtype=np.float64)
            self._overlap = np.zeros(self.hop_size, dtype=np.float64)

    def process(self, pcm_bytes):
        if not pcm_bytes or not self._enabled:
            return pcm_bytes
        with self._lock:
            samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float64)
            self._input_buffer = np.concatenate([self._input_buffer, samples])
            output_chunks = []

            while len(self._input_buffer) >= self.window_size:
                frame = self._input_buffer[:self.window_size]
                self._input_buffer = self._input_buffer[self.hop_size:]
                windowed = frame * self._window
                spectrum = np.fft.rfft(windowed)
                magnitude = np.abs(spectrum)
                phase = np.angle(spectrum)

                if self._noise_profile is None:
                    self._noise_profile = magnitude.copy()
                    output_chunks.append(frame[:self.hop_size])
                    continue

                alpha = self.adaptation_rate
                signal_ratio = magnitude / (self._noise_profile + 1e-8)
                is_noise = signal_ratio < self.sensitivity
                self._noise_profile = np.where(
                    is_noise,
                    alpha * magnitude + (1 - alpha) * self._noise_profile,
                    self._noise_profile
                )

                reduction_linear = 10 ** (-self.reduction_db / 20.0)
                gain = np.where(
                    signal_ratio > self.sensitivity,
                    1.0,
                    np.clip(signal_ratio / self.sensitivity, reduction_linear, 1.0)
                )

                filtered_spectrum = spectrum * gain
                filtered_frame = np.fft.irfft(filtered_spectrum, n=self.window_size)
                out_segment = filtered_frame[:self.hop_size] + self._overlap
                self._overlap = filtered_frame[self.hop_size:self.hop_size * 2]
                output_chunks.append(out_segment)

            if not output_chunks:
                return b''
            output = np.concatenate(output_chunks)
            return np.clip(output, -32768, 32767).astype(np.int16).tobytes()
