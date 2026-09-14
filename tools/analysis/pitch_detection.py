"""Pitch, tone, and note detection for AMP Silver audio tools.

Provides:
- PitchDetector: YIN + autocorrelation pitch detection
- NoteTracker: tracks notes over time with onset detection
"""

import threading
import numpy as np
from collections import deque


NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
A4_FREQ = 440.0
MIDI_A4 = 69


def freq_to_midi(freq):
    if freq <= 0:
        return 0
    return int(round(12 * np.log2(freq / A4_FREQ) + MIDI_A4))


def midi_to_freq(midi):
    return A4_FREQ * 2 ** ((midi - MIDI_A4) / 12.0)


def midi_to_note(midi):
    if midi < 0 or midi > 127:
        return "---"
    name = NOTE_NAMES[midi % 12]
    octave = (midi // 12) - 1
    return f"{name}{octave}"


def freq_to_note(freq):
    midi = freq_to_midi(freq)
    return midi_to_note(midi)


def cents_off(freq, target_freq):
    if freq <= 0 or target_freq <= 0:
        return 0
    return 1200 * np.log2(freq / target_freq)


class PitchDetector:
    """Pitch detection using YIN algorithm with autocorrelation fallback.

    Detects fundamental frequency of a mono audio buffer.
    Works on 16-bit PCM bytes or float64 arrays.
    """

    def __init__(self, sample_rate=44100, threshold=0.15,
                 min_freq=60.0, max_freq=4000.0, buffer_duration=0.1):
        self.sample_rate = sample_rate
        self.threshold = threshold
        self.min_freq = min_freq
        self.max_freq = max_freq
        self.buffer_duration = buffer_duration
        self._lock = threading.Lock()
        self._buffer = np.zeros(0, dtype=np.float64)

    def _to_float(self, pcm_bytes):
        if isinstance(pcm_bytes, bytes):
            return np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float64) / 32768.0
        return pcm_bytes.astype(np.float64)

    def difference_function(self, samples, tau_max):
        """YIN step 1: difference function."""
        n = len(samples)
        d = np.zeros(tau_max)
        for tau in range(1, tau_max):
            d[tau] = np.sum((samples[:n - tau] - samples[tau:n]) ** 2)
        return d

    def cumulative_mean_normalized(self, d):
        """YIN step 2: CMND function."""
        tau_max = len(d)
        cmnd = np.ones(tau_max)
        running_sum = 0.0
        for tau in range(1, tau_max):
            running_sum += d[tau]
            if running_sum > 0:
                cmnd[tau] = d[tau] * tau / running_sum
            else:
                cmnd[tau] = 1.0
        return cmnd

    def _yin(self, samples):
        """Run YIN algorithm, return (frequency, confidence) or (0, 0)."""
        tau_min = max(1, int(self.sample_rate / self.max_freq))
        tau_max = min(len(samples) // 2, int(self.sample_rate / self.min_freq))
        if tau_max <= tau_min + 1:
            return 0.0, 0.0

        d = self.difference_function(samples, tau_max + 1)
        cmnd = self.cumulative_mean_normalized(d)

        # Step 3: find first minimum below threshold
        tau_est = None
        for tau in range(tau_min, tau_max):
            if cmnd[tau] < self.threshold:
                # Find local minimum
                while tau + 1 < tau_max and cmnd[tau + 1] < cmnd[tau]:
                    tau += 1
                tau_est = tau
                break

        if tau_est is None:
            # No dip found — find global minimum in range
            tau_est = tau_min + np.argmin(cmnd[tau_min:tau_max])
            if cmnd[tau_est] >= 1.0:
                return 0.0, 0.0

        # Step 4: parabolic interpolation for sub-sample accuracy
        if tau_est > tau_min and tau_est < tau_max - 1:
            s0 = cmnd[tau_est - 1]
            s1 = cmnd[tau_est]
            s2 = cmnd[tau_est + 1]
            denom = 2.0 * (2 * s1 - s0 - s2)
            if abs(denom) > 1e-10:
                delta = (s0 - s2) / denom
                tau_est = tau_est + delta

        freq = self.sample_rate / tau_est if tau_est > 0 else 0.0
        confidence = 1.0 - cmnd[int(round(tau_est))] if 0 < int(round(tau_est)) < len(cmnd) else 0.0
        return freq, confidence

    def detect(self, pcm_bytes):
        """Detect pitch in a PCM buffer.

        Returns dict with: freq, confidence, midi, note, cents, valid
        """
        with self._lock:
            samples = self._to_float(pcm_bytes)
            if samples.size < 512:
                return {'freq': 0, 'confidence': 0, 'midi': 0, 'note': '---',
                        'cents': 0, 'valid': False}

            # Normalize
            peak = np.max(np.abs(samples))
            if peak < 0.01:
                return {'freq': 0, 'confidence': 0, 'midi': 0, 'note': '---',
                        'cents': 0, 'valid': False}
            samples = samples / peak

            freq, confidence = self._yin(samples)
            if freq <= 0 or confidence < 0.3:
                return {'freq': 0, 'confidence': 0, 'midi': 0, 'note': '---',
                        'cents': 0, 'valid': False}

            midi = freq_to_midi(freq)
            note = midi_to_note(midi)
            target = midi_to_freq(midi)
            cents = cents_off(freq, target)

            return {
                'freq': round(freq, 2),
                'confidence': round(confidence, 3),
                'midi': midi,
                'note': note,
                'cents': round(cents, 1),
                'valid': True,
            }

    def feed(self, pcm_bytes):
        """Accumulate PCM and detect pitch when buffer is full."""
        with self._lock:
            samples = self._to_float(pcm_bytes)
            self._buffer = np.concatenate([self._buffer, samples])
            needed = int(self.sample_rate * self.buffer_duration)
            results = []
            while len(self._buffer) >= needed:
                chunk = self._buffer[:needed]
                self._buffer = self._buffer[needed:]
                results.append(self.detect(chunk.tobytes() if isinstance(chunk, np.ndarray) else chunk))
            return results

    def reset(self):
        with self._lock:
            self._buffer = np.zeros(0, dtype=np.float64)


class NoteTracker:
    """Tracks notes over time with onset detection and duration.

    Converts a PCM stream into a sequence of (note, onset_time, duration).
    """

    def __init__(self, sample_rate=44100, hop_ms=20, note_min_duration_ms=50,
                 pitch_threshold_cents=50):
        self.sample_rate = sample_rate
        self.hop_ms = hop_ms
        self.note_min_duration_ms = note_min_duration_ms
        self.pitch_threshold_cents = pitch_threshold_cents
        self.detector = PitchDetector(sample_rate=sample_rate, buffer_duration=hop_ms / 1000.0)
        self._lock = threading.Lock()
        self._current_note = None
        self._current_start = 0.0
        self._time = 0.0
        self._hop_sec = hop_ms / 1000.0
        self.notes = []  # (note, start_time_sec, duration_sec)

    def process(self, pcm_bytes):
        """Process a PCM chunk and return newly completed notes."""
        new_notes = []
        with self._lock:
            results = self.detector.feed(pcm_bytes)
            for r in results:
                self._time += self._hop_sec
                if r['valid'] and abs(r['cents']) < self.pitch_threshold_cents:
                    note = r['note']
                    if note != self._current_note:
                        # Note change
                        if self._current_note is not None:
                            dur = self._time - self._current_start
                            if dur >= self.note_min_duration_ms / 1000.0:
                                self.notes.append((self._current_note, self._current_start, round(dur, 3)))
                                new_notes.append(self.notes[-1])
                        self._current_note = note
                        self._current_start = self._time
                else:
                    # Silence / unpitched — end current note
                    if self._current_note is not None:
                        dur = self._time - self._current_start
                        if dur >= self.note_min_duration_ms / 1000.0:
                            self.notes.append((self._current_note, self._current_start, round(dur, 3)))
                            new_notes.append(self.notes[-1])
                        self._current_note = None
        return new_notes

    def finalize(self):
        """Close any open note and return it."""
        with self._lock:
            if self._current_note is not None:
                dur = self._time - self._current_start
                if dur >= self.note_min_duration_ms / 1000.0:
                    self.notes.append((self._current_note, self._current_start, round(dur, 3)))
                self._current_note = None
            return list(self.notes)

    def reset(self):
        with self._lock:
            self.detector.reset()
            self._current_note = None
            self._current_start = 0.0
            self._time = 0.0
            self.notes.clear()
