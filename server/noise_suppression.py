#!/usr/bin/env python3
"""Real-time noise suppression for the live audio monitor.

Two backends are available:

* ``RNNoiseSuppressor`` — neural noise suppression via RNNoise (Xiph).
  Requires 48000Hz input, so it resamples from the stream rate (44100Hz)
  to 48000Hz before processing and back to 44100Hz after. Uses the
  ``soxr`` library for high-quality resampling.

* ``SpectralGateSuppressor`` — FFT-based spectral gating. Computes a
  noise profile from the first few frames, then attenuates frequency
  bins that are below a threshold above the noise floor. No resampling
  needed, works at the stream rate.

Both implement the same interface: ``process(pcm_bytes) -> pcm_bytes``.
"""

import threading
import numpy as np


class NoiseSuppressor:
    """Base class for noise suppression backends."""

    def __init__(self, sample_rate: int, channels: int = 1):
        self.sample_rate = sample_rate
        self.channels = channels
        self._lock = threading.Lock()

    def process(self, pcm_bytes: bytes) -> bytes:
        """Process a chunk of PCM audio and return the denoised audio."""
        raise NotImplementedError

    def reset(self):
        """Reset the internal state (e.g., noise profile, resampler state)."""
        pass


class RNNoiseSuppressor(NoiseSuppressor):
    """Neural noise suppression via RNNoise.

    RNNoise operates at 48000Hz with a frame size of 480 samples (10ms).
    This class handles resampling from the stream rate to 48000Hz and back.
    """

    def __init__(self, sample_rate: int, channels: int = 1):
        super().__init__(sample_rate, channels)
        self._init_rnnoise()

    def _init_rnnoise(self):
        """Initialize RNNoise state and resamplers."""
        from pyrnnoise.rnnoise import create, FRAME_SIZE, SAMPLE_RATE

        self._state = create()
        self._frame_size = FRAME_SIZE  # 480 samples at 48000Hz
        self._rnnoise_rate = SAMPLE_RATE  # 48000Hz

        # Buffer for accumulating samples into RNNoise frames
        self._input_buffer = np.zeros(0, dtype=np.int16)
        self._output_buffer = np.zeros(0, dtype=np.int16)

    def _resample(self, samples: np.ndarray, in_rate: int, out_rate: int) -> np.ndarray:
        """Resample using soxr module-level function."""
        import soxr
        if in_rate == out_rate:
            return samples
        return soxr.resample(samples, in_rate, out_rate, quality='HQ')

    def process(self, pcm_bytes: bytes) -> bytes:
        """Process PCM bytes through RNNoise."""
        from pyrnnoise.rnnoise import process_frame

        if not pcm_bytes:
            return pcm_bytes

        with self._lock:
            # Convert bytes to int16 array
            samples = np.frombuffer(pcm_bytes, dtype=np.int16).copy()

            # Resample to 48000Hz if needed
            if self.sample_rate != self._rnnoise_rate:
                samples = self._resample(samples, self.sample_rate, self._rnnoise_rate)

            # Accumulate into buffer
            self._input_buffer = np.concatenate([self._input_buffer, samples])

            # Process complete frames
            output_frames = []
            while len(self._input_buffer) >= self._frame_size:
                frame = self._input_buffer[:self._frame_size]
                self._input_buffer = self._input_buffer[self._frame_size:]

                # RNNoise expects float64 input in range [-1, 1]
                frame_float = frame.astype(np.float64) / 32768.0
                denoised_float, _ = process_frame(self._state, frame_float)

                # Convert back to int16
                denoised = np.clip(denoised_float * 32768.0, -32768, 32767).astype(np.int16)
                output_frames.append(denoised)

            if not output_frames:
                return b''

            denoised_samples = np.concatenate(output_frames)

            # Resample back to stream rate if needed
            if self.sample_rate != self._rnnoise_rate:
                denoised_samples = self._resample(denoised_samples, self._rnnoise_rate, self.sample_rate)

            return denoised_samples.tobytes()

    def reset(self):
        """Reset RNNoise state and resamplers."""
        from pyrnnoise.rnnoise import destroy

        with self._lock:
            if self._state is not None:
                destroy(self._state)
            self._init_rnnoise()


