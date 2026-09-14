#!/usr/bin/env python3
"""Spectral difference (spectral subtraction) filter for AMP Silver tools.

Estimates a noise profile from initial silent frames, then subtracts it
from the signal spectrum. Reduces steady-state noise while preserving
transient sounds and voice.

Features:
  - Auto-calibration: first N frames build the noise profile
  - Adaptive tracking: continuously update profile during low-energy frames
  - Recalibrate: reset and re-learn on demand (UI button)
"""

import threading
import numpy as np
from tools.filters.base import BaseFilter


class SpectralDifferenceFilter(BaseFilter):
    """Spectral subtraction denoiser.

    Parameters:
        noise_frames: number of initial frames used for noise profile estimation
        reduction_amount (0.0-1.0): how much noise to remove (1.0 = full subtraction)
        smoothing (0.0-1.0): temporal smoothing to reduce musical noise artifacts
        window_size: FFT window size (default 2048)
        adaptive (bool): continuously update noise profile during low-energy frames
        adaptive_threshold_db (dB): energy threshold below which a frame is "noise"
    """

    def __init__(self, sample_rate=44100, channels=1,
                 noise_frames=30, reduction_amount=0.8, smoothing=0.5,
                 window_size=2048, adaptive=False, adaptive_threshold_db=-40):
        super().__init__(sample_rate, channels)
        self.noise_frames = int(noise_frames)
        self.reduction_amount = float(reduction_amount)
        self.smoothing = float(smoothing)
        self.window_size = int(window_size)
        self.adaptive = bool(adaptive)
        self.adaptive_threshold_db = float(adaptive_threshold_db)
        self.hop_size = self.window_size // 4
        self._window = np.hanning(self.window_size)
        self._noise_profile = None
        self._frame_count = 0
        self._prev_gain = None
        self._input_buffer = np.zeros(0, dtype=np.float64)
        self._overlap = np.zeros(self.hop_size, dtype=np.float64)
        self._lock = threading.Lock()

    @property
    def state(self):
        """Return current filter state for UI display."""
        with self._lock:
            return {
                "calibrated": self._frame_count >= self.noise_frames,
                "frame_count": self._frame_count,
                "noise_frames": self.noise_frames,
                "adaptive": self.adaptive,
            }

    def recalibrate(self):
        """Reset noise profile and re-learn from next N frames."""
        with self._lock:
            self._noise_profile = None
            self._frame_count = 0
            self._prev_gain = None
            self._input_buffer = np.zeros(0, dtype=np.float64)
            self._overlap = np.zeros(self.hop_size, dtype=np.float64)

    def reset(self):
        self.recalibrate()

    def configure(self, **kwargs):
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)

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
                power = magnitude ** 2

                if self._noise_profile is None or self._frame_count < self.noise_frames:
                    # Learning phase: only build noise profile from LOW-ENERGY
                    # (silent) frames. Skip loud frames so we don't learn the
                    # signal itself as "noise" — which would cause the filter
                    # to subtract the signal from itself after calibration.
                    frame_db = 10 * np.log10(np.mean(power) + 1e-10)
                    if frame_db < self.adaptive_threshold_db:
                        alpha = 0.9
                        if self._noise_profile is None:
                            self._noise_profile = power.copy()
                        else:
                            self._noise_profile = alpha * self._noise_profile + (1 - alpha) * power
                        self._frame_count += 1
                    # Always pass audio through during calibration
                    output_chunks.append(frame[:self.hop_size])
                    continue

                # Subtract noise profile (pass through if not yet calibrated)
                if self._noise_profile is None:
                    output_chunks.append(frame[:self.hop_size])
                    continue
                subtract = self.reduction_amount * self._noise_profile
                clean_power = np.maximum(power - subtract, 0.0)
                gain = np.sqrt(clean_power / (power + 1e-8))
                gain = np.clip(gain, 0.0, 1.0)

                # Temporal smoothing
                if self._prev_gain is not None and self.smoothing > 0:
                    gain = self.smoothing * self._prev_gain + (1 - self.smoothing) * gain
                self._prev_gain = gain.copy()

                # Adaptive tracking: update profile during low-energy frames
                if self.adaptive:
                    frame_db = 10 * np.log10(np.mean(power) + 1e-10)
                    if frame_db < self.adaptive_threshold_db:
                        alpha = 0.95
                        self._noise_profile = alpha * self._noise_profile + (0.05) * power

                filtered_spectrum = spectrum * gain
                filtered_frame = np.fft.irfft(filtered_spectrum, n=self.window_size)
                out_segment = filtered_frame[:self.hop_size] + self._overlap
                self._overlap = filtered_frame[self.hop_size:self.hop_size * 2]
                output_chunks.append(out_segment)

            if not output_chunks:
                return b''
            output = np.concatenate(output_chunks)
            return np.clip(output, -32768, 32767).astype(np.int16).tobytes()
