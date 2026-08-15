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
OUTPUT_DIR = 'audio_segments'

# Breathing detection config (defaults; overridden by config.json / client)
BREATHING_ENABLE = True
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

    def __init__(self, output_dir, max_seconds):
        self.output_dir = output_dir
        self.max_seconds = max_seconds
        self.segments = []  # list of (filepath, duration_sec)

    def add_segment(self, filepath, duration_sec):
        self.segments.append((filepath, duration_sec))
        self._prune()

    def _prune(self):
        total = sum(d for _, d in self.segments)
        while total > self.max_seconds and self.segments:
            old_path, old_dur = self.segments.pop(0)
            try:
                os.remove(old_path)
                print(f"Pruned: {old_path} ({old_dur}s)")
            except OSError as e:
                print(f"Prune failed: {e}")
            total -= old_dur


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
        mag = np.abs(fft) ** 2
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

        # ── Waveform: downsample to VIZ_WAVEFORM_POINTS via min/max envelope ──
        n = audio.size
        step = max(1, n // VIZ_WAVEFORM_POINTS)
        wave = np.zeros(VIZ_WAVEFORM_POINTS, dtype=np.float32)
        for i in range(VIZ_WAVEFORM_POINTS):
            s = i * step
            e = min(s + step, n)
            wave[i] = float(np.mean(audio[s:e])) / 32768.0

        # ── RMS (normalized 0..1) ──
        rms = float(np.sqrt(np.mean(audio ** 2))) / 32768.0

        # ── Spectrogram: rfft -> magnitude -> aggregate to frequency bands ──
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
            "wave": [round(float(x), 5) for x in wave],
            "spec": [round(float(x), 5) for x in spec],
            "preset": self.preset,
        }


