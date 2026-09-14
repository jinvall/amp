#!/usr/bin/env python3
"""
Audio Receiver for Silver
- Receives raw PCM audio from Android via TCP
- Encodes to MP3
- Segments into 5-minute files with 5-second overlap
- Maintains FIFO storage of ~1 hour total
- Optional real-time breathing detection
"""

import socket
import threading
import os
import sys
import time
import struct
import wave
import json
import hashlib
import shutil
import subprocess
import queue as queue_module
from datetime import datetime
from collections import deque

try:
    import lameenc
    HAS_LAME = True
except ImportError:
    HAS_LAME = False

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

# Force unbuffered output
sys.stdout.reconfigure(line_buffering=True)

# ── Config ────────────────────────────────────────────────────────────────────
HOST = '0.0.0.0'
# Port range for all server listeners. The 808x range was congested on the host, so
# we bind inside 8090..8099. Each listener hunts for a free port within this range at
# startup unless an explicit port is passed on the command line.
PORT_RANGE = (8090, 8099)
PORT = PORT_RANGE[0]         # PCM audio (default; receiver hunts within PORT_RANGE if busy)
CONTROL_PORT = PORT_RANGE[0] + 1   # live config push channel (JSON lines), separate from PCM
VIZ_PORT = PORT_RANGE[0] + 2      # real-time visualization WebSocket feed (waveform + spectrogram)
CONFIG_PATH = 'config.json'  # persisted settings; hot-reloaded, never fatal if missing
HOT_RELOAD_INTERVAL_SEC = 2.0

# ── Visualization / latency budget ──────────────────────────────────────────────
# Hard real-time budget: end-to-end (mic -> Silver -> analysis -> WS -> paint) <= 300 ms.
# We emit one analysis frame per VIZ_FRAME_SEC of audio. Smaller = lower latency but more CPU.
VIZ_FRAME_SEC = 0.020            # 20 ms analysis frame -> 50 fps target, ~880 bytes PCM
VIZ_WAVEFORM_POINTS = 256        # min/max peak pairs shipped per frame (downsampled)
VIZ_SPECTROGRAM_BINS = 256       # log-scaled magnitude bins per spectrogram column
VIZ_FFT_SIZE = 1024              # rfft window; >= frame samples (880) so we zero-pad
VIZ_MAX_LATENCY_MS = 300.0       # budget; exceeded frames are flagged in the UI
VIZ_SEND_EVERY_N_FRAMES = 1      # push every analysis frame (1 = lowest latency)
SAMPLE_RATE = 44100
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit
BYTES_PER_SECOND = SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH  # 88200

SEGMENT_DURATION_SEC = 5 * 60       # 5 minutes
OVERLAP_DURATION_SEC = 5             # 5 seconds
STEP_SEC = SEGMENT_DURATION_SEC - OVERLAP_DURATION_SEC  # 295 seconds

SEGMENT_BYTES = SEGMENT_DURATION_SEC * BYTES_PER_SECOND   # 26,460,000
STEP_BYTES = STEP_SEC * BYTES_PER_SECOND                   # 26,049,000

MAX_STORAGE_SECONDS = 60 * 60  # 1 hour
MAX_STORAGE_BYTES = 6 * 1024 * 1024 * 1024  # 6 GiB cumulative cap
OUTPUT_DIR = 'audio_segments'

# Breathing detection config (defaults; overridden by config.json / client)
BREATHING_ENABLE = False
BREATHING_BAND_LOW_HZ = 100.0
BREATHING_BAND_HIGH_HZ = 3000.0
BREATHING_ENERGY_THRESHOLD = 1.2e3  # very sensitive for quiet sounds
BREATHING_MIN_INTERVAL_SEC = 2.0
BREATHING_WINDOW_SEC = 2.0
BREATHING_HOP_SEC = 0.5


# ── Config persistence (fail-safe: never raises) ────────────────────────────────
def load_config(path):
    """Load config.json. Returns {} on any error — never raises 'fail to fetch'."""
    try:
        if not path or not os.path.exists(path):
            return {}
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return data
    except Exception as e:
        print(f"[{datetime.now().isoformat()}] Warning: could not load config '{path}': {e}")
        return {}


def save_config(path, cfg):
    """Atomically write config.json. Returns True on success, False on failure."""
    try:
        if not path:
            return False
        tmp = f"{path}.tmp"
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=2, sort_keys=True)
        os.replace(tmp, path)
        return True
    except Exception as e:
        print(f"[{datetime.now().isoformat()}] Warning: could not save config '{path}': {e}")
        return False


def find_free_port(host=HOST, rng=PORT_RANGE, exclude=None):
    """Return the first free TCP port within rng (inclusive), skipping `exclude`.

    Used at startup so the receiver binds inside 8090..8099 without colliding with
    other instances or already-bound ports. Fail-safe: returns None if the whole
    range is busy (the caller then decides what to do).
    """
    exclude = set(exclude or [])
    lo, hi = rng
    for port in range(lo, hi + 1):
        if port in exclude:
            continue
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind((host, port))
            return port
        except OSError:
            continue
    return None


def allocate_ports(host=HOST, rng=PORT_RANGE, pcm=None, control=None, viz=None):
    """Pick distinct free ports for PCM / control / viz within rng.

    Explicit (non-None) ports are honored if free; otherwise each listener hunts within
    rng. Raises RuntimeError only if a required explicit port is unavailable or the range
    is exhausted. Returns (pcm, control, viz).
    """
    used = set()
    result = {}
    for name, requested in (("pcm", pcm), ("control", control), ("viz", viz)):
        if requested is not None:
            # Honor explicit port; verify it is free.
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    s.bind((host, requested))
            except OSError:
                raise RuntimeError(f"Requested {name} port {requested} is already in use")
            result[name] = requested
            used.add(requested)
        else:
            p = find_free_port(host, rng, exclude=used)
            if p is None:
                raise RuntimeError(f"No free port in range {rng} for {name} "
                                   f"(used={sorted(used)})")
            result[name] = p
            used.add(p)
    return result["pcm"], result["control"], result["viz"]


def _config_hash(cfg):
    try:
        return hashlib.sha256(json.dumps(cfg, sort_keys=True).encode('utf-8')).hexdigest()
    except Exception as e:
        print(f"[{datetime.now().isoformat()}] config_hash failed: {e}")
        return None


class StorageManager:
    """FIFO storage: keep total duration under MAX_STORAGE_SECONDS."""

    def __init__(self, output_dir, max_seconds=None, max_bytes=None):
        self.output_dir = output_dir
        self.max_seconds = max_seconds or 0
        self.max_bytes = max_bytes or MAX_STORAGE_BYTES
        self.segments = []  # list of (filepath, duration_sec, size_bytes)
        # Index files that already exist on disk so a restart does not "forget"
        # them and let storage grow unbounded. Without this, pruning only ever
        # saw segments saved in the current process and the backlog never shrank.
        self._scan()
        self._prune()

    @staticmethod
    def _probe_duration(path):
        """Real duration (seconds) for WAV or FLAC. Returns 0 on any failure."""
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
            print(f"[{datetime.now().isoformat()}] StorageManager._probe_duration failed for {path}: {e}")
            return 0

    def _scan(self):
        """Rebuild the segment index from existing files on disk (oldest first)."""
        if not os.path.isdir(self.output_dir):
            return
        files = []
        for name in os.listdir(self.output_dir):
            if not name.lower().endswith(".wav"):
                continue
            p = os.path.join(self.output_dir, name)
            if not os.path.isfile(p):
                continue
            files.append(p)
        # Oldest first so FIFO eviction order is correct after a restart.
        files.sort(key=lambda p: os.path.getmtime(p))
        for p in files:
            dur = self._probe_duration(p)
            sz = os.path.getsize(p) if os.path.exists(p) else 0
            self.segments.append((p, dur, sz))

    def add_segment(self, filepath, duration_sec=None):
        # Prefer the real on-disk duration; fall back to the caller's estimate.
        dur = self._probe_duration(filepath) if os.path.exists(filepath) else (duration_sec or 0)
        if dur <= 0 and duration_sec:
            dur = duration_sec
        sz = os.path.getsize(filepath) if os.path.exists(filepath) else 0
        self.segments.append((filepath, dur, sz))
        self._prune()

    def _prune(self):
        total_dur = sum(d for _, d, _ in self.segments)
        total_bytes = sum(s for _, _, s in self.segments)
        while ((self.max_seconds > 0 and total_dur > self.max_seconds) or
               (self.max_bytes > 0 and total_bytes > self.max_bytes)) and self.segments:
            old_path, old_dur, old_sz = self.segments.pop(0)
            try:
                os.remove(old_path)
                print(f"Pruned: {old_path} ({old_dur:.1f}s, {old_sz} bytes)")
            except OSError as e:
                print(f"Prune failed: {e}")
            total_dur -= old_dur
            total_bytes -= old_sz


class BreathingDetector:
    """Real-time breathing detector using band-limited energy + cadence."""

    def __init__(self, sample_rate=44100):
        self.sample_rate = sample_rate
        self.band_low = BREATHING_BAND_LOW_HZ
        self.band_high = BREATHING_BAND_HIGH_HZ
        self.threshold = BREATHING_ENERGY_THRESHOLD
        self.min_interval = BREATHING_MIN_INTERVAL_SEC
        self.window_sec = BREATHING_WINDOW_SEC
        self.hop_sec = BREATHING_HOP_SEC

        self.window_samples = int(self.window_sec * sample_rate)
        self.hop_samples = int(self.hop_sec * sample_rate)
        self.ring = bytearray()
        self.last_detection_ts = 0.0

    def feed(self, pcm_bytes):
        if not HAS_NUMPY:
            return None
        self.ring.extend(pcm_bytes)
        detections = []
        while len(self.ring) >= self.window_samples:
            window = bytes(self.ring[: self.window_samples])
            del self.ring[: self.hop_samples]
            energy = self._band_energy(window)
            now = time.time()
            if energy >= self.threshold and (now - self.last_detection_ts) >= self.min_interval:
                self.last_detection_ts = now
                detections.append((now, energy))
        return detections

    def _band_energy(self, pcm_bytes):
        audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float64)
        if audio.size == 0:
            return 0.0
        fft = np.fft.rfft(audio)
        freqs = np.fft.rfftfreq(audio.size, d=1.0 / self.sample_rate)
        mask = (freqs >= self.band_low) & (freqs <= self.band_high)
        mag = np.abs(fft) ** 2 / audio.size if audio.size > 0 else np.abs(fft) ** 2
        return float(mag[mask].sum()) if np.any(mask) else 0.0


