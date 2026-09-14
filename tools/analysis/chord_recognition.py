"""Chord recognition for AMP Silver audio tools.

Converts detected notes (or a chromagram) into chord labels using
template matching against standard chord profiles.
"""

import threading
import numpy as np
from collections import Counter

from tools.analysis.pitch_detection import freq_to_midi, midi_to_note, NOTE_NAMES


# 12-dimensional chroma profiles for common chord types
CHORD_PROFILES = {
    # Major triads
    'C':  [1, 0, 0, 0, 1, 0, 0, 1, 0, 0, 0, 0],
    'C#': [0, 1, 0, 0, 0, 1, 0, 0, 1, 0, 0, 0],
    'D':  [0, 0, 1, 0, 0, 0, 1, 0, 0, 1, 0, 0],
    'D#': [0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 1, 0],
    'E':  [0, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 1],
    'F':  [1, 0, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0],
    'F#': [0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 1, 0],
    'G':  [0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 1],
    'G#': [1, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0],
    'A':  [0, 1, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0],
    'A#': [0, 0, 1, 0, 0, 1, 0, 0, 0, 0, 1, 0],
    'B':  [0, 0, 0, 1, 0, 0, 1, 0, 0, 0, 0, 1],
    # Minor triads
    'Cm':  [1, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 0],
    'C#m': [0, 1, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0],
    'Dm':  [0, 0, 1, 0, 0, 1, 0, 0, 0, 1, 0, 0],
    'D#m': [0, 0, 0, 1, 0, 0, 1, 0, 0, 0, 1, 0],
    'Em':  [0, 0, 0, 0, 1, 0, 0, 1, 0, 0, 0, 1],
    'Fm':  [1, 0, 0, 0, 0, 1, 0, 0, 1, 0, 0, 0],
    'F#m': [0, 1, 0, 0, 0, 0, 1, 0, 0, 1, 0, 0],
    'Gm':  [0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 1, 0],
    'G#m': [0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 1],
    'Am':  [1, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0],
    'A#m': [0, 1, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0],
    'Bm':  [0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 0, 1],
    # Dominant 7ths
    'C7':  [1, 0, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0],
    'G7':  [0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 1],
    'D7':  [0, 0, 1, 0, 0, 0, 1, 0, 0, 1, 0, 0],
    'A7':  [0, 1, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0],
    'E7':  [0, 0, 0, 0, 1, 0, 0, 1, 0, 0, 0, 1],
    # Major 7ths
    'Cmaj7':  [1, 0, 0, 0, 1, 0, 0, 1, 0, 0, 0, 1],
    'Fmaj7':  [1, 0, 0, 0, 0, 1, 0, 0, 1, 0, 0, 0],
    # Minor 7ths
    'Cm7': [1, 0, 0, 1, 0, 0, 0, 1, 0, 0, 1, 0],
    'Am7': [1, 0, 0, 0, 1, 0, 0, 0, 0, 1, 1, 0],
    'Dm7': [0, 0, 1, 0, 0, 1, 0, 0, 0, 1, 0, 0],
    'Em7': [0, 0, 0, 0, 1, 0, 0, 1, 0, 0, 1, 0],
}


class ChordRecognizer:
    """Template-matching chord recognition.

    Input: chromagram (12-dim energy per pitch class) or list of note names.
    Output: best-matching chord label + confidence.
    """

    def __init__(self, profiles=None):
        self.profiles = profiles or CHORD_PROFILES
        self._lock = threading.Lock()

    def notes_to_chroma(self, notes):
        """Convert list of note names (e.g. ['C4', 'E4', 'G4']) to chroma vector."""
        chroma = np.zeros(12)
        for n in notes:
            name = n.strip()
            # Strip octave
            pitch = ''
            for ch in name:
                if ch.isalpha() or ch == '#':
                    pitch += ch
                else:
                    break
            if pitch in NOTE_NAMES:
                idx = NOTE_NAMES.index(pitch)
                chroma[idx] += 1
        total = chroma.sum()
        if total > 0:
            chroma /= total
        return chroma

    def chroma_from_midi(self, midi_notes):
        """Convert list of MIDI note numbers to chroma."""
        chroma = np.zeros(12)
        for m in midi_notes:
            chroma[m % 12] += 1
        total = chroma.sum()
        if total > 0:
            chroma /= total
        return chroma

    def recognize_chord(self, chroma_or_notes):
        """Find best chord match for a chroma vector or note list.

        Returns dict: {chord, confidence, notes}
        """
        with self._lock:
            if isinstance(chroma_or_notes, (list, tuple)):
                chroma = self.notes_to_chroma(chroma_or_notes)
            elif isinstance(chroma_or_notes, np.ndarray):
                chroma = chroma_or_notes
                total = chroma.sum()
                if total > 0:
                    chroma = chroma / total
            else:
                return {'chord': 'N.C.', 'confidence': 0.0, 'notes': []}

            if chroma.sum() < 0.05:
                return {'chord': 'N.C.', 'confidence': 0.0, 'notes': []}

            best_chord = 'N.C.'
            best_score = -1.0
            scores = {}

            for chord_name, profile in self.profiles.items():
                profile_vec = np.array(profile, dtype=np.float64)
                # Cosine similarity
                dot = np.dot(chroma, profile_vec)
                norm = np.linalg.norm(chroma) * np.linalg.norm(profile_vec)
                if norm > 0:
                    score = dot / norm
                else:
                    score = 0.0
                scores[chord_name] = round(score, 4)
                if score > best_score:
                    best_score = score
                    best_chord = chord_name

            # Extract chord notes for display
            chord_notes = self._chord_to_notes(best_chord)

            return {
                'chord': best_chord,
                'confidence': round(best_score, 4),
                'notes': chord_notes,
                'all_scores': dict(sorted(scores.items(), key=lambda x: -x[1])[:5]),
            }

    def _chord_to_notes(self, chord_name):
        """Get constituent notes of a chord label."""
        if chord_name == 'N.C.':
            return []
        # Parse root
        root = chord_name.split('m')[0].split('7')[0].split('maj')[0].split('dim')[0].split('aug')[0]
        if root not in NOTE_NAMES:
            return []
        root_idx = NOTE_NAMES.index(root)

        intervals = [0, 4, 7]  # major triad default
        if 'dim' in chord_name:
            intervals = [0, 3, 6]
        elif 'aug' in chord_name:
            intervals = [0, 4, 8]
        elif 'm7' in chord_name:
            intervals = [0, 3, 7, 10]
        elif 'maj7' in chord_name:
            intervals = [0, 4, 7, 11]
        elif '7' in chord_name:
            intervals = [0, 4, 7, 10]
        elif 'm' in chord_name:
            intervals = [0, 3, 7]

        return [NOTE_NAMES[(root_idx + i) % 12] for i in intervals]

    def recognize_from_notes(self, notes_with_duration):
        """Given list of (note_name, start, duration), find most common chord."""
        all_notes = [n for n, _, _ in notes_with_duration]
        if not all_notes:
            return {'chord': 'N.C.', 'confidence': 0.0, 'notes': []}
        return self.recognize_chord(all_notes)