class SpectralGateSuppressor(NoiseSuppressor):
    """FFT-based spectral gating noise suppression.

    This is intentionally a gentle monitor-side cleaner: it keeps human speech
    and whisper energy intact while slightly reducing steady ambient noise.
    """

    def __init__(self, sample_rate: int, channels: int = 1,
                 noise_frames: int = 30, threshold_db: float = 1.5,
                 attenuation_db: float = 9.0, window_size: int = 2048):
        super().__init__(sample_rate, channels)
        self.noise_frames = noise_frames
        self.threshold_db = threshold_db
        self.attenuation_db = attenuation_db
        self.window_size = window_size
        self._noise_profile = None
        self._frame_count = 0
        self._hop_size = window_size // 4
        self._window = np.hanning(window_size)
        self._input_buffer = np.zeros(0, dtype=np.float64)
        self._overlap = np.zeros(self._hop_size, dtype=np.float64)

    def reset(self):
        self._noise_profile = None
        self._frame_count = 0
        self._input_buffer = np.zeros(0, dtype=np.float64)
        self._overlap = np.zeros(self._hop_size, dtype=np.float64)

    def process(self, pcm_bytes: bytes) -> bytes:
        if not pcm_bytes:
            return pcm_bytes

        with self._lock:
            samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float64)
            self._input_buffer = np.concatenate([self._input_buffer, samples])
            output_chunks = []

            while len(self._input_buffer) >= self.window_size:
                frame = self._input_buffer[:self.window_size]
                self._input_buffer = self._input_buffer[self._hop_size:]
                windowed = frame * self._window
                spectrum = np.fft.rfft(windowed)
                magnitude = np.abs(spectrum)
                phase = np.angle(spectrum)

                if self._frame_count < self.noise_frames:
                    if self._noise_profile is None:
                        self._noise_profile = magnitude.copy()
                    else:
                        alpha = 0.9
                        self._noise_profile = alpha * self._noise_profile + (1 - alpha) * magnitude
                    self._frame_count += 1
                    output_chunks.append(frame[:self._hop_size])
                    continue

                noise_floor = self._noise_profile * (10 ** (self.threshold_db / 20.0))
                ratio = magnitude / (noise_floor + 1e-8)
                soft_gain = np.clip((ratio - 1.0) / (1.5 + self.threshold_db), 0.0, 1.0)
                soft_gain = np.where(ratio > 1.0, 1.0 - (1.0 - soft_gain) * 0.35, 0.55)
                soft_gain = np.clip(soft_gain, 0.0, 1.0)
                gate_floor = 10 ** (-self.attenuation_db / 20.0)
                # Smooth transition — raise floor to avoid choppy gating artifacts
                mask = np.maximum(soft_gain, gate_floor)
                # One-pole smoother on the mask to prevent zipper noise
                if hasattr(self, '_prev_mask'):
                    mask = 0.7 * self._prev_mask + 0.3 * mask
                self._prev_mask = mask.copy()

                gated_magnitude = magnitude * mask
                gated_spectrum = gated_magnitude * np.exp(1j * phase)
                gated_frame = np.fft.irfft(gated_spectrum, n=self.window_size)
                out_segment = gated_frame[:self._hop_size] + self._overlap
                self._overlap = gated_frame[self._hop_size:self._hop_size * 2]
                output_chunks.append(out_segment)

            if not output_chunks:
                return b''

            output = np.concatenate(output_chunks)
            output = np.clip(output, -32768, 32767).astype(np.int16)
            return output.tobytes()


