#!/usr/bin/env python3
"""Feature extraction and filter parameter registry for AMP Silver tools.

Two parts:
A) Filter parameter registry — all configurable parameters for each filter
   with valid ranges, defaults, and descriptions.
B) Audio feature extraction — compute features from PCM for analysis.
"""

import threading
import numpy as np
from tools.filters.base import BaseFilter


FILTER_PARAM_REGISTRY = {
    'noise_cancellation': {
        'strength': {'type': 'float', 'min': 0.0, 'max': 1.0, 'default': 0.5, 'label': 'Strength'},
        'noise_floor_db': {'type': 'float', 'min': 0.0, 'max': 30.0, 'default': 1.5, 'label': 'Noise Floor (dB)'},
        'attenuation_db': {'type': 'float', 'min': 0.0, 'max': 40.0, 'default': 9.0, 'label': 'Attenuation (dB)'},
        'method': {'type': 'choice', 'options': ['spectral_gate', 'wiener'], 'default': 'spectral_gate', 'label': 'Method'},
    },
    'spectral_difference': {
        'noise_frames': {'type': 'int', 'min': 5, 'max': 100, 'default': 30, 'label': 'Noise Frames'},
        'reduction_amount': {'type': 'float', 'min': 0.0, 'max': 1.0, 'default': 0.8, 'label': 'Reduction'},
        'smoothing': {'type': 'float', 'min': 0.0, 'max': 0.95, 'default': 0.5, 'label': 'Smoothing'},
    },
    'voice_removal': {
        'voice_low_hz': {'type': 'int', 'min': 20, 'max': 500, 'default': 80, 'label': 'Low Freq (Hz)'},
        'voice_high_hz': {'type': 'int', 'min': 1000, 'max': 8000, 'default': 4000, 'label': 'High Freq (Hz)'},
        'attenuation_db': {'type': 'float', 'min': 0.0, 'max': 40.0, 'default': 20.0, 'label': 'Attenuation (dB)'},
    },
    'voice_isolation': {
        'voice_low_hz': {'type': 'int', 'min': 20, 'max': 500, 'default': 80, 'label': 'Low Freq (Hz)'},
        'voice_high_hz': {'type': 'int', 'min': 1000, 'max': 8000, 'default': 4000, 'label': 'High Freq (Hz)'},
        'preserve_db': {'type': 'float', 'min': 0.0, 'max': 10.0, 'default': 0.0, 'label': 'Preserve (dB)'},
        'attenuate_db': {'type': 'float', 'min': 0.0, 'max': 40.0, 'default': 18.0, 'label': 'Attenuate (dB)'},
    },
    'ambient_removal': {
        'adaptation_rate': {'type': 'float', 'min': 0.01, 'max': 0.5, 'default': 0.05, 'label': 'Adaptation Rate'},
        'reduction_db': {'type': 'float', 'min': 0.0, 'max': 30.0, 'default': 12.0, 'label': 'Reduction (dB)'},
        'sensitivity': {'type': 'float', 'min': 0.5, 'max': 5.0, 'default': 1.5, 'label': 'Sensitivity'},
    },
    'compressor': {
        'threshold_db': {'type': 'float', 'min': -60.0, 'max': 0.0, 'default': -20.0, 'label': 'Threshold (dB)'},
        'ratio': {'type': 'float', 'min': 1.0, 'max': 20.0, 'default': 4.0, 'label': 'Ratio'},
        'attack_ms': {'type': 'float', 'min': 0.1, 'max': 100.0, 'default': 5.0, 'label': 'Attack (ms)'},
        'release_ms': {'type': 'float', 'min': 10.0, 'max': 1000.0, 'default': 50.0, 'label': 'Release (ms)'},
        'makeup_gain_db': {'type': 'float', 'min': 0.0, 'max': 30.0, 'default': 6.0, 'label': 'Makeup Gain (dB)'},
    },
    'agc': {
        'target_rms': {'type': 'float', 'min': 0.01, 'max': 0.5, 'default': 0.1, 'label': 'Target RMS'},
        'attack_ms': {'type': 'float', 'min': 10.0, 'max': 1000.0, 'default': 200.0, 'label': 'Attack (ms)'},
        'release_ms': {'type': 'float', 'min': 100.0, 'max': 5000.0, 'default': 1000.0, 'label': 'Release (ms)'},
        'max_gain_db': {'type': 'float', 'min': 0.0, 'max': 60.0, 'default': 30.0, 'label': 'Max Gain (dB)'},
        'min_gain_db': {'type': 'float', 'min': -40.0, 'max': 0.0, 'default': -20.0, 'label': 'Min Gain (dB)'},
    },
    'wind_noise': {
        'cutoff_hz': {'type': 'float', 'min': 40.0, 'max': 500.0, 'default': 80.0, 'label': 'Cutoff (Hz)'},
        'strength': {'type': 'float', 'min': 0.0, 'max': 1.0, 'default': 0.7, 'label': 'Strength'},
    },
    'deesser': {
        'freq_low_hz': {'type': 'float', 'min': 2000.0, 'max': 8000.0, 'default': 4000.0, 'label': 'Low Freq (Hz)'},
        'freq_high_hz': {'type': 'float', 'min': 5000.0, 'max': 16000.0, 'default': 10000.0, 'label': 'High Freq (Hz)'},
        'threshold_db': {'type': 'float', 'min': -60.0, 'max': 0.0, 'default': -30.0, 'label': 'Threshold (dB)'},
        'reduction_db': {'type': 'float', 'min': 0.0, 'max': 30.0, 'default': 12.0, 'label': 'Reduction (dB)'},
    },
}