class VisualizerAnalyzer:
    """Converts a PCM ring buffer into low-latency analysis frames (waveform + spectrogram).

    Runs inside the receiver's worker thread. FFT is computed on Silver so the browser
    stays dumb and the 300 ms budget holds on weak clients.
    """

    # Supported spectrogram presets: windowing function + frequency scale.
    PRESETS = {
        'kalman':     {'window': 'kaiser',  'scale': 'linear'},
        'black':      {'window': 'blackman','scale': 'linear'},
        'haris':      {'window': 'hamming', 'scale': 'linear'},
        'hann':       {'window': 'hann',    'scale': 'linear'},
        'mel':        {'window': 'hann',    'scale': 'mel'},
        'linear':     {'window': 'hann',    'scale': 'linear'},
        'logarythmic':{'window': 'hann',    'scale': 'log'},
    }
    DEFAULT_PRESET = 'logarythmic'

    def __init__(self, sample_rate=SAMPLE_RATE, preset=DEFAULT_PRESET):
        self.sample_rate = sample_rate
        self.frame_samples = int(VIZ_FRAME_SEC * sample_rate)
        self.fft_size = VIZ_FFT_SIZE
        self.preset = preset if preset in self.PRESETS else self.DEFAULT_PRESET
        # Per-graph enable flags (independent waveform / spectrogram toggles).
        # When False, analyze() skips that graph entirely (saves CPU on the
        # mini PC) and the frame carries null for the disabled graph so the
        # browser stops drawing it.
        self.wave_enabled = True
        self.spec_enabled = True
        self._recompute_bins()

    def set_preset(self, preset):
        if preset != self.preset and preset in self.PRESETS:
            self.preset = preset
            self._recompute_bins()

    def _recompute_bins(self):
        p = self.PRESETS[self.preset]
        freqs = np.fft.rfftfreq(self.fft_size, d=1.0 / self.sample_rate)
        scale = p['scale']
        if scale == 'log':
            self._spec_bins = self._log_bin_edges(freqs, VIZ_SPECTROGRAM_BINS)
        elif scale == 'mel':
            self._spec_bins = self._mel_bin_edges(freqs, VIZ_SPECTROGRAM_BINS, self.sample_rate)
        else:
            self._spec_bins = self._linear_bin_edges(freqs, VIZ_SPECTROGRAM_BINS)

    @staticmethod
    def _linear_bin_edges(freqs, n_bins):
        """Evenly spaced linear-frequency bins."""
        ranges = []
        step = max(1, len(freqs) // n_bins)
        for i in range(n_bins):
            lo = i * step
            hi = min((i + 1) * step, len(freqs))
            hi = max(hi, lo + 1)
            ranges.append((int(lo), int(hi)))
        return ranges

    @staticmethod
    def _log_bin_edges(freqs, n_bins):
        """Return list of (start_idx, end_idx) index ranges mapping linear rfft bins
        into n_bins log-spaced bands."""
        fmin, fmax = max(freqs[1], 20.0), freqs[-1]
        edges = np.logspace(np.log10(fmin), np.log10(fmax), n_bins + 1)
        ranges = []
        for i in range(n_bins):
            lo = np.searchsorted(freqs, edges[i], side='left')
            hi = np.searchsorted(freqs, edges[i + 1], side='right')
            hi = max(hi, lo + 1)
            ranges.append((int(lo), int(hi)))
        return ranges

    @staticmethod
    def _mel_bin_edges(freqs, n_bins, sample_rate):
        """Map linear rfft bins into n_bins mel-spaced bands (up to Nyquist)."""
        nyquist = sample_rate / 2.0
        mel_min = 0.0
        mel_max = 2595.0 * np.log10(1.0 + nyquist / 700.0)
        mel_edges = np.linspace(mel_min, mel_max, n_bins + 1)
        hz_edges = 700.0 * (np.power(10.0, mel_edges / 2595.0) - 1.0)
        ranges = []
        for i in range(n_bins):
            lo = np.searchsorted(freqs, hz_edges[i], side='left')
            hi = np.searchsorted(freqs, hz_edges[i + 1], side='right')
            hi = max(hi, lo + 1)
            ranges.append((int(lo), int(hi)))
        return ranges

    def _window(self, n):
        """Return the window function for the current preset."""
        name = self.PRESETS[self.preset]['window']
        if name == 'kaiser':
            # Kaiser with beta=8 gives good mainlobe/sidelobe tradeoff.
            return np.kaiser(n, beta=8.0)
        if name == 'blackman':
            return np.blackman(n)
        if name == 'hamming':
            return np.hamming(n)
        # hann (default)
        return np.hanning(n)

    def analyze(self, pcm_bytes, sender_ts):
        """pcm_bytes: raw 16-bit LE mono PCM. Returns a dict frame or None if too short."""
        if len(pcm_bytes) < self.frame_samples * 2:
            return None
        audio = np.frombuffer(pcm_bytes[: self.frame_samples * 2], dtype=np.int16).astype(np.float64)
        if audio.size == 0:
            return None

        # ── RMS (normalized 0..1) — always computed (cheap, drives the meter) ──
        rms = float(np.sqrt(np.mean(audio ** 2))) / 32768.0

        # ── Waveform: downsample to VIZ_WAVEFORM_POINTS via min/max envelope ──
        # Using mean (DC offset) makes the waveform invisible for AC-coupled
        # audio. Instead we compute peak amplitude per bin: the max absolute
        # deviation from zero, which captures the signal envelope.
        wave = None
        if self.wave_enabled:
            n = audio.size
            step = max(1, n // VIZ_WAVEFORM_POINTS)
            wave = np.zeros(VIZ_WAVEFORM_POINTS, dtype=np.float32)
            for i in range(VIZ_WAVEFORM_POINTS):
                s = i * step
                e = min(s + step, n)
                wave[i] = float(np.max(np.abs(audio[s:e]))) / 32768.0

        # ── Spectrogram: rfft -> magnitude -> aggregate to frequency bands ──
        spec = None
        if self.spec_enabled:
            win = self._window(audio.size)
            windowed = audio * win
            if audio.size < self.fft_size:
                pad = np.zeros(self.fft_size - audio.size, dtype=np.float64)
                windowed = np.concatenate([windowed, pad])
            spec_full = np.abs(np.fft.rfft(windowed, n=self.fft_size))
            spec = np.zeros(VIZ_SPECTROGRAM_BINS, dtype=np.float32)
            for b, (lo, hi) in enumerate(self._spec_bins):
                spec[b] = float(np.mean(spec_full[lo:hi]))
            # Normalize to dB and clamp to a fixed floor for stable coloring.
            eps = 1e-6
            spec_db = 20.0 * np.log10(spec / (spec.max() if spec.max() > 0 else 1.0) + eps)
            spec_db = np.clip(spec_db, -80.0, 0.0)
            spec = (spec_db + 80.0) / 80.0  # 0..1

        return {
            "t": sender_ts,                 # analysis timestamp (epoch seconds, float)
            "rms": round(rms, 6),
            "wave": [round(float(x), 5) for x in wave] if wave is not None else None,
            "spec": [round(float(x), 5) for x in spec] if spec is not None else None,
            # Explicit effective state so the browser can pause+dump a disabled
            # graph's ring instead of letting it keep filling.
            "wave_active": wave is not None,
            "spec_active": spec is not None,
            "preset": self.preset,
        }


class LiveAudioMonitor:
    """Stream live PCM to the system speaker/headphone output when monitoring is enabled."""

    def __init__(self, sample_rate=SAMPLE_RATE, channels=CHANNELS, sample_width=SAMPLE_WIDTH):
        self.sample_rate = sample_rate
        self.channels = channels
        self.sample_width = sample_width
        self.is_enabled = True
        self._lock = threading.Lock()
        self._proc = None
        self._cmd = self._choose_command()
        self.total_written = 0

    def _audio_device_available(self):
        """Enable the real monitor whenever the host has a usable audio tool available."""
        if shutil.which('ffplay'):
            return True
        if shutil.which('aplay'):
            return True
        if shutil.which('paplay'):
            return True
        return False

    def _choose_command(self):
        if not self._audio_device_available():
            return None

        if shutil.which('ffplay'):
            # ffplay is the lowest-latency choice here; keep it aggressively
            # real-time and avoid extra buffering or blocking decode delay.
            return [
                'ffplay',
                '-loglevel', 'quiet',
                '-nodisp',
                '-autoexit',
                '-fflags', 'nobuffer',
                '-flags', 'low_delay',
                '-framedrop',
                '-f', 's16le',
                '-ar', str(self.sample_rate),
                '-ac', str(self.channels),
                '-i', 'pipe:0',
            ]

        if shutil.which('aplay'):
            # Fall back to ALSA default device. This keeps output working even on
            # machines with non-deterministic card ordering.
            return ['aplay', '-D', 'default', '-q', '-f', 'S16_LE', '-r', str(self.sample_rate), '-c', str(self.channels), '-']

        if shutil.which('paplay'):
            return ['paplay', '--format=s16le', '--rate=' + str(self.sample_rate), '--channels=' + str(self.channels)]
        return None

    def _start_process(self):
        if self._cmd is None:
            return False
        try:
            self._proc = subprocess.Popen(
                self._cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                bufsize=0,
            )
            return True
        except Exception:
            self._proc = None
            return False

    def set_enabled(self, enabled):
        will_enable = bool(enabled)
        with self._lock:
            self.is_enabled = will_enable
        if not will_enable:
            self.stop()
        elif self._proc is None and self._cmd is not None:
            self._start_process()

    def feed(self, pcm_bytes):
        if not self.is_enabled or not pcm_bytes:
            return
        with self._lock:
            if self._cmd is None:
                return
            if self._proc is None and not self._start_process():
                return
            try:
                if self._proc is None or self._proc.stdin is None:
                    return

                # The raw PCM stream is continuous; partial writes are normal when
                # the sink buffers or blocks briefly. If we only count the first
                # fragment, the rest is silently dropped and the monitor sounds
                # choppy or stuttery. Retry until the whole payload is accepted.
                remaining = bytes(pcm_bytes)
                total_written = 0
                while remaining:
                    try:
                        written = self._proc.stdin.write(remaining)
                    except (BrokenPipeError, OSError):
                        try:
                            if self._proc is not None and self._proc.stdin is not None:
                                self._proc.stdin.close()
                        except Exception:
                            pass
                        self._proc = None
                        return

                    if written <= 0:
                        raise RuntimeError('audio monitor write returned 0 bytes')

                    total_written += written
                    remaining = remaining[written:]

                self._proc.stdin.flush()
                self.total_written += total_written
            except Exception:
                try:
                    if self._proc is not None and self._proc.stdin is not None:
                        self._proc.stdin.close()
                except Exception:
                    pass
                self._proc = None

    def stop(self):
        with self._lock:
            proc = self._proc
            self._proc = None
        if proc is not None:
            try:
                if proc.stdin is not None:
                    proc.stdin.close()
            except Exception:
                pass
            try:
                proc.terminate()
                proc.wait(timeout=1)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass



class MasterVolume:
    """Master gain stage for live audio output."""

    def __init__(self, initial_gain=1.0):
        self._gain = float(initial_gain)
        self._lock = threading.Lock()

    @property
    def gain(self):
        return self._gain

    @property
    def gain_db(self):
        import math
        return 20.0 * math.log10(max(self._gain, 1e-10))

    def set_gain(self, linear):
        with self._lock:
            self._gain = max(0.0, min(2.0, float(linear)))

    def set_gain_db(self, db):
        import math
        with self._lock:
            self._gain = 10.0 ** (float(db) / 20.0)
            self._gain = max(0.0, min(2.0, self._gain))

    def apply(self, pcm_bytes):
        if not pcm_bytes:
            return pcm_bytes
        with self._lock:
            gain = self._gain
        if gain == 1.0:
            return pcm_bytes
        import numpy as np
        samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
        samples *= gain
        return np.clip(samples, -32768, 32767).astype(np.int16).tobytes()


# ── Filter factory for live audio processing ──
# Maps filter name → filter class. Used by VisualizerFeed.set_filter_state()
# to instantiate filters requested by the browser UI.
_FILTER_CLASS_MAP = {
    'noise_cancellation': ('tools.filters.noise_cancellation', 'NoiseCancellationFilter'),
    'spectral_difference': ('tools.filters.spectral_difference', 'SpectralDifferenceFilter'),
    'voice_isolation': ('tools.filters.voice_isolation', 'VoiceIsolationFilter'),
    'voice_removal': ('tools.filters.voice_removal', 'VoiceRemovalFilter'),
    'ambient_removal': ('tools.filters.ambient_removal', 'AmbientRemovalFilter'),
    'compressor': ('tools.filters.compressor', 'CompressorFilter'),
    'agc': ('tools.filters.agc', 'AGCFilter'),
    'wind_noise': ('tools.filters.wind_noise', 'WindNoiseFilter'),
    'deesser': ('tools.filters.deesser', 'DeEsserFilter'),
    'equalizer': ('tools.filters.eq_filter', 'EqualizerFilter'),
    'feature_options': ('tools.filters.feature_options', 'FeatureOptionsFilter'),
}


def create_live_filter(name, sample_rate, params):
    """Create a live filter instance by name.

    Args:
        name: Filter name (e.g. 'noise_cancellation', 'compressor')
        sample_rate: Audio sample rate in Hz
        params: Dict of parameter name → value from the UI

    Returns:
        Filter instance or None if the filter is unknown or fails to instantiate.
    """
    entry = _FILTER_CLASS_MAP.get(name)
    if entry is None:
        print(f"[{datetime.now().isoformat()}] create_live_filter: unknown filter '{name}'")
        return None
    module_path, class_name = entry
    try:
        import importlib
        module = importlib.import_module(module_path)
        cls = getattr(module, class_name)
    except Exception as e:
        print(f"[{datetime.now().isoformat()}] create_live_filter: failed to import {module_path}.{class_name}: {e}")
        return None
    try:
        # Build kwargs from params, falling back to filter defaults
        kwargs = dict(params or {})
        # Special handling for equalizer: convert gains list to gains_db
        if name == 'equalizer':
            gains = kwargs.pop('gains', None)
            if gains is not None:
                kwargs['gains_db'] = gains
        return cls(sample_rate=sample_rate, **kwargs)
    except Exception as e:
        print(f"[{datetime.now().isoformat()}] create_live_filter: failed to create {name}: {e}")
        return None


class VisualizerFeed:
    """Owns the WebSocket server + a thread-safe frame queue.

    The receiver's worker thread calls feed_pcm() with each chunk; we buffer enough
    PCM for one VIZ_FRAME_SEC window, run VisualizerAnalyzer, and enqueue a frame.
    A separate asyncio thread drains the queue and broadcasts to all WS clients.
    """

    def __init__(self, host=HOST, port=VIZ_PORT, analyzer=None, live_processor=None):
        self.host = host
        self.port = port
        self.analyzer = analyzer or VisualizerAnalyzer(SAMPLE_RATE)
        self.audio_monitor = LiveAudioMonitor()
        self.master_volume = MasterVolume(initial_gain=1.0)
        self.live_processor = live_processor
        self._pcm_ring = bytearray()
        self._frame_samples = self.analyzer.frame_samples
        self._lock = threading.Lock()
        # Bridge between the receiver's worker thread (producer) and the asyncio
        # event loop (consumer). asyncio.Queue is awaitable; the producer pushes
        # via loop.call_soon_threadsafe so it never blocks the audio path or the loop.
        self._out_queue = None      # set in _run_server (asyncio.Queue belongs to a loop)
        self._clients = set()
        self._clients_lock = None   # asyncio.Lock, created in _run_server (loop-scoped)
        self.running = False
        self._loop = None
        # Global monitoring enable flag. Default to ON so the live audio path is
        # active immediately on startup; it can still be toggled off from the UI
        # or control channel when needed.
        self._monitoring_enabled = True
        # Auto-start audio monitor so speakers work without UI connection
        self.audio_monitor.set_enabled(True)
        # Per-graph manual enable flags (independent waveform / spectrogram
        # toggles driven from the web UI). Default both ON.
        self._wave_enabled = True
        self._spec_enabled = True
        # When paused (e.g. during heavy stem extraction that pegs CPU/RAM),
        # feed_pcm drops incoming PCM instead of analyzing/queuing frames, so
        # the waveform + spectrogram graphs effectively stop and the freed
        # CPU/RAM is available for extraction. Consumers keep their last frame.
        self._paused = False
        # Reference count so concurrent extractions keep graphs paused until the
        # last one finishes (see pause()/resume()).
        self._pause_count = 0
        # Extraction batch gate: when > 0 BOTH graphs are forced off for the
        # whole duration of a manual "Extract Now" run, regardless of the
        # manual toggle state. Refcounted so overlapping batches behave.
        self._extract_disable = 0
        self._filter_state = {
            'enabled': False,
            'filters': [],
            'params': {},
        }
        self._startup_monitoring_enabled = True

    def _advertised_host(self):
        """Return a host browsers can use from another machine."""
        if self.host not in ('0.0.0.0', '::', ''):
            return self.host
        try:
            import socket
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                probe.connect(('8.8.8.8', 80))
                return probe.getsockname()[0]
            finally:
                probe.close()
        except Exception:
            return 'localhost'


    # ── Called from the receiver's worker thread (real-time path) ──
    def feed_pcm(self, pcm_bytes):
        if not self._monitoring_enabled:
            with self._lock:
                self._pcm_ring.clear()
            return
        if self._paused:
            # Drop the audio; we intentionally stop feeding the two graphs so
            # extraction has the machine to itself. Clear any buffered partial
            # frame so we don't resume mid-window with stale data.
            with self._lock:
                self._pcm_ring.clear()
            return
        if self.live_processor is not None:
            try:
                pcm_bytes = self.live_processor.process(pcm_bytes)
            except Exception as e:
                print(f"[{datetime.now().isoformat()}] live processor error (ignored): {e}")
        # Feed live audio monitor (speakers) — must happen after filters so the
        # user hears the processed signal, not the raw input.
        if self.audio_monitor is not None and self.audio_monitor.is_enabled:
            try:
                # Apply master volume (live output only, not recordings)
                pcm_bytes = self.master_volume.apply(pcm_bytes)
                self.audio_monitor.feed(pcm_bytes)
            except Exception as e:
                print(f"[{datetime.now().isoformat()}] audio monitor feed error (ignored): {e}")
        with self._lock:
            # Resolve effective per-graph state: a graph draws only if it is
            # manually enabled AND not forced off by an active extraction batch.
            extract_off = self._extract_disable > 0
            self.analyzer.wave_enabled = self._wave_enabled and not extract_off
            self.analyzer.spec_enabled = self._spec_enabled and not extract_off
            self._pcm_ring.extend(pcm_bytes)
            needed = self._frame_samples * 2
            while len(self._pcm_ring) >= needed:
                frame_pcm = bytes(self._pcm_ring[:needed])
                del self._pcm_ring[:needed]
                sender_ts = time.time()  # capture as late as possible for accurate latency
                f = self.analyzer.analyze(frame_pcm, sender_ts)
                if f is not None:
                    self._enqueue(f)

    def _live_filter_names(self):
        try:
            from tools.filters.feature_options import get_all_filter_names
            return set(get_all_filter_names())
        except Exception as e:
            print(f"[{datetime.now().isoformat()}] filter registry unavailable: {e}")
            return set()

    def _filter_definitions(self):
        """Return filter definitions for the UI."""
        try:
            from tools.filters.feature_options import FILTER_PARAM_REGISTRY
            defs = {}
            for name, params in FILTER_PARAM_REGISTRY.items():
                defs[name] = {
                    'params': params,
                    'has_calibration': name == 'spectral_difference',
                }
            return defs
        except Exception as e:
            print(f"[{datetime.now().isoformat()}] filter definitions unavailable: {e}")
            return {}

    def set_filter_state(self, enabled, filters=None, params=None):
        """Apply the browser's live filter selection to LiveProcessor."""
        supported = self._live_filter_names()
        requested = list(filters or [])
        filter_names = [name for name in requested if name in supported]
        rejected = [name for name in requested if name not in supported]
        if rejected:
            print(f"[{datetime.now().isoformat()}] unsupported live filters ignored: {rejected}")
        
        # Parse flat param keys ("noise_cancellation.strength") into nested dicts
        # The UI sends flat keys, but create_live_filter expects nested dicts
        raw_params = dict(params or {})
        filter_params = {}
        for key, value in raw_params.items():
            if '.' in key:
                fname, pname = key.split('.', 1)
                if fname not in filter_params:
                    filter_params[fname] = {}
                filter_params[fname][pname] = value
            else:
                # Already nested or top-level
                filter_params[key] = value
        
        with self._lock:
            self._filter_state = {
                'enabled': bool(enabled),
                'filters': filter_names,
                'params': filter_params,
            }
        processor = self.live_processor
        if processor is None:
            print(f"[{datetime.now().isoformat()}] live filter update ignored: LiveProcessor unavailable")
            return False
        try:
            processor.clear_filters()
            for name in filter_names:
                filt_params = filter_params.get(name, {})
                filt = create_live_filter(name, self.analyzer.sample_rate, filt_params)
                if filt is not None:
                    processor.add_filter(filt)
                    print(f"[{datetime.now().isoformat()}] live filter '{name}' created with params: {filt_params}")
                else:
                    print(f"[{datetime.now().isoformat()}] live filter '{name}' failed to create")
            processor.set_active(bool(enabled))
            print(f"[{datetime.now().isoformat()}] live filters {'enabled' if enabled else 'disabled'}: {filter_names}")
            return True
        except Exception as e:
            print(f"[{datetime.now().isoformat()}] live filter update failed: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _handle_filter_cmd(self, msg):
        """Handle per-filter commands (reset, calibrate, enable, configure)."""
        cmd = msg.get('cmd', '')
        name = msg.get('filter', '')
        processor = self.live_processor
        if processor is None:
            return
        try:
            if cmd == 'reset':
                # Reset a specific filter to defaults
                for filt in processor.chain.filters:
                    if filt.__class__.__name__.lower().replace('filter', '') == name:
                        filt.reset()
                        print(f"[{datetime.now().isoformat()}] filter '{name}' reset")
                        break
            elif cmd == 'calibrate':
                # Trigger recalibration for spectral difference
                for filt in processor.chain.filters:
                    if filt.__class__.__name__.lower().replace('filter', '') == name:
                        if hasattr(filt, 'recalibrate'):
                            filt.recalibrate()
                            print(f"[{datetime.now().isoformat()}] filter '{name}' recalibrating")
                        break
            elif cmd == 'enable':
                # Enable/disable a specific filter
                enabled = bool(msg.get('enabled', True))
                for filt in processor.chain.filters:
                    if filt.__class__.__name__.lower().replace('filter', '') == name:
                        filt.enabled = enabled
                        print(f"[{datetime.now().isoformat()}] filter '{name}' {'enabled' if enabled else 'disabled'}")
                        break
            elif cmd == 'configure':
                # Configure parameters for a specific filter
                params = msg.get('params', {})
                for filt in processor.chain.filters:
                    if filt.__class__.__name__.lower().replace('filter', '') == name:
                        filt.configure(**params)
                        print(f"[{datetime.now().isoformat()}] filter '{name}' configured: {params}")
                        break
            else:
                print(f"[{datetime.now().isoformat()}] unknown filter_cmd: {cmd}")
        except Exception as e:
            print(f"[{datetime.now().isoformat()}] filter_cmd error: {e}")

    def filter_state(self):
        with self._lock:
            state = dict(self._filter_state)
            # Add per-filter details
            if self.live_processor is not None:
                filters = []
                for filt in self.live_processor.chain.filters:
                    fstate = {
                        'name': filt.__class__.__name__.lower().replace('filter', ''),
                        'enabled': filt.enabled,
                        'class': filt.__class__.__name__,
                    }
                    # Add calibration state for spectral difference
                    if hasattr(filt, 'state'):
                        fstate['calibration'] = filt.state
                    filters.append(fstate)
                state['active_filters'] = filters
            return state

    def _handle_volume_cmd(self, msg):
        """Handle volume control commands."""
        cmd = msg.get('cmd', '')
        if cmd == 'set_gain':
            gain = float(msg.get('gain', 1.0))
            self.master_volume.set_gain(gain)
        elif cmd == 'set_gain_db':
            db = float(msg.get('db', 0.0))
            self.master_volume.set_gain_db(db)
        elif cmd == 'mute':
            self.master_volume.set_gain(0.0)
        elif cmd == 'unmute':
            self.master_volume.set_gain(1.0)

    def _broadcast_filter_state(self, state):
        loop = self._loop
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(self._out_queue.put_nowait, {
                'type': 'filter_state',
                'state': state,
            })
        except Exception as e:
            print(f"[{datetime.now().isoformat()}] filter_state enqueue failed: {e}")

    def set_monitoring_enabled(self, enabled):
        """Globally enable or disable the live visual + audio monitor feed."""
        with self._lock:
            self._monitoring_enabled = bool(enabled)
            if not self._monitoring_enabled:
                self._pcm_ring.clear()
            self.audio_monitor.set_enabled(self._monitoring_enabled)
            print(f"[{datetime.now().isoformat()}] monitoring {'enabled' if self._monitoring_enabled else 'disabled'}")

    def set_graph_enabled(self, graph, enabled):
        """Manually enable/disable one graph ('wave' or 'spec') from the UI."""
        with self._lock:
            if graph == 'wave':
                self._wave_enabled = bool(enabled)
            elif graph == 'spec':
                self._spec_enabled = bool(enabled)
            else:
                print(f"[{datetime.now().isoformat()}] set_graph_enabled: unknown graph '{graph}'")
                return
            state = 'enabled' if enabled else 'disabled'
            print(f"[{datetime.now().isoformat()}] graph '{graph}' {state} (manual)")

    def graph_states(self):
        """Return the effective + manual graph states (for /health + UI)."""
        with self._lock:
            extract_off = self._extract_disable > 0
            return {
                "monitoring": self._monitoring_enabled,
                "wave_manual": self._wave_enabled,
                "spec_manual": self._spec_enabled,
                "extract_disabled": extract_off,
                "wave_active": self._monitoring_enabled and self._wave_enabled and not extract_off,
                "spec_active": self._monitoring_enabled and self._spec_enabled and not extract_off,
                "paused": self._paused,
            }

    def pause(self):
        """Stop feeding the visualizer graphs (waveform + spectrogram).

        Safe to call from any thread. Reference-counted so concurrent extractions
        (or a manual "Extract Now" batch) keep the graphs paused until the LAST
        caller resumes. Frees CPU/RAM during extraction; the browser keeps
        displaying its last received frame until resume().
        """
        with self._lock:
            self._pause_count += 1
            if self._pause_count == 1 and self.running:
                self._paused = True
                print(f"[{datetime.now().isoformat()}] viz_feed paused (graphs stopped)")

    def resume(self):
        """Resume feeding the visualizer graphs after a pause()."""
        with self._lock:
            if self._pause_count > 0:
                self._pause_count -= 1
            if self._pause_count == 0:
                self._pcm_ring.clear()
                self._paused = False
                print(f"[{datetime.now().isoformat()}] viz_feed resumed (graphs running)")

    def disable_graphs_for_extraction(self):
        """Force BOTH graphs off for the duration of an extraction batch.

        Refcounted. Re-enable with enable_graphs_for_extraction(); graphs return
        to their manual toggle state once the last batch finishes.
        """
        with self._lock:
            self._extract_disable += 1
            if self._extract_disable == 1:
                print(f"[{datetime.now().isoformat()}] graphs disabled for extraction batch")

    def enable_graphs_for_extraction(self):
        """Release one extraction-batch disable. Restores manual toggle state."""
        with self._lock:
            if self._extract_disable > 0:
                self._extract_disable -= 1
            if self._extract_disable == 0:
                self._pcm_ring.clear()
                print(f"[{datetime.now().isoformat()}] graphs re-enabled after extraction batch")

    def _enqueue(self, frame):
        """Thread-safe push into the asyncio queue. Never blocks the audio path."""
        loop = self._loop
        if loop is None or self._out_queue is None:
            return
        try:
            # Drop if full rather than stalls the producer.
            if self._out_queue.full():
                try:
                    self._out_queue.get_nowait()
                except Exception as e:
                    print(f"[{datetime.now().isoformat()}] viz_enqueue: failed to drain full queue: {e}")
            loop.call_soon_threadsafe(self._out_queue.put_nowait, frame)
        except Exception as e:
            print(f"[{datetime.now().isoformat()}] viz_enqueue: dropped frame: {e}")

    # ── WebSocket server (own thread) ──
    def start(self):
        self.running = True
        t = threading.Thread(target=self._run_server, daemon=True)
        t.start()

    def _run_server(self):
        try:
            import asyncio
            import websockets
        except ImportError:
            print(f"[{datetime.now().isoformat()}] VisualizerFeed disabled: "
                  f"need 'websockets' + 'asyncio'")
            return
        import json as _json

        self._clients_lock = asyncio.Lock()

        # websockets 15+ passes the request path to the handler. Keep a
        # path argument so the feed works with both old and new releases.
        async def handler(ws, path=None):
            async with self._clients_lock:
                self._clients.add(ws)
            try:
                # Send a one-time config hello so the client knows the layout.
                await ws.send(_json.dumps({
                    "type": "config",
                    "sample_rate": SAMPLE_RATE,
                    "frame_sec": VIZ_FRAME_SEC,
                    "waveform_points": VIZ_WAVEFORM_POINTS,
                    "spectrogram_bins": VIZ_SPECTROGRAM_BINS,
                    "budget_ms": VIZ_MAX_LATENCY_MS,
                    "presets": sorted(self.analyzer.PRESETS.keys()),
                    "preset": self.analyzer.preset,
                    "monitoring": self._monitoring_enabled,
                    "filters": self.filter_state(),
                    "filter_definitions": self._filter_definitions(),
                    "volume": {
                        "gain": self.master_volume.gain,
                        "gain_db": self.master_volume.gain_db,
                    },
                    "source_ip": self._advertised_host(),
                    # Initial graph toggle states so the UI matches the server.
                    "graph_states": self.graph_states(),
                }))
                # Accept client messages (e.g. preset changes) while frames are pushed
                # by the broadcast loop.
                async for raw in ws:
                    try:
                        msg = _json.loads(raw)
                    except Exception as e:
                        print(f"[{datetime.now().isoformat()}] viz_handler: bad msg: {e}")
                        continue
                    if isinstance(msg, dict):
                        if msg.get('type') == 'preset':
                            preset = str(msg.get('preset', '')).lower()
                            if preset in self.analyzer.PRESETS:
                                self.analyzer.set_preset(preset)
                        elif msg.get('type') == 'monitor':
                            self.set_monitoring_enabled(bool(msg.get('enabled', True)))
                        elif msg.get('type') == 'graph':
                            # Manual per-graph enable/disable toggle from the UI.
                            graph = str(msg.get('graph', ''))
                            enabled = bool(msg.get('enabled', True))
                            if graph in ('wave', 'spec'):
                                self.set_graph_enabled(graph, enabled)
                            else:
                                print(f"[{datetime.now().isoformat()}] viz_handler: "
                                      f"bad graph '{graph}'")
                        elif msg.get('type') == 'tools':
                            self.set_filter_state(
                                bool(msg.get('enabled', False)),
                                msg.get('filters', []),
                                msg.get('params', {}),
                            )
                        elif msg.get('type') == 'tools_cmd' and msg.get('cmd') == 'refresh':
                            self._broadcast_filter_state(self.filter_state())
                        elif msg.get('type') == 'filter_cmd':
                            self._handle_filter_cmd(msg)
                        elif msg.get('type') == 'volume':
                            self._handle_volume_cmd(msg)
            except Exception as e:
                print(f"[{datetime.now().isoformat()}] viz_handler: connection error: {e}")
            finally:
                async with self._clients_lock:
                    self._clients.discard(ws)

        async def broadcast_loop():
            while self.running:
                try:
                    f = await asyncio.wait_for(self._out_queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    print(f"[{datetime.now().isoformat()}] viz_broadcast: queue get failed: {e}")
                    await asyncio.sleep(0.01)
                    continue
                msg = _json.dumps(f)
                async with self._clients_lock:
                    dead = []
                    for ws in list(self._clients):
                        try:
                            await ws.send(msg)
                        except Exception as e:
                            print(f"[{datetime.now().isoformat()}] viz_broadcast: ws send failed: {e}")
                            dead.append(ws)
                    for ws in dead:
                        self._clients.discard(ws)

        async def main_async():
            self._loop = asyncio.get_running_loop()
            self._out_queue = asyncio.Queue(maxsize=30)  # ~0.6 s of frames of headroom
            # Keep the WS connection alive while the browser is idle. The earlier
            # attempt to disable pings caused the socket to be silently dropped by
            # intermediaries/NATs, which surfaced as abrupt 1005/1006 closes even
            # though the app itself was still healthy. Keep a moderate heartbeat so
            # dead peers are detected and reconnected without breaking the stream.
            # Bind explicitly to all interfaces when configured that way. This
            # is required for browsers running on another machine; localhost-only
            # binds make the WebSocket appear reachable while connections fail.
            bind_host = '0.0.0.0' if self.host in ('0.0.0.0', '::') else self.host
            async with websockets.serve(
                handler, bind_host, self.port,
                max_size=2 ** 20,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=10,
            ):
                print(f"Visualizer WebSocket listening on {self.host}:{self.port} "
                      f"(budget={VIZ_MAX_LATENCY_MS:.0f}ms)")
                await broadcast_loop()

        try:
            asyncio.run(main_async())
        except Exception as e:
            print(f"[{datetime.now().isoformat()}] VisualizerFeed server error: {e}")

    def stop(self):
        self.running = False


class AudioReceiver:
    def __init__(self, host=HOST, port=PORT, config_path=CONFIG_PATH, control_port=CONTROL_PORT,
                 viz_port=VIZ_PORT):
        self.host = host
        self.port = port
        self.control_port = control_port
        self.config_path = config_path
        self.output_dir = OUTPUT_DIR
        os.makedirs(self.output_dir, exist_ok=True)
        self.storage = StorageManager(self.output_dir, MAX_STORAGE_SECONDS)

        self.ring_buffer = bytearray()

        # Noise suppression for recorded audio (make saved files cleaner)
        self.noise_suppressor = None
        try:
            from noise_suppression import SpectralGateSuppressor
            self.noise_suppressor = SpectralGateSuppressor(SAMPLE_RATE)
            print(f"[{datetime.now().isoformat()}] Noise suppression enabled (SpectralGate)")
        except Exception as e:
            print(f"[{datetime.now().isoformat()}] Noise suppression disabled: {e}")
        self.bytes_since_last_segment = 0
        self.segment_index = 0
        self.running = False
        self.server_socket = None
        self.control_socket = None
        self.clients = set()
        self.lock = threading.Lock()
        self.config_lock = threading.Lock()
        self._last_config_hash = None
        self.breathing_detector = BreathingDetector(SAMPLE_RATE) if BREATHING_ENABLE else None

        # Live audio processor (filter chain for monitoring + recording)
        self.live_processor = None
        try:
            from tools.live.live_processor import LiveProcessor
            self.live_processor = LiveProcessor(sample_rate=SAMPLE_RATE)
            print(f"[{datetime.now().isoformat()}] LiveProcessor initialized")
        except Exception as e:
            print(f"[{datetime.now().isoformat()}] LiveProcessor disabled: {e}")

        # Real-time visualization feed (Waveform + Spectrogram over WebSocket)
        self.viz_port = viz_port
        self.viz_feed = None
        try:
            self.viz_feed = VisualizerFeed(
                host=host,
                port=viz_port,
                analyzer=VisualizerAnalyzer(SAMPLE_RATE),
                live_processor=self.live_processor,
            )
            print(f"[{datetime.now().isoformat()}] VisualizerFeed initialized")
        except Exception as e:
            print(f"[{datetime.now().isoformat()}] VisualizerFeed disabled: {e}")
        self._control_clients = set()

        # HTTP stream buffer for /stream endpoint
        self.stream_buffer = StreamBuffer(max_seconds=2, sample_rate=SAMPLE_RATE)
        # WAV segment config
        self._wav_output_dir = OUTPUT_DIR
        os.makedirs(self._wav_output_dir, exist_ok=True)

        # Stem extraction manager (capped rotation, CPU-only by default)
        stem_dir = os.path.join(os.path.dirname(self._wav_output_dir), "audio_stems")
        self.stem_manager = None
        self.stem_extraction_enabled = False
        # Manual "Extract Now" batch gating: counts in-flight batch segments so
        # the graphs stay OFF until the whole batch completes, then restores.
        self._extract_batch_remaining = 0
        self._extract_batch_lock = threading.Lock()
        # Segment save queue + background saver thread so FLAC encoding
        # never blocks the real-time audio path.
        self._segment_save_queue = queue_module.Queue(maxsize=8)
        self._segment_saver_thread = None
        try:
            from stem_manager import StemManager
            self.stem_manager = StemManager(
                stem_dir, max_seconds=1800,
                viz_feed=self.viz_feed if self.viz_feed is not None else None,
            )
            self.stem_extraction_enabled = True
            print(f"[stem] StemManager initialized: dir={stem_dir}")
        except Exception as e:
            print(f"[stem] StemManager disabled: {e}")

        # Dynamic segment config
        self.segment_duration_sec = SEGMENT_DURATION_SEC
        self.overlap_duration_sec = OVERLAP_DURATION_SEC
        self._recalculate_segment_bytes()

        # Load persisted settings at startup (fail-safe: missing/corrupt -> defaults)
        self._startup_monitoring_enabled = True

    def _advertised_host(self):
        """Return a host browsers can use from another machine."""
        if self.host not in ('0.0.0.0', '::', ''):
            return self.host
        try:
            import socket
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                probe.connect(('8.8.8.8', 80))
                return probe.getsockname()[0]
            finally:
                probe.close()
        except Exception:
            return 'localhost'

        startup_cfg = load_config(self.config_path)
        if startup_cfg:
            self.apply_config_dict(startup_cfg, persist=False)
            self._last_config_hash = _config_hash(startup_cfg)
            print(f"[{datetime.now().isoformat()}] Loaded config from {self.config_path}: {startup_cfg}")

        # Apply stem backend config after startup config is loaded.
        if self.stem_manager is not None and startup_cfg:
            backend_cfg = startup_cfg.get('stem_backend')
            preset_cfg = startup_cfg.get('stem_backend_preset')
            if backend_cfg:
                self.stem_manager.backend = backend_cfg
                print(f"[stem] backend set to {backend_cfg} from config")
            if preset_cfg:
                self.stem_manager._backend_preset = preset_cfg
                print(f"[stem] backend preset set to {preset_cfg} from config")

    def connection_state(self):
        pcm = len(getattr(self, 'clients', set()))
        ctrl = len(getattr(self, '_control_clients', set()))
        viz = len(getattr(self.viz_feed, '_clients', set())) if self.viz_feed is not None else 0
        viz_paused = bool(getattr(self.viz_feed, '_paused', False)) if self.viz_feed is not None else False
        viz_disabled = bool(getattr(self.viz_feed, '_extract_disable', 0) > 0) if self.viz_feed is not None else False
        graph_states = self.viz_feed.graph_states() if self.viz_feed is not None else {}
        return {
            "pcm_clients": pcm,
            "control_clients": ctrl,
            "viz_clients": viz,
            "monitoring_enabled": bool(getattr(self.viz_feed, '_monitoring_enabled', True)) if self.viz_feed is not None else True,
            "filters": self.viz_feed.filter_state() if self.viz_feed is not None else {
                'enabled': False, 'filters': [], 'params': {},
            },
            "viz_paused": viz_paused,
            "viz_disabled": viz_disabled,
            "graph_states": graph_states,
            "android_connected": pcm > 0,
        }

    def _recalculate_segment_bytes(self):
        self.step_sec = max(1, self.segment_duration_sec - self.overlap_duration_sec)
        self.segment_bytes = self.segment_duration_sec * BYTES_PER_SECOND
        self.step_bytes = self.step_sec * BYTES_PER_SECOND

    def apply_config_dict(self, cfg, persist=False):
        """Apply a config dict live. Thread-safe. Optionally persist to config.json.

        Returns True if any setting actually changed, False otherwise. Never
        raises — but unexpected failures are logged loudly (with traceback) so
        a real bug is never silently swallowed as a "successful" no-op.
        """
        try:
            if not isinstance(cfg, dict):
                print(f"[{datetime.now().isoformat()}] apply_config_dict: caller passed "
                      f"non-dict ({type(cfg).__name__}); refusing to apply")
                return False
            with self.config_lock:
                changed = False
                if 'segment_duration_min' in cfg:
                    try:
                        val = int(cfg['segment_duration_min'])
                        val = max(1, min(val, 60)) * 60
                        if val != self.segment_duration_sec:
                            self.segment_duration_sec = val
                            self._recalculate_segment_bytes()
                            changed = True
                            print(f"[{datetime.now().isoformat()}] Updated segment duration to {val}s "
                                  f"(step={self.step_sec}s, segment_bytes={self.segment_bytes})")
                    except Exception as e:
                        print(f"[{datetime.now().isoformat()}] Bad segment_duration_min: {e}")

                if self.breathing_detector is not None:
                    bd = self.breathing_detector
                    if 'breathing_sensitivity' in cfg:
                        try:
                            val = float(cfg['breathing_sensitivity'])
                            threshold = 1e8 * pow(1e-5, val / 100.0)
                            bd.threshold = max(0.0, threshold)
                            changed = True
                            print(f"[{datetime.now().isoformat()}] Updated breathing threshold to "
                                  f"{threshold:.1f} (sensitivity={val:.1f})")
                        except Exception as e:
                            print(f"[{datetime.now().isoformat()}] Bad breathing_sensitivity: {e}")
                    if 'breathing_cooldown' in cfg:
                        try:
                            val = float(cfg['breathing_cooldown'])
                            bd.min_interval = max(0.1, val)
                            changed = True
                            print(f"[{datetime.now().isoformat()}] Updated breathing cooldown to {val}s")
                        except Exception as e:
                            print(f"[{datetime.now().isoformat()}] Bad breathing_cooldown: {e}")

                if changed and persist:
                    # Merge into existing persisted config so we don't clobber other keys.
                    merged = load_config(self.config_path)
                    merged.update(cfg)
                    if save_config(self.config_path, merged):
                        self._last_config_hash = _config_hash(merged)
                        print(f"[{datetime.now().isoformat()}] Persisted config to {self.config_path}")

                # Stem manager config
                if self.stem_manager is not None:
                    if 'stem_max_storage_seconds' in cfg:
                        try:
                            val = int(cfg['stem_max_storage_seconds'])
                            val = max(60, min(val, 86400))
                            self.stem_manager.max_seconds = val
                            changed = True
                            print(f"[{datetime.now().isoformat()}] Updated stem max storage to {val}s")
                        except Exception as e:
                            print(f"[{datetime.now().isoformat()}] Bad stem_max_storage_seconds: {e}")
                    if 'stem_backend' in cfg:
                        try:
                            self.stem_manager.backend = cfg['stem_backend']
                            changed = True
                            print(f"[{datetime.now().isoformat()}] Updated stem backend to "
                                  f"{self.stem_manager.backend}")
                        except Exception as e:
                            print(f"[{datetime.now().isoformat()}] Bad stem_backend: {e}")
                    if 'stem_backend_preset' in cfg:
                        try:
                            self.stem_manager._backend_preset = cfg['stem_backend_preset']
                            changed = True
                            print(f"[{datetime.now().isoformat()}] Updated stem backend preset to "
                                  f"{self.stem_manager._backend_preset}")
                        except Exception as e:
                            print(f"[{datetime.now().isoformat()}] Bad stem_backend_preset: {e}")

                # Visualizer graph toggles (independent wave / spec enable).
                if self.viz_feed is not None:
                    if 'monitoring_enabled' in cfg:
                        try:
                            self.viz_feed.set_monitoring_enabled(bool(cfg['monitoring_enabled']))
                            self._startup_monitoring_enabled = bool(cfg['monitoring_enabled'])
                            changed = True
                            print(f"[{datetime.now().isoformat()}] monitoring set to {bool(cfg['monitoring_enabled'])}")
                        except Exception as e:
                            print(f"[{datetime.now().isoformat()}] Bad monitoring_enabled: {e}")
                    if 'viz_wave_enabled' in cfg:
                        try:
                            self.viz_feed.set_graph_enabled('wave', bool(cfg['viz_wave_enabled']))
                            changed = True
                            print(f"[{datetime.now().isoformat()}] graph 'wave' "
                                  f"set to {bool(cfg['viz_wave_enabled'])}")
                        except Exception as e:
                            print(f"[{datetime.now().isoformat()}] Bad viz_wave_enabled: {e}")
                    if 'viz_spec_enabled' in cfg:
                        try:
                            self.viz_feed.set_graph_enabled('spec', bool(cfg['viz_spec_enabled']))
                            changed = True
                            print(f"[{datetime.now().isoformat()}] graph 'spec' "
                                  f"set to {bool(cfg['viz_spec_enabled'])}")
                        except Exception as e:
                            print(f"[{datetime.now().isoformat()}] Bad viz_spec_enabled: {e}")
            return changed
        except Exception as e:
            # Safety net only. A hit here means an unexpected bug, NOT a benign
            # no-op — log with traceback so it can't masquerade as success.
            import traceback
            print(f"[{datetime.now().isoformat()}] apply_config_dict UNEXPECTED ERROR: {e}")
            traceback.print_exc()
            return False

    def _save_segment(self, pcm_data):
        """Save segment as FLAC with noise suppression."""
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"segment_{ts}_{self.segment_index:04d}.flac"
        filepath = os.path.join(self._wav_output_dir, filename)

        try:
            # Apply noise suppression to make recorded audio cleaner
            processed = pcm_data
            if self.noise_suppressor is not None:
                try:
                    processed = self.noise_suppressor.process(bytes(pcm_data))
                except Exception as e:
                    print(f"[{datetime.now().isoformat()}] noise suppression failed: {e}")
                    processed = pcm_data

            # Stabilize: normalize levels to prevent clipping and ensure consistent volume
            import numpy as np
            audio_arr = np.frombuffer(processed, dtype=np.int16).astype(np.float32)
            peak = np.max(np.abs(audio_arr))
            if peak > 0:
                # Normalize to -3dB (prevents clipping while maintaining headroom)
                target = 32767 * 0.707  # -3dB
                audio_arr = (audio_arr * (target / peak)).astype(np.int16)
            processed = audio_arr.tobytes()

            # Save as FLAC (lossless compression, ~50% size reduction vs WAV)
            import soundfile as sf
            audio = np.frombuffer(processed, dtype=np.int16).astype(np.float32) / 32768.0
            sf.write(filepath, audio, SAMPLE_RATE, format='FLAC')
            self.storage.add_segment(filepath, self.segment_duration_sec)
            print(f"[{datetime.now().isoformat()}] Saved: {filename} "
                  f"({len(processed)} bytes, {self.segment_duration_sec}s)")

            # Async stem extraction — never block the audio path.
            if self.stem_manager is not None and self.stem_extraction_enabled:
                segment_id = os.path.splitext(filename)[0]
                threading.Thread(
                    target=self._extract_stems_async,
                    args=(segment_id, filepath),
                    daemon=True,
                ).start()

            self.segment_index += 1
        except Exception as e:
            print(f"Failed to save segment: {e}")

    def _extract_stems_async(self, segment_id, segment_path, batch=False):
        try:
            stems = self.stem_manager.extract(segment_id, segment_path)
            if stems:
                print(f"[stem] extracted {len(stems)} stems for {segment_id}: "
                      f"{', '.join(stems.keys())}")
            else:
                print(f"[stem] no stems extracted for {segment_id}")
        except Exception as e:
            print(f"[stem] async extraction failed for {segment_id}: {e}")
        finally:
            # If this was part of a manual "Extract Now" batch, release one slot
            # and re-enable the graphs once the batch is fully drained.
            if batch:
                with self._extract_batch_lock:
                    self._extract_batch_remaining -= 1
                    if self._extract_batch_remaining <= 0:
                        self._extract_batch_remaining = 0
                        if self.viz_feed is not None:
                            self.viz_feed.enable_graphs_for_extraction()

    # ── Stem service controls ───────────────────────────────────────────
    def toggle_stem_extraction(self, enabled=None):
        """Enable or disable automatic stem extraction.

        Returns the new state (bool).
        """
        if self.stem_manager is None:
            return self.stem_extraction_enabled
        if enabled is not None:
            self.stem_extraction_enabled = bool(enabled)
        else:
            self.stem_extraction_enabled = not self.stem_extraction_enabled
        state = 'enabled' if self.stem_extraction_enabled else 'disabled'
        print(f"[stem] automatic extraction {state}")
        return self.stem_extraction_enabled

    def extract_all_stems(self):
        """Manually trigger stem extraction for every segment on disk.

        While the batch runs, BOTH visualizer graphs are forced off (per
        KODE.md) so the CPU/RAM is fully available to the models; they are
        re-enabled automatically when the last segment finishes. Returns the
        number of segments queued.
        """
        if self.stem_manager is None:
            return 0
        count = 0
        for filename in sorted(os.listdir(self._wav_output_dir)):
            if not filename.endswith('.wav'):
                continue
            segment_id = os.path.splitext(filename)[0]
            segment_path = os.path.join(self._wav_output_dir, filename)
            if not os.path.exists(segment_path):
                continue
            # Skip if we already have stems for this segment.
            existing = self.stem_manager.list_stems(segment_id)
            if existing:
                continue
            threading.Thread(
                target=self._extract_stems_async,
                args=(segment_id, segment_path, True),
                daemon=True,
            ).start()
            count += 1
        if count > 0:
            with self._extract_batch_lock:
                self._extract_batch_remaining += count
            if self.viz_feed is not None:
                self.viz_feed.disable_graphs_for_extraction()
            print(f"[stem] manual extraction queued for {count} segments")
        return count

    def clear_all_stems(self):
        """Remove all stored stems and reset the stem manager index.

        Returns the number of segments cleared.
        """
        if self.stem_manager is None:
            return 0
        count = len(self.stem_manager._entries)
        self.stem_manager._entries.clear()
        stem_dir = self.stem_manager.output_dir
        if os.path.isdir(stem_dir):
            for segment_id in os.listdir(stem_dir):
                self.stem_manager.remove(segment_id)
        print(f"[stem] cleared {count} segments")
        return count

    def _compute_rms(self, data):
        if not HAS_NUMPY or len(data) < 2:
            return 0.0
        audio = np.frombuffer(data, dtype=np.int16).astype(np.float64)
        if audio.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(audio ** 2)))

    def _process_audio(self, data):
        """Process one PCM chunk through live filters, monitor, and storage."""
        with self.lock:
            if self.live_processor is not None:
                try:
                    data = self.live_processor.process(data)
                except Exception as e:
                    print(f"[{datetime.now().isoformat()}] live processor error (ignored): {e}")

            self.ring_buffer.extend(data)
            # Feed stream buffer for /stream endpoint
            if hasattr(self, 'stream_buffer') and self.stream_buffer is not None:
                try:
                    self.stream_buffer.write(data)
                except Exception as e:
                    print(f"[{datetime.now().isoformat()}] stream_buffer write error: {e}")
            self.bytes_since_last_segment += len(data)

            # Breathing detection
            if self.breathing_detector is not None:
                detections = self.breathing_detector.feed(data)
                for ts, energy in (detections or []):
                    print(f"[{datetime.now().isoformat()}] BREATHING DETECTED energy={energy:.1f}")

            # Real-time visualization feed — never let it block the audio path.
            if self.viz_feed is not None:
                try:
                    self.viz_feed.feed_pcm(data)
                except Exception as e:
                    print(f"[{datetime.now().isoformat()}] viz_feed error (ignored): {e}")

            # Keep ring buffer bounded to 1 segment + a small safety margin
            max_buffer = self.segment_bytes + self.step_bytes
            if len(self.ring_buffer) > max_buffer:
                excess = len(self.ring_buffer) - max_buffer
                del self.ring_buffer[:excess]

            # Enqueue segments for background saving so FLAC encoding
            # never blocks the real-time audio path.
            while self.bytes_since_last_segment >= self.step_bytes:
                if len(self.ring_buffer) >= self.segment_bytes:
                    segment_pcm = bytes(self.ring_buffer[-self.segment_bytes:])
                    self._enqueue_segment_save(segment_pcm)
                    self.bytes_since_last_segment -= self.step_bytes
                else:
                    # Not enough buffered yet (shouldn't happen in steady state)
                    break

    def _handle_client(self, conn, addr):
        print(f"Client connected: {addr}")
        self.clients.add(conn)
        try:
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 131072)
            conn.settimeout(0.5)
            # Read optional initial config line (returns audio bytes that arrived
            # coalesced after the config newline so we never drop leading PCM).
            config, leftover = self._read_config(conn)
            if config is not None:
                # _read_config already returns a parsed dict; apply it directly
                # instead of re-parsing (which would raise TypeError). Report the
                # REAL outcome — never claim success when nothing changed/failed.
                changed = self.apply_config_dict(config, persist=True)
                if changed:
                    print(f"[{datetime.now().isoformat()}] Applied config from {addr}: {config}")
                else:
                    print(f"[{datetime.now().isoformat()}] Config from {addr} had no effect "
                          f"(unknown/empty keys or apply failed): {config}")

            queue = queue_module.Queue(maxsize=32)
            worker = threading.Thread(target=self._process_queue, args=(queue,), daemon=True)
            worker.start()

            def queue_chunk(data):
                # Do not discard the first PCM burst during the initial connect.
                # Prefer bounded backpressure over silent loss so the audio stream
                # does not start with a gap when the client connects and sends the
                # config line + first audio bytes in one packet.
                if not data:
                    return
                while self.running:
                    try:
                        queue.put(data, timeout=0.1)
                        return
                    except queue_module.Full:
                        continue

            # If any PCM bytes arrived together with the config line, queue them first.
            if leftover:
                queue_chunk(leftover)

            # Receive audio stream
            while self.running:
                try:
                    chunk = conn.recv(65536)
                except socket.timeout:
                    continue
                except Exception as e:
                    print(f"[{datetime.now().isoformat()}] Client {addr} recv error: {e}")
                    break
                if not chunk:
                    break
                if self.bytes_since_last_segment % (BYTES_PER_SECOND * 5) < len(chunk):
                    print(f"[{datetime.now().isoformat()}] recv={len(chunk)} bytes")
                queue_chunk(chunk)
        except Exception as e:
            print(f"[{datetime.now().isoformat()}] Client {addr} error: {e}")
        finally:
            try:
                conn.close()
            except Exception as e:
                print(f"[{datetime.now().isoformat()}] Client {addr} close failed: {e}")
            self.clients.discard(conn)
            print(f"Client disconnected: {addr}")

    def _read_config(self, conn):
        """Read an optional newline-terminated JSON config from the client.

        Returns (config_dict_or_None, leftover_bytes).
        - config is parsed only if a non-empty JSON line preceded the first '\\n'.
        - leftover_bytes are any bytes received AFTER the '\\n' (e.g. the start of
          the PCM stream that arrived coalesced in the same TCP segment). The caller
          MUST feed these into the audio pipeline — otherwise leading PCM is lost.
        - If the connection closes before any '\\n' (or no config is sent at all),
          returns (None, b'') and the client is treated as "raw PCM only".
        """
        buffer = bytearray()
        while True:
            try:
                chunk = conn.recv(1024)
            except Exception as e:
                print(f"[{datetime.now().isoformat()}] read_config: recv failed: {e}")
                return (None, b'')
            if not chunk:
                # Client sent no config line at all (raw PCM from the start).
                return (None, b'')
            idx = chunk.find(b'\n')
            if idx >= 0:
                head = bytes(buffer) + chunk[:idx]
                leftover = bytes(chunk[idx + 1:])
                text = head.decode('utf-8', errors='replace').strip()
                if not text:
                    # Empty line (e.g. leading newline) -> no config, but keep leftovers.
                    return (None, leftover)
                try:
                    return (json.loads(text), leftover)
                except Exception as e:
                    # Not valid JSON; treat as no config but preserve audio leftovers.
                    print(f"[{datetime.now().isoformat()}] Client config not valid JSON: {e} "
                          f"(ignoring, len={len(text)})")
                    return (None, leftover)
            buffer.extend(chunk)
            if len(buffer) > 4096:
                # No newline within 4 KB: assume the client streams raw PCM with no
                # config line. Hand the buffered bytes back as audio, no config.
                return (None, bytes(buffer))

    def _apply_client_config(self, raw_json, persist=False):
        """Parse a JSON config line from a client and apply it live.

        Returns True if a setting changed. Empty input and parse errors are
        reported (not swallowed) so the control channel can't silently drop a
        config push.
        """
        if not raw_json or not str(raw_json).strip():
            return False
        try:
            config = json.loads(raw_json)
            return self.apply_config_dict(config, persist=persist)
        except Exception as e:
            print(f"[{datetime.now().isoformat()}] Failed to apply client config: {e}")
            return False

    def _process_queue(self, q):
        """Worker thread: drain audio queue and process chunks."""
        while self.running:
            try:
                chunk = q.get(timeout=0.1)
            except queue_module.Empty:
                continue
            except Exception as e:
                print(f"[{datetime.now().isoformat()}] process_queue: get failed: {e}")
                continue
            try:
                self._process_audio(chunk)
            except Exception as e:
                print(f"Worker error: {e}")

    def _enqueue_segment_save(self, segment_pcm):
        """Enqueue a segment PCM for background saving. Drops if queue full."""
        try:
            self._segment_save_queue.put_nowait(segment_pcm)
        except queue_module.Full:
            print(f"[{datetime.now().isoformat()}] segment save queue full — dropping segment")

    def _segment_saver_loop(self):
        """Background thread: drain segment save queue and save FLAC files."""
        while self.running:
            try:
                pcm_data = self._segment_save_queue.get(timeout=0.5)
            except queue_module.Empty:
                continue
            except Exception as e:
                print(f"[{datetime.now().isoformat()}] segment saver get failed: {e}")
                continue
            if pcm_data is None:
                # Poison pill — exit
                break
            try:
                self._save_segment(pcm_data)
            except Exception as e:
                print(f"[{datetime.now().isoformat()}] segment saver error: {e}")

    def _handle_control_client(self, conn, addr):
        """Receive live JSON config lines on the control port and apply + persist them.

        Bulletproof: never lets a malformed line kill the thread or slow the receiver.
        """
        print(f"Control client connected: {addr}")
        self._control_clients.add(conn)
        try:
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            conn.settimeout(0.5)
            buffer = bytearray()
            while self.running:
                try:
                    chunk = conn.recv(4096)
                except socket.timeout:
                    continue
                except Exception as e:
                    print(f"[{datetime.now().isoformat()}] Control client {addr} recv error: {e}")
                    break
                if not chunk:
                    break
                buffer.extend(chunk)
                while True:
                    idx = buffer.find(b'\n')
                    if idx < 0:
                        break
                    raw = bytes(buffer[:idx])
                    del buffer[:idx + 1]
                    try:
                        line = raw.decode('utf-8', errors='replace')
                    except Exception as e:
                        print(f"[{datetime.now().isoformat()}] control: decode failed: {e}")
                        continue
                    if not line.strip():
                        continue
                    # Log the received config line (sanitized to ASCII so the
                    # unified log never becomes binary/un-grep-able if a client
                    # sends non-text bytes). Keep enough to debug bad clients.
                    safe = ''.join(ch if 32 <= ord(ch) < 127 else '?' for ch in line[:200])
                    print(f"[{datetime.now().isoformat()}] Control rx from {addr}: {safe}")
                    changed = self._apply_client_config(line, persist=True)
                    if not changed:
                        print(f"[{datetime.now().isoformat()}] Control config from {addr} had no "
                              f"effect (unknown/empty keys or parse/apply failed): {safe}")
        except Exception as e:
            print(f"[{datetime.now().isoformat()}] Control client {addr} error: {e}")
        finally:
            self._control_clients.discard(conn)
            try:
                conn.close()
            except Exception as e:
                print(f"[{datetime.now().isoformat()}] Control client {addr} close failed: {e}")
            print(f"Control client disconnected: {addr}")

    def _control_loop(self):
        """Accept live config pushes on the control port."""
        try:
            self.control_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.control_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.control_socket.bind((self.host, self.control_port))
            self.control_socket.listen(5)
            self.control_socket.settimeout(0.5)
            print(f"Control (live config) listening on {self.host}:{self.control_port}")
        except Exception as e:
            print(f"[{datetime.now().isoformat()}] Could not start control port {CONTROL_PORT}: {e}")
            return
        try:
            while self.running:
                try:
                    conn, addr = self.control_socket.accept()
                    t = threading.Thread(target=self._handle_control_client, args=(conn, addr), daemon=True)
                    t.start()
                except socket.timeout:
                    continue
                except OSError:
                    if not self.running:
                        break
        finally:
            try:
                self.control_socket.close()
            except Exception as e:
                print(f"[{datetime.now().isoformat()}] control_socket close failed: {e}")

    def _hot_reload_loop(self):
        """Periodically re-read config.json and apply external edits (fail-safe)."""
        while self.running:
            time.sleep(HOT_RELOAD_INTERVAL_SEC)
            if not self.running:
                break
            try:
                cfg = load_config(self.config_path)
                h = _config_hash(cfg)
                if h and h != self._last_config_hash:
                    self._last_config_hash = h
                    self.apply_config_dict(cfg, persist=False)
                    print(f"[{datetime.now().isoformat()}] Hot-reloaded config from {self.config_path}")
            except Exception as e:
                # Never let hot-reload crash the receiver.
                print(f"[{datetime.now().isoformat()}] hot_reload failed: {e}")

    def _advertised_host(self):
        """Return a host browsers can use from another machine."""
        if self.host not in ('0.0.0.0', '::', ''):
            return self.host
        try:
            import socket
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                # This does not send traffic; it only selects the local route.
                probe.connect(('8.8.8.8', 80))
                return probe.getsockname()[0]
            finally:
                probe.close()
        except Exception:
            return 'localhost'

    def _advertised_websocket_url(self):
        return 'ws://{}:{}'.format(self._advertised_host(), self.viz_port)

    def start(self):
        self.running = True
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((self.host, self.port))
        self.server_socket.listen(5)
        self.server_socket.settimeout(0.5)
        print(f"Audio receiver listening on {self.host}:{self.port}")
        print(f"Visualizer WebSocket: {self._advertised_websocket_url()}")
        print(f"Segment: {self.segment_duration_sec}s, Overlap: {self.overlap_duration_sec}s, Step: {self.step_sec}s")
        print(f"Max storage: {MAX_STORAGE_SECONDS}s (~1 hour)")
        print(f"Breathing detection: {'enabled' if self.breathing_detector else 'disabled'}")

        # Start the real-time monitor from the server process. The default
        # remains enabled even when config.json has no monitoring key.
        if self.viz_feed is not None and self._startup_monitoring_enabled:
            self.viz_feed.set_monitoring_enabled(True)

        # Real-time visualization WebSocket feed
        if self.viz_feed is not None:
            try:
                self.viz_feed.start()
            except Exception as e:
                print(f"[{datetime.now().isoformat()}] Could not start visualizer feed: {e}")

        # Live config push channel + hot reload of config.json edits
        threading.Thread(target=self._control_loop, daemon=True).start()
        threading.Thread(target=self._hot_reload_loop, daemon=True).start()
        # Background segment saver thread
        self._segment_saver_thread = threading.Thread(
            target=self._segment_saver_loop, daemon=True
        )
        self._segment_saver_thread.start()

        try:
            while self.running:
                try:
                    conn, addr = self.server_socket.accept()
                    t = threading.Thread(target=self._handle_client, args=(conn, addr), daemon=True)
                    t.start()
                except socket.timeout:
                    continue
                except OSError:
                    if not self.running:
                        break
        finally:
            self.stop()

    def stop(self):
        if getattr(self, '_stopped', False):
            return
        self._stopped = True
        self.running = False
        # Signal segment saver to drain and exit
        try:
            self._segment_save_queue.put_nowait(None)
        except queue_module.Full:
            pass
        for conn in list(self.clients):
            try:
                conn.close()
            except Exception as e:
                print(f"[{datetime.now().isoformat()}] stop: client close failed: {e}")
        if self.server_socket:
            try:
                self.server_socket.close()
            except Exception as e:
                print(f"[{datetime.now().isoformat()}] stop: server_socket close failed: {e}")
        if self.control_socket:
            try:
                self.control_socket.close()
            except Exception as e:
                print(f"[{datetime.now().isoformat()}] stop: control_socket close failed: {e}")
        if self.viz_feed is not None:
            try:
                self.viz_feed.stop()
            except Exception as e:
                print(f"[{datetime.now().isoformat()}] stop: viz_feed stop failed: {e}")
        print("Receiver stopped.")


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Silver Audio Receiver (legacy alias)')
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=None)
    parser.add_argument('--control-port', type=int, default=None)
    parser.add_argument('--viz-port', type=int, default=None)
    parser.add_argument('--no-viz', action='store_true')
    parser.add_argument('--config', default=CONFIG_PATH)
    parser.add_argument('--output-dir', default=OUTPUT_DIR)
    parser.add_argument('--no-breathing', action='store_true')
    args = parser.parse_args()

    # Resolve ports: explicit args honored if free, otherwise hunt within PORT_RANGE.
    try:
        pcm_port, control_port, viz_port = allocate_ports(
            host=args.host, rng=PORT_RANGE,
            pcm=args.port, control=args.control_port,
            viz=None if args.no_viz else args.viz_port)
    except RuntimeError as e:
        print(f"[{datetime.now().isoformat()}] Fatal: {e}")
        sys.exit(1)

    print(f"Resolved ports -> PCM:{pcm_port} control:{control_port} viz:{viz_port}")

    receiver = AudioReceiver(args.host, pcm_port, config_path=args.config,
                             control_port=control_port, viz_port=viz_port)
    receiver.output_dir = args.output_dir
    os.makedirs(args.output_dir, exist_ok=True)
    receiver.storage = StorageManager(args.output_dir, MAX_STORAGE_SECONDS)
    if args.no_breathing:
        receiver.breathing_detector = None
    if args.no_viz:
        receiver.viz_feed.running = False
        receiver.viz_feed = None

    try:
        receiver.start()
    except KeyboardInterrupt:
        print("\nShutting down...")
        receiver.stop()


