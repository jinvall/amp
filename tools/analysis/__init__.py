"""Audio analysis tools for AMP Silver.

Provides pitch/tone/note detection, chord recognition, and transcription.
"""
from tools.analysis.pitch_detection import PitchDetector, NoteTracker
from tools.analysis.chord_recognition import ChordRecognizer, CHORD_PROFILES
from tools.analysis.audio_transcribe import transcribe_audio, TranscriptionResult

__all__ = [
    'PitchDetector',
    'NoteTracker',
    'ChordRecognizer',
    'CHORD_PROFILES',
    'transcribe_audio',
    'TranscriptionResult',
]
