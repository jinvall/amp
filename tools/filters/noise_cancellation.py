#!/usr/bin/env python3
"""Noise cancellation filter for AMP Silver tools.

Multi-method noise cancellation with adjustable strength:
- spectral_gate: FFT-based spectral gating (default, fast)
- wiener: Wiener filtering for stationary noise
- rnnoise: Neural noise suppression via RNNoise (optional, requires pyrnnoise+soxr)

All methods preserve voice quality while reducing ambient noise.
"""

import threading
import numpy as np
from tools.filters.base import BaseFilter


class NoiseCancellationFilter(BaseFilter):
    """Multi-method noise cancellation with adjustable strength.

    Parameters:
        strength (0.0-1.0): overall aggressiveness
        noise_floor_db: noise floor threshold in dB
        attenuation_db: how much to attenuate noise
        method: 'spectral_gate', 'wiener', or 'rnnoise'
    """

    def __init__(self, sample_rate=44100, channels=1,
                 strength=0.5, noise_floor_db=1.5, attenuation_db=9.0,
                 method='spectral_gate'):
        super().__init__(sample_rate, channels)
        self.strength = float(strength)
        self.noise_floor_db = float(noise_floor_db)
        self.attenuation_db = float(attenuation_db)
        self.method = method
        self._impl = self._create_impl()
        self._lock = threading.Lock()

    def _create_impl(self):
        if self.method == 'rnnoise':
            try:
                from server.noise_suppression import RNNoiseSuppressor
                return RNNoiseSuppressor(self.sample_rate, self.channels)
            except Exception:
                pass
        if self.method == 'wiener':
            return WienerDenoiser(self.sample_rate, self.strength)
        return SpectralGateDenoiser(self.sample_rate, self.noise_floor_db,
                                    self.attenuation_db, self.strength)

    def process(self, pcm_bytes):
        if not pcm_bytes or not self._enabled:
            return pcm_bytes
        with self._lock:
            return self._impl.process(pcm_bytes)

    def reset(self):
        with self._lock:
            if hasattr(self._impl, 'reset'):
                self._impl.reset()

    def configure(self, **kwargs):
        super().configure(**kwargs)
        if 'method' in kwargs or 'strength' in kwargs:
            with self._lock:
                self._impl = self._create_impl()


class SpectralGateDenoiser:
    """FFT-based spectral gating denoiser."""

    def __init__(self, sample_rate, noise_floor_db, attenuation_db, strength):
        self.sample_rate = sample_rate
        self.noise_floor_db = noise_floor_db
        self.attenuation_db = attenuation_db
        self.strength = strength
        self.window_size = 2048
        self.hop_size = self.window_size // 4
        self._window = np.hanning(self.window_size)
        self._noise_profile = None
        self._frame_count = 0
        self._noise_frames = 30
        self._input_buffer = np.zeros(0, dtype=np.float64)
        self._overlap = np.zeros(self.hop_size, dtype=np.float64)

    def reset(self):
        self._noise_profile = None
        self._frame_count = 0
        self._input_buffer = np.zeros(0, dtype=np.float64)
        self._overlap = np.zeros(self.hop_size, dtype=np.float64)

    def process(self, pcm_bytes):
        if not pcm_bytes:
            return pcm_bytes
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

            if self._frame_count < self._noise_frames:
                alpha = 0.9
                if self._noise_profile is None:
                    self._noise_profile = magnitude.copy()
                else:
                    self._noise_profile = alpha * self._noise_profile + (1 - alpha) * magnitude
                self._frame_count += 1
                output_chunks.append(frame[:self.hop_size])
                continue

            noise_floor = self._noise_profile * (10 ** (self.noise_floor_db / 20.0))
            ratio = magnitude / (noise_floor + 1e-8)
            soft_gain = np.clip((ratio - 1.0) / (1.5 + self.noise_floor_db), 0.0, 1.0)
            soft_gain = np.where(ratio > 1.0, 1.0 - (1.0 - soft_gain) * 0.35, 0.55)
            soft_gain = np.clip(soft_gain, 0.0, 1.0)
            gate_floor = 10 ** (-self.attenuation_db / 20.0)
            mask = np.maximum(soft_gain, gate_floor)
            mask = mask * self.strength + (1.0 - self.strength)

            gated_magnitude = magnitude * mask
            gated_spectrum = gated_magnitude * np.exp(1j * phase)
            gated_frame = np.fft.irfft(gated_spectrum, n=self.window_size)
            out_segment = gated_frame[:self.hop_size] + self._overlap
            self._overlap = gated_frame[self.hop_size:self.hop_size * 2]
            output_chunks.append(out_segment)

        if not output_chunks:
            return b''
        output = np.concatenate(output_chunks)
        return np.clip(output, -32768, 32767).astype(np.int16).tobytes()


class WienerDenoiser:
    """Wiener filter denoiser for stationary noise."""

    def __init__(self, sample_rate, strength=0.5):
        self.sample_rate = sample_rate
        self.strength = strength
        self.window_size = 2048
        self.hop_size = self.window_size // 4
        self._window = np.hanning(self.window_size)
        self._noise_profile = None
        self._frame_count = 0
        self._noise_frames = 30
        self._input_buffer = np.zeros(0, dtype=np.float64)
        self._overlap = np.zeros(self.hop_size, dtype=np.float64)

    def reset(self):
        self._noise_profile = None
        self._frame_count = 0
        self._input_buffer = np.zeros(0, dtype=np.float64)
        self._overlap = np.zeros(self.hop_size, dtype=np.float64)

    def process(self, pcm_bytes):
        if not pcm_bytes:
            return pcm_bytes
        samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float64)
        self._input_buffer = np.concatenate([self._input_buffer, samples])
        output_chunks = []

        while len(self._input_buffer) >= self.window_size:
            frame = self._input_buffer[:self.window_size]
            self._input_buffer = self._input_buffer[self.hop_size:]
            windowed = frame * self._window
            spectrum = np.fft.rfft(windowed)
            power = np.abs(spectrum) ** 2

            if self._frame_count < self._noise_frames:
                alpha = 0.9
                if self._noise_profile is None:
                    self._noise_profile = power.copy()
                else:
                    self._noise_profile = alpha * self._noise_profile + (1 - alpha) * power
                self._frame_count += 1
                output_chunks.append(frame[:self.hop_size])
                continue

            wiener_gain = np.clip(1.0 - self.strength * (self._noise_profile / (power + 1e-8)), 0.0, 1.0)
            filtered_spectrum = spectrum * wiener_gain
            filtered_frame = np.fft.irfft(filtered_spectrum, n=self.window_size)
            out_segment = filtered_frame[:self.hop_size] + self._overlap
            self._overlap = filtered_frame[self.hop_size:self.hop_size * 2]
            output_chunks.append(out_segment)

        if not output_chunks:
            return b''
        output = np.concatenate(output_chunks)
        return np.clip(output, -32768, 32767).astype(np.int16).tobytes()
