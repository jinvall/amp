"""
Multi-band parametric equalizer for AMP.
10-band graphic EQ with adjustable gain and Q factor.
Uses second-order biquad filters (shelf + peak) as IIR.
"""

from __future__ import annotations
import numpy as np
from typing import Optional, Dict, List, Tuple
import logging

logger = logging.getLogger(__name__)

ISO_BANDS = [32, 64, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]
DEFAULT_Q = [0.7, 0.7, 1.0, 1.0, 1.5, 1.5, 1.5, 1.0, 1.0, 0.7]


def _biquad_coeffs(kind, freq, gain_db, q, sample_rate):
    w0 = 2.0 * np.pi * freq / sample_rate
    cos_w0, sin_w0 = np.cos(w0), np.sin(w0)
    A = 10.0 ** (gain_db / 40.0)
    alpha = sin_w0 / (2.0 * q)
    if kind == "lowshelf":
        s = np.sqrt(A)
        b = [A*((A+1)-(A-1)*cos_w0+2*s*alpha), 2*A*((A-1)-(A+1)*cos_w0), A*((A+1)-(A-1)*cos_w0-2*s*alpha)]
        a = [(A+1)+(A-1)*cos_w0+2*s*alpha, -2*((A-1)+(A+1)*cos_w0), (A+1)+(A-1)*cos_w0-2*s*alpha]
    elif kind == "highshelf":
        s = np.sqrt(A)
        b = [A*((A+1)+(A-1)*cos_w0+2*s*alpha), -2*A*((A-1)+(A+1)*cos_w0), A*((A+1)+(A-1)*cos_w0-2*s*alpha)]
        a = [(A+1)-(A-1)*cos_w0+2*s*alpha, 2*((A-1)-(A+1)*cos_w0), (A+1)-(A-1)*cos_w0-2*s*alpha]
    else:
        b = [1+alpha*A, -2*cos_w0, 1-alpha*A]
        a = [1+alpha/A, -2*cos_w0, 1-alpha/A]
    return np.array(b)/a[0], np.array(a)/a[0]


class MultiBandEqualizer:
    def __init__(self, sample_rate=44100, bands=None, q_values=None, gains_db=None):
        self.sample_rate = sample_rate
        self.bands = bands or ISO_BANDS[:]
        self.q_values = q_values or DEFAULT_Q[:]
        self.gains_db = gains_db or [0.0]*len(self.bands)
        self.enabled = True
        self._states, self._coeffs = {}, []
        self._compute_coeffs()

    def _compute_coeffs(self):
        self._coeffs = []
        for i, (freq, gain, q) in enumerate(zip(self.bands, self.gains_db, self.q_values)):
            kind = "lowshelf" if i == 0 else "highshelf" if i == len(self.bands)-1 else "peak"
            self._coeffs.append(_biquad_coeffs(kind, freq, gain, q, self.sample_rate))

    def set_gain(self, idx, gain_db):
        if 0 <= idx < len(self.bands):
            self.gains_db[idx] = np.clip(gain_db, -12.0, 12.0)
            self._compute_coeffs()

    def set_all_gains(self, gains_db):
        for i, g in enumerate(gains_db):
            if i < len(self.bands):
                self.gains_db[i] = np.clip(g, -12.0, 12.0)
        self._compute_coeffs()

    def reset(self):
        self.gains_db = [0.0]*len(self.bands)
        self._states.clear()
        self._compute_coeffs()

    def process(self, audio):
        if not self.enabled:
            return audio
        if audio.ndim == 1:
            return self._process_channel(audio, 0)
        out = np.empty_like(audio)
        for ch in range(audio.shape[0]):
            out[ch] = self._process_channel(audio[ch], ch)
        return out

    def _process_channel(self, x, channel):
        if channel not in self._states:
            self._states[channel] = [{"x1":0.0,"x2":0.0,"y1":0.0,"y2":0.0} for _ in self._coeffs]
        y = x.astype(np.float64)
        states = self._states[channel]
        for i, (b, a) in enumerate(self._coeffs):
            s = states[i]
            out = np.empty_like(y)
            for n in range(len(y)):
                x0 = y[n]
                y0 = b[0]*x0 + b[1]*s["x1"] + b[2]*s["x2"] - a[1]*s["y1"] - a[2]*s["y2"]
                s["x2"], s["x1"] = s["x1"], x0
                s["y2"], s["y1"] = s["y1"], y0
                out[n] = y0
            y = out
        return y.astype(x.dtype)

    def get_frequency_response(self, num_points=512):
        freqs = np.logspace(1, np.log10(self.sample_rate/2), num_points)
        mag_db = np.zeros(num_points)
        for i, f in enumerate(freqs):
            total = 1.0
            for gain, q, center in zip(self.gains_db, self.q_values, self.bands):
                if abs(gain) < 0.01: continue
                band_idx = self.bands.index(center)
                kind = "lowshelf" if band_idx == 0 else "highshelf" if band_idx == len(self.bands)-1 else "peak"
                b, a = _biquad_coeffs(kind, center, gain, q, self.sample_rate)
                w = 2.0*np.pi*f/self.sample_rate
                num = b[0] + b[1]*np.exp(-1j*w) + b[2]*np.exp(-2j*w)
                den = a[0] + a[1]*np.exp(-1j*w) + a[2]*np.exp(-2j*w)
                total *= np.abs(num/den)
            mag_db[i] = 20.0*np.log10(max(total, 1e-10))
        return freqs, mag_db

    def to_dict(self):
        return {"bands": self.bands, "gains_db": self.gains_db, "q_values": self.q_values, "enabled": self.enabled}

    @classmethod
    def from_dict(cls, data, sample_rate=44100):
        eq = cls(sample_rate=sample_rate, bands=data.get("bands"), q_values=data.get("q_values"), gains_db=data.get("gains_db"))
        eq.enabled = data.get("enabled", True)
        return eq