if __name__ == '__main__':
    # Single entry point for the whole Silver stack is server/amp.py.
    # Running this module directly just delegates to it so there is exactly
    # one startup code path (receiver + web UI, one process, /health endpoint).
    import subprocess
    import shutil
    this_dir = os.path.dirname(os.path.abspath(__file__))
    amp_entry = os.path.join(this_dir, 'amp.py')
    py = shutil.which('python3') or shutil.which('python') or sys.executable
    try:
        sys.exit(subprocess.call([py, amp_entry] + sys.argv[1:]))
    except FileNotFoundError:
        print("ERROR: server/amp.py not found. Use `python3 server/amp.py` as the entry point.",
              file=sys.stderr)
        sys.exit(1)


class StreamBuffer:
    """Thread-safe ring buffer for streaming raw PCM to HTTP clients."""
    def __init__(self, max_seconds=2, sample_rate=44100):
        self.max_bytes = max_seconds * sample_rate * 2
        self._buffer = bytearray()
        self._lock = threading.Lock()
        self._event = threading.Event()

    def write(self, pcm_bytes):
        with self._lock:
            self._buffer.extend(pcm_bytes)
            if len(self._buffer) > self.max_bytes:
                del self._buffer[:len(self._buffer) - self.max_bytes]
            self._event.set()

    def read(self, timeout=0.5):
        self._event.wait(timeout)
        with self._lock:
            data = bytes(self._buffer)
            self._buffer.clear()
            self._event.clear()
            return data
