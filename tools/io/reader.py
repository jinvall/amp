#!/usr/bin/env python3
"""Audio file reader for AMP Silver tools.

Reads WAV and FLAC files from audio_segments/ and converts them to
mono float64 at the target sample rate. Used by batch processing.
"""

import os
import wave
import numpy as np


class AudioReader:
    """Read audio files and convert to mono float64.

    Supports WAV and FLAC formats. Resamples via linear interpolation
    (no scipy dependency).
    """

    SUPPORTED_FORMATS = ('.wav', '.flac')

    @staticmethod
    def read(filepath: str, target_sr: int = 44100) -> tuple:
        """Read an audio file and return (samples_float64, sample_rate).

        Returns mono audio at target_sr. If the file is stereo, it's
        converted to mono by averaging channels.

        Args:
            filepath: Path to WAV or FLAC file
            target_sr: Target sample rate (default 44100)

        Returns:
            Tuple of (samples as float64 numpy array, sample_rate)

        Raises:
            FileNotFoundError: if file doesn't exist
            ValueError: if format is unsupported or file is corrupt
        """
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Audio file not found: {filepath}")

        ext = os.path.splitext(filepath)[1].lower()
        if ext not in AudioReader.SUPPORTED_FORMATS:
            raise ValueError(f"Unsupported format: {ext}")

        if ext == '.flac':
            return AudioReader._read_flac(filepath, target_sr)
        else:
            return AudioReader._read_wav(filepath, target_sr)

    @staticmethod
    def _read_wav(filepath: str, target_sr: int) -> tuple:
        """Read a WAV file."""
        with wave.open(filepath, 'rb') as w:
            sr = w.getframerate()
            n = w.getnframes()
            nch = w.getnchannels()
            raw = w.readframes(n)

        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float64)

        # Convert to mono if stereo
        if nch > 1:
            samples = samples.reshape(-1, nch).mean(axis=1)

        # Resample if needed
        if sr != target_sr and n > 0:
            samples = AudioReader._resample(samples, sr, target_sr)

        return samples, target_sr

    @staticmethod
    def _read_flac(filepath: str, target_sr: int) -> tuple:
        """Read a FLAC file using soundfile."""
        try:
            import soundfile as sf
        except ImportError:
            raise ImportError("soundfile required for FLAC support: pip install soundfile")

        data, sr = sf.read(filepath, dtype='float64')

        # Convert to mono if stereo
        if data.ndim > 1:
            data = data.mean(axis=1)

        # Resample if needed
        if sr != target_sr:
            data = AudioReader._resample(data, sr, target_sr)

        return data, target_sr

    @staticmethod
    def _resample(samples: np.ndarray, in_rate: int, out_rate: int) -> np.ndarray:
        """Resample using linear interpolation (no scipy)."""
        if in_rate == out_rate:
            return samples
        n_out = int(round(len(samples) * out_rate / in_rate))
        x_old = np.linspace(0, 1, len(samples), endpoint=False)
        x_new = np.linspace(0, 1, n_out, endpoint=False)
        return np.interp(x_new, x_old, samples)

    @staticmethod
    def duration(filepath: str) -> float:
        """Get duration of an audio file in seconds."""
        ext = os.path.splitext(filepath)[1].lower()
        try:
            if ext == '.flac':
                import soundfile as sf
                info = sf.info(filepath)
                return info.duration
            else:
                with wave.open(filepath, 'rb') as w:
                    return w.getnframes() / float(w.getframerate())
        except Exception:
            return 0.0
