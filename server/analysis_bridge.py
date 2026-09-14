#!/usr/bin/env python3
"""Server-side bridge for audio analysis tools.

Wraps tools/analysis modules with WAV reading and result formatting.
Used by amp.py HTTP endpoints.
"""

import os
import sys
import json
import tempfile
import wave
import threading
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


def _read_wav_mono(path):
    """Read WAV file, return (samples_float64, sample_rate)."""
    with wave.open(path, 'rb') as w:
        sr = w.getframerate()
        nch = w.getnchannels()
        raw = w.readframes(w.getnframes())
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float64)
    if nch > 1:
        samples = samples.reshape(-1, nch).mean(axis=1)
    return samples, sr


def _write_wav_mono(path, samples, sr):
    """Write mono float64 samples as 16-bit WAV."""
    clipped = np.clip(samples * 32768.0, -32768, 32767).astype(np.int16)
    with wave.open(path, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(clipped.tobytes())


class AnalysisBridge:
    """Runs analysis on demand using loaded profile settings."""

    def __init__(self, profile_manager=None):
        self._pm = profile_manager
        self._lock = threading.Lock()

    def _get_profile(self):
        if self._pm:
            return self._pm.get_active()
        return {}

    def transcribe(self, audio_path):
        """Transcribe a WAV/FLAC file. Returns dict."""
        try:
            from tools.analysis.audio_transcribe import transcribe_audio
            p = self._get_profile()
            tc = p.get('analysis', {}).get('transcription', {})
            backend = tc.get('backend', 'none')
            if backend == 'none':
                return {'error': 'Transcription disabled in active profile. Enable faster-whisper or whisper.'}
            result = transcribe_audio(
                audio_path,
                model_size=tc.get('model_size', 'base'),
                device=tc.get('device', 'cpu'),
                language=tc.get('language', None),
            )
            return result.to_dict()
        except ImportError as e:
            return {'error': f'Transcription backend not available: {e}'}
        except Exception as e:
            return {'error': f'Transcription failed: {e}'}

    def detect_pitch(self, audio_path):
        """Detect pitch in a file. Returns list of frame results."""
        try:
            from tools.analysis.pitch_detection import PitchDetector
            p = self._get_profile()
            pc = p.get('analysis', {}).get('pitch', {})
            algo = pc.get('algorithm', 'yin')
            if algo == 'none':
                return {'error': 'Pitch detection disabled in active profile.'}

            # Read audio
            if audio_path.lower().endswith('.flac'):
                import soundfile as sf
                data, sr = sr_raw = sf.read(audio_path, dtype='float64')
                if data.ndim > 1:
                    data = data.mean(axis=1)
                samples_int16 = np.clip(data * 32768, -32768, 32767).astype(np.int16)
            else:
                samples_int16, sr = _read_wav_mono(audio_path)
                samples_int16 = samples_int16.astype(np.int16)

            detector = PitchDetector(
                sample_rate=sr,
                min_freq=pc.get('min_freq', 60.0),
                max_freq=pc.get('max_freq', 4000.0),
            )
            pcm_bytes = samples_int16.tobytes()
            chunk_size = int(sr * 0.1) * 2  # 100ms chunks * 2 bytes
            results = []
            for offset in range(0, len(pcm_bytes), chunk_size):
                chunk = pcm_bytes[offset:offset + chunk_size]
                r = detector.detect(chunk)
                r['time_sec'] = round(offset / (sr * 2), 3)
                results.append(r)
            return {'sample_rate': sr, 'frames': results, 'algorithm': algo}
        except ImportError as e:
            return {'error': f'Pitch module not available: {e}'}
        except Exception as e:
            return {'error': f'Pitch detection failed: {e}'}

    def detect_chords(self, audio_path):
        """Detect chords from chroma analysis of a file."""
        try:
            from tools.analysis.chord_recognition import ChordRecognizer
            from tools.analysis.pitch_detection import freq_to_midi
            p = self._get_profile()
            cc = p.get('analysis', {}).get('chords', {})
            if cc.get('profiles') == 'none':
                return {'error': 'Chord recognition disabled in active profile.'}

            # Read audio and compute chroma
            if audio_path.lower().endswith('.flac'):
                import soundfile as sf
                data, sr = sf.read(audio_path, dtype='float64')
                if data.ndim > 1:
                    data = data.mean(axis=1)
            else:
                with wave.open(audio_path, 'rb') as w:
                    sr = w.getframerate()
                    nch = w.getnchannels()
                    raw = w.readframes(w.getnframes())
                data = np.frombuffer(raw, dtype=np.int16).astype(np.float64)
                if nch > 1:
                    data = data.reshape(-1, nch).mean(axis=1)

            recognizer = ChordRecognizer()
            # Compute chromagram in windows and recognize per-window
            window = int(sr * 2.0)  # 2-second windows
            hop = int(sr * 0.5)
            results = []
            for start in range(0, len(data) - window, hop):
                chunk = data[start:start + window]
                spectrum = np.fft.rfft(chunk)
                mag = np.abs(spectrum)
                freqs = np.fft.rfftfreq(len(chunk), 1.0/sr)
                chroma = np.zeros(12)
                for i, f in enumerate(freqs):
                    if f > 0:
                        midi = freq_to_midi(f)
                        if 0 <= midi <= 127:
                            chroma[midi % 12] += mag[i]
                if chroma.sum() > 0:
                    chroma /= chroma.sum()
                r = recognizer.recognize_chord(chroma)
                r['time_sec'] = round(start / sr, 2)
                results.append(r)
            return {'sample_rate': sr, 'windows': results}
        except ImportError as e:
            return {'error': f'Chord module not available: {e}'}
        except Exception as e:
            return {'error': f'Chord detection failed: {e}'}

    def apply_filters(self, source_path, filter_list, params):
        """Apply a chain of filters to a file. Returns output path or error."""
        try:
            from tools.filters.chain import FilterChain
            from tools.filters.noise_cancellation import NoiseCancellationFilter
            from tools.filters.spectral_difference import SpectralDifferenceFilter
            from tools.filters.voice_removal import VoiceRemovalFilter
            from tools.filters.voice_isolation import VoiceIsolationFilter
            from tools.filters.ambient_removal import AmbientRemovalFilter
            from tools.filters.compressor import CompressorFilter
            from tools.filters.agc import AGCFilter
            from tools.filters.wind_noise import WindNoiseFilter
            from tools.filters.deesser import DeEsserFilter
            from tools.io.reader import AudioReader
            from tools.io.writer import AudioWriter

            FILTER_MAP = {
                'noise_cancellation': NoiseCancellationFilter,
                'spectral_difference': SpectralDifferenceFilter,
                'voice_removal': VoiceRemovalFilter,
                'voice_isolation': VoiceIsolationFilter,
                'ambient_removal': AmbientRemovalFilter,
                'compressor': CompressorFilter,
                'agc': AGCFilter,
                'wind_noise': WindNoiseFilter,
                'deesser': DeEsserFilter,
            }

            samples, sr = AudioReader.read(source_path)
            # Convert int16 PCM → float for filters (filters take bytes)
            # The filter chain works on bytes, so we need int16 bytes
            samples_i16 = np.clip(samples, -32768, 32767).astype(np.int16)
            pcm_bytes = samples_i16.tobytes()

            chain = FilterChain()
            for fname in filter_list:
                cls = FILTER_MAP.get(fname)
                if cls is None:
                    continue
                fparams = params.get(fname, {})
                chain.add(cls(sample_rate=sr, **fparams))

            processed = chain.process(pcm_bytes)
            out_samples = np.frombuffer(processed, dtype=np.int16).astype(np.float64) / 32768.0

            out_dir = os.path.join(os.path.dirname(source_path), 'processed')
            os.makedirs(out_dir, exist_ok=True)
            base = os.path.splitext(os.path.basename(source_path))[0]
            out_path = os.path.join(out_dir, f"{base}_processed.wav")
            AudioWriter.write_with_metadata(out_path, out_samples, sr, {
                'filters': filter_list,
                'params': params,
            })
            return {'output_path': out_path, 'duration': round(len(out_samples) / sr, 2)}
        except Exception as e:
            return {'error': f'Filter processing failed: {e}'}


# Singleton
_bridge = None
_bridge_lock = threading.Lock()

def get_bridge(profile_manager=None):
    global _bridge
    with _bridge_lock:
        if _bridge is None:
            _bridge = AnalysisBridge(profile_manager)
        return _bridge
