#!/usr/bin/env python3
"""Audio file writer for AMP Silver tools.

Writes processed audio as WAV or FLAC with metadata about the processing
chain applied. Saves to audio_segments/ or a separate processed directory.
"""

import os
import wave
import json
import numpy as np
from datetime import datetime


class AudioWriter:
    """Write processed audio files with processing metadata."""

    @staticmethod
    def write_wav(filepath: str, samples: np.ndarray, sample_rate: int):
        """Write samples as 16-bit PCM WAV.

        Args:
            filepath: Output path (should end in .wav)
            samples: Float64 array in range [-1, 1]
            sample_rate: Sample rate in Hz
        """
        clipped = np.clip(samples * 32768.0, -32768, 32767).astype(np.int16)
        with wave.open(filepath, 'wb') as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            w.writeframes(clipped.tobytes())

    @staticmethod
    def write_flac(filepath: str, samples: np.ndarray, sample_rate: int):
        """Write samples as FLAC.

        Args:
            filepath: Output path (should end in .flac)
            samples: Float64 array in range [-1, 1]
            sample_rate: Sample rate in Hz
        """
        try:
            import soundfile as sf
        except ImportError:
            raise ImportError("soundfile required for FLAC support: pip install soundfile")

        sf.write(filepath, samples, sample_rate, format='FLAC')

    @staticmethod
    def write_with_metadata(filepath: str, samples: np.ndarray, sample_rate: int,
                            metadata: dict, format: str = 'wav'):
        """Write audio file with processing metadata stored in a sidecar JSON.

        Args:
            filepath: Output path
            samples: Float64 array in range [-1, 1]
            sample_rate: Sample rate in Hz
            metadata: Dict of processing info (filters, params, timestamp)
            format: 'wav' or 'flac'
        """
        # Write audio
        if format == 'flac':
            AudioWriter.write_flac(filepath, samples, sample_rate)
        else:
            AudioWriter.write_wav(filepath, samples, sample_rate)

        # Write sidecar metadata
        meta_path = filepath + '.meta.json'
        metadata['written_at'] = datetime.now().isoformat()
        metadata['sample_rate'] = sample_rate
        metadata['format'] = format
        metadata['duration_sec'] = len(samples) / sample_rate
        with open(meta_path, 'w') as f:
            json.dump(metadata, f, indent=2, sort_keys=True)