class VisualizerFeed:
    """Owns the WebSocket server + a thread-safe frame queue.

    The receiver's worker thread calls feed_pcm() with each chunk; we buffer enough
    PCM for one VIZ_FRAME_SEC window, run VisualizerAnalyzer, and enqueue a frame.
    A separate asyncio thread drains the queue and broadcasts to all WS clients.
    """

    def __init__(self, host=HOST, port=VIZ_PORT, analyzer=None):
        self.host = host
        self.port = port
        self.analyzer = analyzer or VisualizerAnalyzer(SAMPLE_RATE)
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

    # ── Called from the receiver's worker thread (real-time path) ──
    def feed_pcm(self, pcm_bytes):
        with self._lock:
            self._pcm_ring.extend(pcm_bytes)
            needed = self._frame_samples * 2
            while len(self._pcm_ring) >= needed:
                frame_pcm = bytes(self._pcm_ring[:needed])
                del self._pcm_ring[:needed]
                sender_ts = time.time()  # capture as late as possible for accurate latency
                f = self.analyzer.analyze(frame_pcm, sender_ts)
                if f is not None:
                    self._enqueue(f)

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

        async def handler(ws):
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
                }))
                # Accept client messages (e.g. preset changes) while frames are pushed
                # by the broadcast loop.
                async for raw in ws:
                    try:
                        msg = _json.loads(raw)
                    except Exception as e:
                        print(f"[{datetime.now().isoformat()}] viz_handler: bad msg: {e}")
                        continue
                    if isinstance(msg, dict) and msg.get('type') == 'preset':
                        preset = str(msg.get('preset', '')).lower()
                        if preset in self.analyzer.PRESETS:
                            self.analyzer.set_preset(preset)
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
            self._out_queue = asyncio.Queue(maxsize=120)  # ~2.4 s of frames of headroom
            async with websockets.serve(handler, self.host, self.port, max_size=2 ** 20):
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

        # Real-time visualization feed (Waveform + Spectrogram over WebSocket)
        self.viz_port = viz_port
        self.viz_feed = VisualizerFeed(host=host, port=viz_port,
                                       analyzer=VisualizerAnalyzer(SAMPLE_RATE))
        self._control_clients = set()

        # WAV segment config
        self._wav_output_dir = OUTPUT_DIR
        os.makedirs(self._wav_output_dir, exist_ok=True)

        # Dynamic segment config
        self.segment_duration_sec = SEGMENT_DURATION_SEC
        self.overlap_duration_sec = OVERLAP_DURATION_SEC
        self._recalculate_segment_bytes()

        # Load persisted settings at startup (fail-safe: missing/corrupt -> defaults)
        startup_cfg = load_config(self.config_path)
        if startup_cfg:
            self.apply_config_dict(startup_cfg, persist=False)
            self._last_config_hash = _config_hash(startup_cfg)
            print(f"[{datetime.now().isoformat()}] Loaded config from {self.config_path}: {startup_cfg}")

    def connection_state(self):
        pcm = len(getattr(self, 'clients', set()))
        ctrl = len(getattr(self, '_control_clients', set()))
        viz = len(getattr(self.viz_feed, '_clients', set())) if self.viz_feed is not None else 0
        return {
            "pcm_clients": pcm,
            "control_clients": ctrl,
            "viz_clients": viz,
            "android_connected": pcm > 0,
        }

    def _recalculate_segment_bytes(self):
        self.step_sec = max(1, self.segment_duration_sec - self.overlap_duration_sec)
        self.segment_bytes = self.segment_duration_sec * BYTES_PER_SECOND
        self.step_bytes = self.step_sec * BYTES_PER_SECOND

    def apply_config_dict(self, cfg, persist=False):
        """Apply a config dict live. Thread-safe. Optionally persist to config.json.

        Never raises — callers always get a safe no-op on bad input.
        """
        try:
            if not isinstance(cfg, dict):
                return
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
        except Exception as e:
            print(f"[{datetime.now().isoformat()}] apply_config_dict error (ignored): {e}")

    def _save_segment(self, pcm_data):
        """Save segment as WAV."""
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"segment_{ts}_{self.segment_index:04d}.wav"
        filepath = os.path.join(self._wav_output_dir, filename)

        try:
            with wave.open(filepath, 'wb') as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(SAMPLE_WIDTH)
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(bytes(pcm_data))
            self.storage.add_segment(filepath, self.segment_duration_sec)
            print(f"[{datetime.now().isoformat()}] Saved: {filename} "
                  f"({len(pcm_data)} bytes, {self.segment_duration_sec}s)")
            self.segment_index += 1
        except Exception as e:
            print(f"Failed to save segment: {e}")

    def _compute_rms(self, data):
        if not HAS_NUMPY or len(data) < 2:
            return 0.0
        audio = np.frombuffer(data, dtype=np.int16).astype(np.float64)
        if audio.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(audio ** 2)))

    def _process_audio(self, data):
        """Feed PCM data into ring buffer and trigger segment saves."""
        with self.lock:
            self.ring_buffer.extend(data)
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

            # Save segments as needed
            while self.bytes_since_last_segment >= self.step_bytes:
                if len(self.ring_buffer) >= self.segment_bytes:
                    segment_pcm = bytes(self.ring_buffer[-self.segment_bytes:])
                    self._save_segment(segment_pcm)
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
                self._apply_client_config(config, persist=True)
                print(f"[{datetime.now().isoformat()}] Applied config from {addr}: {config}")

            queue = queue_module.Queue(maxsize=50)
            worker = threading.Thread(target=self._process_queue, args=(queue,), daemon=True)
            worker.start()

            # If any PCM bytes arrived together with the config line, queue them first.
            if leftover:
                if not queue.full():
                    queue.put_nowait(leftover)
                else:
                    try:
                        queue.get_nowait()
                        queue.put_nowait(leftover)
                    except Exception as e:
                        print(f"[{datetime.now().isoformat()}] client_queue: dropped leftover PCM: {e}")

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
                if not queue.full():
                    queue.put_nowait(chunk)
                else:
                    try:
                        queue.get_nowait()
                        queue.put_nowait(chunk)
                    except Exception as e:
                        print(f"[{datetime.now().isoformat()}] client_queue: dropped PCM chunk: {e}")
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
        """Parse a JSON config line from a client and apply it live."""
        if not raw_json or not str(raw_json).strip():
            return
        try:
            config = json.loads(raw_json)
            self.apply_config_dict(config, persist=persist)
        except Exception as e:
            print(f"[{datetime.now().isoformat()}] Failed to apply client config: {e}")

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
                    # Log the actual bytes so we can debug bad clients (Android, etc.)
                    print(f"[{datetime.now().isoformat()}] Control rx from {addr}: {line[:200]}")
                    self._apply_client_config(line, persist=True)
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

    def start(self):
        self.running = True
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((self.host, self.port))
        self.server_socket.listen(5)
        self.server_socket.settimeout(0.5)
        print(f"Audio receiver listening on {self.host}:{self.port}")
        print(f"Segment: {self.segment_duration_sec}s, Overlap: {self.overlap_duration_sec}s, Step: {self.step_sec}s")
        print(f"Max storage: {MAX_STORAGE_SECONDS}s (~1 hour)")
        print(f"Breathing detection: {'enabled' if self.breathing_detector else 'disabled'}")

        # Real-time visualization WebSocket feed
        try:
            self.viz_feed.start()
        except Exception as e:
            print(f"[{datetime.now().isoformat()}] Could not start visualizer feed: {e}")

        # Live config push channel + hot reload of config.json edits
        threading.Thread(target=self._control_loop, daemon=True).start()
        threading.Thread(target=self._hot_reload_loop, daemon=True).start()

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