def get_filter_params(filter_name):
    return FILTER_PARAM_REGISTRY.get(filter_name, {})


def get_all_filter_names():
    return list(FILTER_PARAM_REGISTRY.keys())


def validate_param(filter_name, param_name, value):
    params = FILTER_PARAM_REGISTRY.get(filter_name, {})
    if param_name not in params:
        return False, f"Unknown parameter: {param_name}"
    d = params[param_name]
    if d['type'] in ('float', 'int'):
        if value < d['min'] or value > d['max']:
            return False, f"Value {value} out of range [{d['min']}, {d['max']}]"
    elif d['type'] == 'choice':
        if value not in d['options']:
            return False, f"Invalid choice: {value}"
    return True, None


def clamp_param(filter_name, param_name, value):
    params = FILTER_PARAM_REGISTRY.get(filter_name, {})
    if param_name not in params:
        return value
    d = params[param_name]
    if d['type'] in ('float', 'int'):
        return max(d['min'], min(d['max'], value))
    return value


class FeatureOptionsFilter(BaseFilter):
    """Feature extraction filter.

    Extracts audio features from PCM chunks. Does not modify the audio —
    passes through unchanged while computing features for analysis/display.
    """

    def __init__(self, sample_rate=44100, channels=1):
        super().__init__(sample_rate, channels)
        self.enabled = True
        self.features = {}
        self._prev_spectrum = None
        self._lock = threading.Lock()

    def reset(self):
        with self._lock:
            self.features = {}
            self._prev_spectrum = None

    def process(self, pcm_bytes):
        if not pcm_bytes:
            return pcm_bytes
        with self._lock:
            samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float64)
            if samples.size == 0:
                return pcm_bytes
            self.features = extract_features(samples, self.sample_rate, self._prev_spectrum)
            self._prev_spectrum = self.features.get('_last_spectrum')
            return pcm_bytes


def extract_features(samples, sample_rate=44100, prev_spectrum=None):
    """Extract audio features from a chunk of audio."""
    if samples.size == 0:
        return {}

    rms = float(np.sqrt(np.mean(samples ** 2))) / 32768.0
    peak = float(np.max(np.abs(samples))) / 32768.0
    zero_crossings = np.sum(np.abs(np.diff(np.sign(samples)))) / 2
    zcr = float(zero_crossings) / len(samples)

    spectrum = np.fft.rfft(samples)
    magnitude = np.abs(spectrum)
    freqs = np.fft.rfftfreq(len(samples), d=1.0 / sample_rate)

    total_energy = np.sum(magnitude ** 2)
    if total_energy > 0:
        spectral_centroid = float(np.sum(freqs * (magnitude ** 2)) / total_energy)
        cumulative = np.cumsum(magnitude ** 2)
        rolloff_idx = np.searchsorted(cumulative, 0.85 * total_energy)
        spectral_rolloff = float(freqs[min(rolloff_idx, len(freqs) - 1)])
    else:
        spectral_centroid = 0.0
        spectral_rolloff = 0.0

    # Spectral flux (change in spectrum vs previous frame)
    spectral_flux = 0.0
    if prev_spectrum is not None and prev_spectrum.shape == magnitude.shape:
        diff = magnitude - prev_spectrum
        spectral_flux = float(np.sum(np.maximum(diff, 0) ** 2))

    return {
        'rms': rms,
        'peak': peak,
        'zcr': zcr,
        'spectral_centroid': spectral_centroid,
        'spectral_rolloff': spectral_rolloff,
        'spectral_flux': spectral_flux,
        '_last_spectrum': magnitude,
    }


def extract_mfcc(samples, sample_rate=44100, n_mfcc=13):
    """Extract MFCCs. Requires librosa. Returns None if not available."""
    try:
        import librosa
        y = samples / 32768.0 if samples.dtype == np.float64 else samples.astype(np.float64)
        mfccs = librosa.feature.mfcc(y=y, sr=sample_rate, n_mfcc=n_mfcc)
        return mfccs.tolist()
    except ImportError:
        return None
