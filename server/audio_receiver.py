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
PORT = 8080
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
MP3_BITRATE = 128  # kbps

# Breathing detection config
BREATHING_ENABLE = True
BREATHING_BAND_LOW_HZ = 100.0
BREATHING_BAND_HIGH_HZ = 3000.0
BREATHING_ENERGY_THRESHOLD = 1.2e3  # very sensitive for quiet sounds
BREATHING_MIN_INTERVAL_SEC = 2.0
BREATHING_WINDOW_SEC = 2.0
BREATHING_HOP_SEC = 0.5


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


class AudioReceiver:
    def __init__(self, host=HOST, port=PORT):
        self.host = host
        self.port = port
        self.output_dir = OUTPUT_DIR
        os.makedirs(self.output_dir, exist_ok=True)
        self.storage = StorageManager(self.output_dir, MAX_STORAGE_SECONDS)

        self.ring_buffer = bytearray()
        self.bytes_since_last_segment = 0
        self.segment_index = 0
        self.running = False
        self.server_socket = None
        self.clients = set()
        self.lock = threading.Lock()
        self.breathing_detector = BreathingDetector(SAMPLE_RATE) if BREATHING_ENABLE else None

        # Dynamic segment config
        self.segment_duration_sec = SEGMENT_DURATION_SEC
        self.overlap_duration_sec = OVERLAP_DURATION_SEC
        self._recalculate_segment_bytes()

    def _recalculate_segment_bytes(self):
        self.step_sec = max(1, self.segment_duration_sec - self.overlap_duration_sec)
        self.segment_bytes = self.segment_duration_sec * BYTES_PER_SECOND
        self.step_bytes = self.step_sec * BYTES_PER_SECOND

    def _encode_mp3(self, pcm_data):
        """Encode 16-bit mono PCM to MP3 bytes."""
        if not HAS_LAME:
            raise RuntimeError("lameenc not installed. Run: pip install lameenc")
        encoder = lameenc.Encoder()
        encoder.set_bit_rate(MP3_BITRATE)
        encoder.set_in_sample_rate(SAMPLE_RATE)
        encoder.set_channels(CHANNELS)
        encoder.set_quality(2)
        mp3 = encoder.encode(bytes(pcm_data))
        mp3 += encoder.flush()
        return mp3

    def _save_segment(self, pcm_data):
        """Encode and save a segment."""
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"segment_{ts}_{self.segment_index:04d}.mp3"
        filepath = os.path.join(self.output_dir, filename)

        try:
            mp3_data = self._encode_mp3(pcm_data)
            with open(filepath, 'wb') as f:
                f.write(mp3_data)
            self.storage.add_segment(filepath, self.segment_duration_sec)
            print(f"[{datetime.now().isoformat()}] Saved: {filename} "
                  f"({len(mp3_data)} bytes, {self.segment_duration_sec}s)")
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
            # Read initial config line
            config = self._read_config(conn)
            if config is not None:
                self._apply_client_config(config)
                print(f"[{datetime.now().isoformat()}] Applied config from {addr}: {config}")

            # Process audio stream
            while self.running:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                if self.bytes_since_last_segment % (BYTES_PER_SECOND * 5) < len(chunk):
                    print(f"[{datetime.now().isoformat()}] recv={len(chunk)} bytes")
                self._process_audio(chunk)
        except Exception as e:
            print(f"Client {addr} error: {e}")
        finally:
            conn.close()
            self.clients.discard(conn)
            print(f"Client disconnected: {addr}")

    def _read_config(self, conn):
        """Read a newline-terminated JSON config from the client."""
        buffer = bytearray()
        while True:
            chunk = conn.recv(1024)
            if not chunk:
                return None
            idx = chunk.find(b'\n')
            if idx >= 0:
                buffer.extend(chunk[:idx])
                return bytes(buffer).decode('utf-8')
            buffer.extend(chunk)
            if len(buffer) > 4096:
                return None

    def _apply_client_config(self, raw_json):
        try:
            import json
            config = json.loads(raw_json)
            if not isinstance(config, dict):
                return
            if 'segment_duration_min' in config:
                val = int(config['segment_duration_min'])
                # Convert minutes to seconds
                val = max(1, min(val, 60)) * 60
                self.segment_duration_sec = val
                self._recalculate_segment_bytes()
                print(f"[{datetime.now().isoformat()}] Updated segment duration to {val}s ({val/60:.0f} min) "
                      f"(step={self.step_sec}s, segment_bytes={self.segment_bytes})")
            if self.breathing_detector is not None:
                if 'breathing_sensitivity' in config:
                    val = float(config['breathing_sensitivity'])
                    # Map slider sensitivity 0-100 to threshold.
                    # Higher sensitivity = lower threshold = more detections.
                    threshold = 1e8 * pow(1e-5, val / 100.0)
                    self.breathing_detector.threshold = max(0.0, threshold)
                    print(f"[{datetime.now().isoformat()}] Updated breathing threshold to {threshold:.1f} (sensitivity={val:.1f})")
                if 'breathing_cooldown' in config:
                    val = float(config['breathing_cooldown'])
                    self.breathing_detector.min_interval = max(0.1, val)
                    print(f"[{datetime.now().isoformat()}] Updated breathing cooldown to {val}s")
        except Exception as e:
            print(f"[{datetime.now().isoformat()}] Failed to apply client config: {e}")

    def start(self):
        self.running = True
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((self.host, self.port))
        self.server_socket.listen(5)
        print(f"Audio receiver listening on {self.host}:{self.port}")
        print(f"Segment: {self.segment_duration_sec}s, Overlap: {self.overlap_duration_sec}s, Step: {self.step_sec}s")
        print(f"Max storage: {MAX_STORAGE_SECONDS}s (~1 hour)")
        print(f"Breathing detection: {'enabled' if self.breathing_detector else 'disabled'}")

        try:
            while self.running:
                try:
                    conn, addr = self.server_socket.accept()
                    t = threading.Thread(target=self._handle_client, args=(conn, addr), daemon=True)
                    t.start()
                except OSError:
                    if not self.running:
                        break
        finally:
            self.stop()

    def stop(self):
        self.running = False
        for conn in list(self.clients):
            try:
                conn.close()
            except Exception:
                pass
        if self.server_socket:
            try:
                self.server_socket.close()
            except Exception:
                pass
        print("Receiver stopped.")


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Silver Audio Receiver')
    parser.add_argument('--host', default='0.0.0.0', help='Bind host')
    parser.add_argument('--port', type=int, default=8080, help='Listen port')
    parser.add_argument('--output-dir', default=OUTPUT_DIR, help='Segment output directory')
    parser.add_argument('--no-breathing', action='store_true', help='Disable breathing detection')
    args = parser.parse_args()

    receiver = AudioReceiver(args.host, args.port)
    receiver.output_dir = args.output_dir
    os.makedirs(args.output_dir, exist_ok=True)
    receiver.storage = StorageManager(args.output_dir, MAX_STORAGE_SECONDS)
    if args.no_breathing:
        receiver.breathing_detector = None

    try:
        receiver.start()
    except KeyboardInterrupt:
        print("\nShutting down...")
        receiver.stop()


if __name__ == '__main__':
    main()
