#!/usr/bin/env python3
"""Stem extraction manager with capped rotation storage.

Separates stored WAV segments into stems using a pluggable backend.
Stems are stored under ``audio_stems/<segment_id>/`` and rotated by total
duration so storage stays bounded.
"""

import os
import shutil
import time
import threading
from collections import OrderedDict

import audio_receiver as ar


class StemManager:
    """Owns stem extraction, storage, and LRU rotation."""

    def __init__(self, output_dir, max_seconds=1800, backend=None, backend_preset=None, viz_feed=None):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)
        self.max_seconds = max(60, int(max_seconds))
        self.backend = backend or self._default_backend()
        self._backend_preset = backend_preset
        # Optional VisualizerFeed (waveform + spectrogram). When set, the graphs
        # are paused for the duration of a heavy extraction so the CPU/RAM is
        # freed for the model instead of being split with live visualization.
        self.viz_feed = viz_feed
        # Backends whose extraction loads a heavy model and benefits from
        # pausing the live graphs.
        self._heavy_backends = {"audio_separator", "demucs"}
        self._lock = threading.RLock()
        # segment_id -> {"duration": float, "stems": dict, "created": float}
        self._entries = OrderedDict()
        # Recent extraction exceptions: list of {"time": epoch, "segment_id": str, "error": str}
        self._exceptions = []
        self._max_exceptions = 50
        # Prevent duplicate concurrent extractions for the same segment.
        self._pending = set()
        self._scan()

    def _pause_graphs(self):
        """Pause the visualizer graphs during a heavy extraction, if configured."""
        if self.viz_feed is not None and self.backend in self._heavy_backends:
            try:
                self.viz_feed.pause()
                return True
            except Exception as e:
                print(f"[stem] failed to pause graphs: {e}")
        return False

    def _resume_graphs(self):
        """Resume the visualizer graphs if they were paused."""
        if self.viz_feed is not None:
            try:
                self.viz_feed.resume()
            except Exception as e:
                print(f"[stem] failed to resume graphs: {e}")

    @staticmethod
    def _default_backend():
        """Return the default backend.

        NOTE: the deployment target is a 4-core / 16GB / CPU-only mini PC. ML
        backends (demucs, audio_separator) load multi-hundred-MB models and are
        far too heavy to run by default — they OOM the box. The DEFAULT is the
        instant, RAM-free ``frequency_band`` split (renamed to the requested
        vocals/guitar/bass/drums + noise bands). Operators can opt into real ML
        separation explicitly via config: ``stem_backend: "demucs"`` (or
        ``"audio_separator"``), accepting the RAM/CPU cost.
        """
        try:
            import stem_backends  # noqa: F401
            return "frequency_band"
        except ImportError:
            try:
                import audio_separator_backend  # noqa: F401
                return "audio_separator"
            except ImportError:
                return None

    # ── Public API ───────────────────────────────────────────────────────
    def extract(self, segment_id, segment_path):
        """Extract stems from ``segment_path`` into ``audio_stems/<segment_id>/``.

        Returns dict of stem_name -> relative path, or None on failure.
        """
        # Fast path: already extracted.
        with self._lock:
            if segment_id in self._entries:
                entry = self._entries[segment_id]
                if entry.get("stems"):
                    return entry["stems"]
            # Prevent duplicate concurrent extractions.
            if segment_id in self._pending:
                print(f"[stem] extraction already pending for {segment_id}")
                return None
            self._pending.add(segment_id)
            stem_dir = os.path.join(self.output_dir, segment_id)
            os.makedirs(stem_dir, exist_ok=True)
            backend_name = self.backend or "none"
            print(f"[stem] extracting {segment_id} with backend={backend_name}")

        try:
            if self.backend == "demucs":
                from stem_backends import DemucsBackend
                self._pause_graphs()
                try:
                    stems = DemucsBackend.extract(segment_path, stem_dir)
                finally:
                    self._resume_graphs()
            elif self.backend == "frequency_band":
                from stem_backends import FrequencyBandBackend
                stems = FrequencyBandBackend.extract(segment_path, stem_dir)
            elif self.backend == "audio_separator":
                from audio_separator_backend import AudioSeparatorBackend
                preset = getattr(self, '_backend_preset', None)
                self._pause_graphs()
                try:
                    stems = AudioSeparatorBackend.extract(segment_path, stem_dir, preset=preset)
                finally:
                    self._resume_graphs()
            else:
                print("[stem] no backend configured; skipping extraction")
                stems = None
        except Exception as e:
            stems = None
            self.record_exception(segment_id, e)
            print(f"[stem] extraction failed for {segment_id}: {e}")
        finally:
            with self._lock:
                self._pending.discard(segment_id)

        if not stems:
            return None

        duration = self._probe_duration(segment_path) or 0
        with self._lock:
            self._entries[segment_id] = {
                "duration": duration,
                "stems": stems,
                "created": time.time(),
            }
            self._entries.move_to_end(segment_id)
            self._prune()
        return stems

    def list_stems(self, segment_id=None):
        """Return stem info for all segments, or one segment."""
        with self._lock:
            if segment_id is not None:
                entry = self._entries.get(segment_id)
                return entry["stems"] if entry else {}
            return {sid: entry["stems"] for sid, entry in self._entries.items()}

    def record_exception(self, segment_id, error):
        """Record a recent extraction exception for UI visibility."""
        with self._lock:
            self._exceptions.append({
                "time": time.time(),
                "segment_id": segment_id,
                "error": str(error),
            })
            if len(self._exceptions) > self._max_exceptions:
                self._exceptions = self._exceptions[-self._max_exceptions:]

    def recent_exceptions(self, limit=20):
        """Return the most recent extraction exceptions."""
        with self._lock:
            return self._exceptions[-limit:]

    def remove(self, segment_id):
        """Remove all stems for a segment."""
        with self._lock:
            stem_dir = os.path.join(self.output_dir, segment_id)
            if os.path.exists(stem_dir):
                shutil.rmtree(stem_dir, ignore_errors=True)
            self._entries.pop(segment_id, None)

    def stats(self):
        with self._lock:
            total_dur = sum(e["duration"] for e in self._entries.values())
            viz_paused = bool(getattr(self.viz_feed, '_paused', False)) if self.viz_feed is not None else False
            viz_disabled = bool(getattr(self.viz_feed, '_extract_disable', 0) > 0) if self.viz_feed is not None else False
            graph_states = self.viz_feed.graph_states() if self.viz_feed is not None else {}
            return {
                "backend": self.backend,
                "max_seconds": self.max_seconds,
                "total_stored_seconds": total_dur,
                "segment_count": len(self._entries),
                "usage_pct": round(100.0 * total_dur / self.max_seconds, 1) if self.max_seconds else 0,
                "graphs_paused": viz_paused,
                "graphs_disabled": viz_disabled,
                "graph_states": graph_states,
            }

    # ── Internal ─────────────────────────────────────────────────────────
    def _scan(self):
        """Rebuild index from existing ``audio_stems/`` directory."""
        if not os.path.isdir(self.output_dir):
            return
        for segment_id in os.listdir(self.output_dir):
            stem_dir = os.path.join(self.output_dir, segment_id)
            if not os.path.isdir(stem_dir):
                continue
            segment_path = os.path.join(
                ar.OUTPUT_DIR, f"{segment_id}.flac"
            )
            if not os.path.exists(segment_path):
                segment_path = os.path.join(
                    ar.OUTPUT_DIR, f"{segment_id}.wav"
                )
            duration = self._probe_duration(segment_path) if os.path.exists(segment_path) else 0
            stems = {}
            if os.path.isdir(stem_dir):
                stems = {f: os.path.join(stem_dir, f) for f in os.listdir(stem_dir)}
            if stems:
                self._entries[segment_id] = {
                    "duration": duration,
                    "stems": stems,
                    "created": time.time(),
                }
        # Bring storage back under cap immediately on startup. Without this, a
        # backlog created while the old (deadlocked) code was running would
        # never be pruned until a fresh extraction happened to fire.
        self._prune()

    def _prune(self):
        """LRU eviction until total duration is under cap."""
        total = sum(e["duration"] for e in self._entries.values())
        while total > self.max_seconds and self._entries:
            sid, entry = self._entries.popitem(last=False)
            self.remove(sid)
            total -= entry.get("duration", 0)

    @staticmethod
    def _probe_duration(path):
        try:
            if path.lower().endswith('.flac'):
                import soundfile as sf
                info = sf.info(path)
                return info.duration
            else:
                import wave
                with wave.open(path, "rb") as w:
                    return w.getnframes() / float(w.getframerate())
        except Exception as e:
            print(f"[stem] _probe_duration failed for {path}: {e}")
            return 0
