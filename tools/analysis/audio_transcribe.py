"""Audio transcription for AMP Silver.

Provides transcription via faster-whisper (preferred) or openai-whisper (fallback).
Returns text, language, confidence, and word-level or segment-level timestamps.

Install:
    pip install faster-whisper  # preferred, faster on CPU
    # OR
    pip install openai-whisper   # slower fallback
"""

import os
import threading
import logging
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class TranscriptionSegment:
    start: float = 0.0
    end: float = 0.0
    text: str = ""
    confidence: float = 0.0


@dataclass
class TranscriptionResult:
    text: str = ""
    language: str = "en"
    confidence: float = 0.0
    segments: list = field(default_factory=list)
    duration: float = 0.0
    model: str = ""
    backend: str = ""  # 'faster-whisper' or 'whisper' or 'none'
    error: str = ""

    @property
    def has_text(self):
        return bool(self.text.strip())

    def to_dict(self):
        return {
            'text': self.text,
            'language': self.language,
            'confidence': round(self.confidence, 4),
            'segments': [
                {'start': round(s.start, 3), 'end': round(s.end, 3),
                 'text': s.text, 'confidence': round(s.confidence, 4)}
                for s in self.segments
            ],
            'duration': round(self.duration, 2),
            'model': self.model,
            'backend': self.backend,
            'error': self.error,
        }


class Transcriber:
    """Transcriber that auto-selects the best available backend.

    Priority:
    1. faster-whisper (CTranslate2, fast on CPU)
    2. openai-whisper (slower, torch-based)
    3. None available → returns error result
    """

    def __init__(self, model_size="base", device="cpu", compute_type="int8",
                 cache_dir=None):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.cache_dir = cache_dir or os.path.expanduser("~/.cache/whisper")
        self._lock = threading.Lock()
        self._model = None
        self._backend = None

    def _load_model(self):
        if self._model is not None:
            return True
        with self._lock:
            if self._model is not None:
                return True

            # Try faster-whisper first
            try:
                from faster_whisper import WhisperModel
                os.makedirs(self.cache_dir, exist_ok=True)
                self._model = WhisperModel(
                    self.model_size,
                    device=self.device,
                    compute_type=self.compute_type,
                    download_root=self.cache_dir,
                )
                self._backend = "faster-whisper"
                log.info(f"faster-whisper model '{self.model_size}' loaded")
                return True
            except ImportError:
                pass
            except Exception as e:
                log.warning(f"faster-whisper load failed: {e}")

            # Fall back to openai-whisper
            try:
                import whisper
                self._model = whisper.load_model(
                    self.model_size,
                    device=self.device,
                    download_root=self.cache_dir,
                )
                self._backend = "whisper"
                log.info(f"whisper model '{self.model_size}' loaded")
                return True
            except ImportError:
                pass
            except Exception as e:
                log.warning(f"whisper load failed: {e}")

            self._backend = "none"
            return False

    def transcribe(self, audio_path: str, language=None, verbose=False):
        """Transcribe an audio file.

        Returns a TranscriptionResult.
        """
        result = TranscriptionResult(model=self.model_size)
        if not self._load_model():
            result.error = "No transcription backend available. Install faster-whisper or whisper."
            return result

        try:
            if not os.path.exists(audio_path):
                result.error = f"File not found: {audio_path}"
                return result

            if self._backend == "faster-whisper":
                return self._transcribe_faster(audio_path, language, result)
            elif self._backend == "whisper":
                return self._transcribe_whisper(audio_path, language, result)
            else:
                result.error = "No backend loaded"
                return result
        except Exception as e:
            result.error = f"Transcription failed: {str(e)}"
            log.error(result.error, exc_info=True)
            return result

    def _transcribe_faster(self, audio_path, language, result):
        from faster_whisper import WhisperModel
        segments_iter, info = self._model.transcribe(
            audio_path,
            language=language,
            vad_filter=True,
            beam_size=5,
        )

        result.language = info.language or "en"
        result.backend = "faster-whisper"
        result.duration = info.duration
        all_text = []
        all_conf = []

        for seg in segments_iter:
            ts = TranscriptionSegment(
                start=seg.start,
                end=seg.end,
                text=seg.text.strip(),
                confidence=seg.avg_log_prob if hasattr(seg, 'avg_log_prob') else 0.0,
            )
            result.segments.append(ts)
            all_text.append(seg.text.strip())
            all_conf.append(seg.avg_log_prob)

        result.text = " ".join(all_text)
        if all_conf:
            # Convert log-prob to a rough 0-1 confidence
            avg_lp = sum(all_conf) / len(all_conf)
            result.confidence = max(0.0, min(1.0, 1.0 + avg_lp))
        return result

    def _transcribe_whisper(self, audio_path, language, result):
        opts = {}
        if language:
            opts['language'] = language
        out = self._model.transcribe(audio_path, **opts)

        result.language = out.get('language', 'en')
        result.backend = "whisper"
        result.text = out.get('text', '').strip()
        result.duration = 0.0
        all_conf = []

        for seg in out.get('segments', []):
            ts = TranscriptionSegment(
                start=seg.get('start', 0),
                end=seg.get('end', 0),
                text=seg.get('text', '').strip(),
                confidence=1.0 - seg.get('no_speech_prob', 0.5),
            )
            result.segments.append(ts)
            all_conf.append(ts.confidence)
            result.duration = max(result.duration, ts.end)

        if all_conf:
            result.confidence = sum(all_conf) / len(all_conf)
        return result


# ── Module-level convenience ──────────────────────────────────────────────────

_transcriber_cache = {}
_transcriber_lock = threading.Lock()


def _get_transcriber(model_size="base", **kwargs):
    key = (model_size, kwargs.get("device", "cpu"))
    with _transcriber_lock:
        if key not in _transcriber_cache:
            _transcriber_cache[key] = Transcriber(model_size=model_size, **kwargs)
        return _transcriber_cache[key]


def transcribe_audio(audio_path: str, model_size="base", language=None, **kwargs):
    """Transcribe an audio file. Convenience wrapper around Transcriber.

    Returns a TranscriptionResult with .text, .segments, .confidence, etc.
    """
    t = _get_transcriber(model_size=model_size, **kwargs)
    return t.transcribe(audio_path, language=language)


def transcribe_pcm(pcm_bytes: bytes, sample_rate=44100, model_size="base",
                   language=None, **kwargs):
    """Transcribe raw PCM bytes (writes to temp WAV then transcribes)."""
    import tempfile, wave
    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
        with wave.open(f.name, 'wb') as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            w.writeframes(pcm_bytes)
        return transcribe_audio(f.name, model_size=model_size, language=language, **kwargs)


def available_backends():
    """Check which transcription backends are available."""
    backends = []
    try:
        import faster_whisper
        backends.append('faster-whisper')
    except ImportError:
        pass
    try:
        import whisper
        backends.append('whisper')
    except ImportError:
        pass
    return backends


def is_ready():
    """Return True if at least one transcription backend is importable."""
    return len(available_backends()) > 0