class VoiceIsolationSuppressor(NoiseSuppressor):
    """Voice-targeted monitor filter that isolates or removes the human voice band.

    This is intentionally monitor-only and does not touch the core audio pipeline,
    storage, or existing app logic. It only changes the real-time monitoring output.
    """

    def __init__(self, sample_rate: int, channels: int = 1,
                 mode: str = 'voice_isolate',
                 voice_low_hz: float = 80.0, voice_high_hz: float = 4000.0,
                 attenuation_db: float = 20.0, frame_size: int = 2048):
        super().__init__(sample_rate, channels)
        self.mode = mode if mode in {'voice_isolate', 'voice_remove'} else 'voice_isolate'
        self.voice_low_hz = float(voice_low_hz)
        self.voice_high_hz = float(voice_high_hz)
        self.attenuation_db = float(attenuation_db)
        self.frame_size = int(frame_size)
        self.hop_size = self.frame_size // 2
        self._window = np.hanning(self.frame_size)
        self._input_buffer = np.zeros(0, dtype=np.float64)
        self._overlap = np.zeros(self.hop_size, dtype=np.float64)

    def reset(self):
        self._input_buffer = np.zeros(0, dtype=np.float64)
        self._overlap = np.zeros(self.hop_size, dtype=np.float64)

    def process(self, pcm_bytes: bytes) -> bytes:
        if not pcm_bytes:
            return pcm_bytes

        with self._lock:
            samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float64)
            self._input_buffer = np.concatenate([self._input_buffer, samples])
            output_chunks = []

            while len(self._input_buffer) >= self.frame_size:
                frame = self._input_buffer[:self.frame_size]
                self._input_buffer = self._input_buffer[self.hop_size:]

                windowed = frame * self._window
                spectrum = np.fft.rfft(windowed)
                magnitude = np.abs(spectrum)
                phase = np.angle(spectrum)
                freqs = np.fft.rfftfreq(self.frame_size, d=1.0 / self.sample_rate)
                voice_band = (freqs >= self.voice_low_hz) & (freqs <= self.voice_high_hz)

                noise_floor = float(np.median(magnitude[~voice_band])) if np.any(~voice_band) else float(np.median(magnitude))
                noise_floor = max(noise_floor, 1e-6)
                voice_ratio = np.zeros_like(magnitude)
                if np.any(voice_band):
                    voice_ratio[voice_band] = magnitude[voice_band] / (noise_floor + 1e-8)

                if self.mode == 'voice_remove':
                    voice_gain = np.ones_like(magnitude)
                    voice_gain[voice_band] = np.clip(10 ** (-self.attenuation_db / 20.0), 0.03, 0.4)
                    voice_gain[voice_band] *= np.clip((voice_ratio[voice_band] / 4.0), 0.3, 1.0)
                    voice_gain[~voice_band] = 1.0
                else:
                    voice_gain = np.ones_like(magnitude)
                    voice_gain[voice_band] = np.clip(
                        np.maximum(0.25, 1.0 - (voice_ratio[voice_band] - 1.0) * 0.18),
                        0.25,
                        1.0,
                    )
                    voice_gain[~voice_band] = 1.0

                # FIX: Apply gain to magnitude, reconstruct spectrum, inverse FFT
                gated_magnitude = magnitude * voice_gain
                gated_spectrum = gated_magnitude * np.exp(1j * phase)
                gated_frame = np.fft.irfft(gated_spectrum, n=self.frame_size)
                out_segment = gated_frame[:self.hop_size] + self._overlap
                self._overlap = gated_frame[self.hop_size:self.hop_size * 2]
                output_chunks.append(out_segment)

            if not output_chunks:
                return b''

            output = np.concatenate(output_chunks)
            output = np.clip(output, -32768, 32767).astype(np.int16)
            return output.tobytes()


def create_suppressor(method: str, sample_rate: int, channels: int = 1) -> NoiseSuppressor:
    """Factory function to create a noise suppressor by method name."""
    method_name = str(method or '').strip().lower()
    if method_name in ('ambient', 'spectral'):
        return SpectralGateSuppressor(sample_rate, channels)
    if method_name == 'rnnoise':
        return RNNoiseSuppressor(sample_rate, channels)
    if method_name == 'voice_isolate':
        return VoiceIsolationSuppressor(sample_rate, channels, mode='voice_isolate')
    if method_name == 'voice_remove':
        return VoiceIsolationSuppressor(sample_rate, channels, mode='voice_remove')
    raise ValueError(f"Unknown noise suppression method: {method!r}")
