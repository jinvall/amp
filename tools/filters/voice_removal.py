#!/usr/bin/env python3
"""Voice removal filter for AMP Silver tools.

Removes/attenuates the human voice band (80 Hz - 4000 Hz) while preserving
other frequencies. Useful for karaoke, isolating instruments, or reducing
speech in recordings.
"""

import threading
import numpy as np
from tools.filters.base import BaseFilter


class VoiceRemovalFilter(BaseFilter):
    """Remove voice band via spectral gating.

    Parameters:
        voice_low_hz: lower edge of voice band (default 80)
        voice_high_hz: upper edge of voice band (default 4000)
        attenuation_db: how much to cut the voice band (default 20)
        window_size: FFT window size (default 2048)
    """

    def __init__(self, sample_rate=44100, channels=1,
                 voice_low_hz=80.0, voice_high_hz=4000.0,
                 attenuation_db=20.0, window_size=2048):
        super().__init__(sample_rate, channels)
        self.voice_low_hz = float(voice_low_hz)
        self.voice_high_hz = float(voice_high_hz)
        self.attenuation_db = float(attenuation_db)
        self.window_size = int(window_size)
        self.hop_size = self.window_size // 2
        self._window = np.hanning(self.window_size)
        self._input_buffer = np.zeros(0, dtype=np.float64)
        self._overlap = np.zeros(self.hop_size, dtype=np.float64)
        self._lock = threading.Lock()

    def reset(self):
        with self._lock:
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
                freqs = np.fft.rfftfreq(self.window_size, d=1.0 / self.sample_rate)
                voice_band = (freqs >= self.voice_low_hz) & (freqs <= self.voice_high_hz)

                gain = np.ones_like(magnitude)
                gain[voice_band] = 10 ** (-self.attenuation_db / 20.0)

                filtered_spectrum = spectrum * gain
                filtered_frame = np.fft.irfft(filtered_spectrum, n=self.window_size)
                out_segment = filtered_frame[:self.hop_size] + self._overlap
                self._overlap = filtered_frame[self.hop_size:self.hop_size * 2]
                output_chunks.append(out_segment)

            if not output_chunks:
                return b''
            output = np.concatenate(output_chunks)
            return np.clip(output, -32768, 32767).astype(np.int16).tobytes()
