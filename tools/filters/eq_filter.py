"""BaseFilter-compatible wrapper for MultiBandEqualizer."""
from __future__ import annotations
import numpy as np
from typing import Optional, List, Dict
import logging
from tools.filters.base import BaseFilter
from tools.filters.equalizer import MultiBandEqualizer, ISO_BANDS, DEFAULT_Q

logger = logging.getLogger(__name__)


class EqualizerFilter(BaseFilter):
    def __init__(self, sample_rate=44100, channels=1, gains_db=None, q_values=None):
        super().__init__(sample_rate=sample_rate, channels=channels)
        self._eq = MultiBandEqualizer(
            sample_rate=sample_rate,
            bands=ISO_BANDS[:],
            q_values=q_values or DEFAULT_Q[:],
            gains_db=gains_db or [0.0]*len(ISO_BANDS),
        )
        self.bands = ISO_BANDS[:]
        self.gains_db = self._eq.gains_db
        self.q_values = self._eq.q_values

    def process(self, pcm_bytes):
        if not pcm_bytes or not self.enabled:
            return pcm_bytes
        samples = self._pcm_to_float(pcm_bytes)
        if self.channels == 2:
            if len(samples) % 2 != 0: samples = samples[:-1]
            samples = samples.reshape(-1, 2).T
            out = self._eq.process(samples)
            out = out.T.reshape(-1)
        else:
            out = self._eq.process(samples)
        return self._float_to_pcm(out)

    def set_band_gain(self, band_index, gain_db):
        self._eq.set_gain(band_index, gain_db)
        self.gains_db = self._eq.gains_db

    def set_all_gains(self, gains_db):
        self._eq.set_all_gains(gains_db)
        self.gains_db = self._eq.gains_db

    def reset(self):
        self._eq.reset()
        self.gains_db = self._eq.gains_db

    def configure(self, **kwargs):
        if "gains_db" in kwargs: self.set_all_gains(kwargs["gains_db"])
        if "enabled" in kwargs: self.enabled = bool(kwargs["enabled"])

    def get_frequency_response(self, num_points=512):
        return self._eq.get_frequency_response(num_points)

    def to_dict(self):
        return self._eq.to_dict()

    @classmethod
    def from_dict(cls, data, sample_rate=44100, channels=1):
        f = cls(sample_rate=sample_rate, channels=channels)
        f._eq = MultiBandEqualizer.from_dict(data, sample_rate)
        f.gains_db = f._eq.gains_db
        f.q_values = f._eq.q_values
        return f
