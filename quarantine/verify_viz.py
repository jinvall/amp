#!/usr/bin/env python3
"""Verification harness for the real-time visualization feed.

1. Starts the receiver (subprocess).
2. Connects a fake PCM source over TCP (44.1k/16-bit mono sine).
3. Connects a WebSocket client to the viz feed.
4. Reports frames received, fps, and observed latency (server ts -> now).
"""
import socket
import struct
import subprocess
import sys
import time
import threading
import json
import os
import signal

# Self-clean: kill any lingering receiver processes from prior runs (avoids port
# contention on the fixed control port). Uses /proc scan + os.kill (no pkill).
def _kill_lingering_receivers():
    for p in os.listdir('/proc'):
        if not p.isdigit():
            continue
        try:
            with open(f'/proc/{p}/cmdline', 'rb') as f:
                data = f.read()
        except Exception:
            continue
        if b'audio_receiver.py' in data:
            try:
                os.kill(int(p), signal.SIGKILL)
            except Exception:
                pass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'server'))
# import locally to reuse constants
import importlib.util
spec = importlib.util.spec_from_file_location("audio_receiver",
        os.path.join(os.path.dirname(__file__), "server", "audio_receiver.py"))
ar = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ar)

import asyncio
import websockets

HOST = '127.0.0.1'
SR = ar.SAMPLE_RATE


def free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind((HOST, 0))
    port = s.getsockname()[1]
    s.close()
    return port


# Auto-pick unused ports (incl. control) to avoid collisions with lingering processes.
_kill_lingering_receivers()
time.sleep(0.5)
PCM_PORT = free_port()
CTRL_PORT = free_port()
VIZ_PORT = free_port()

recv = subprocess.Popen([sys.executable, "server/audio_receiver.py",
                         "--host", "127.0.0.1", "--port", str(PCM_PORT),
                         "--control-port", str(CTRL_PORT),
                         "--viz-port", str(VIZ_PORT), "--no-breathing"],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

# Wait until the viz feed is confirmed listening (read stdout live).
viz_ready = False
deadline = time.time() + 10
while time.time() < deadline:
    line = recv.stdout.readline()
    if line == "":  # EOF -> process died
        print("RECEIVER DIED. REMAINING LOG:")
        print(recv.stdout.read())
        sys.exit(1)
    print("[recv]", line.rstrip())
    if "Visualizer WebSocket listening" in line:
        viz_ready = True
        break
if not viz_ready:
    print("VIZ FEED NEVER CAME UP")
    sys.exit(1)

# ── Raw probe: confirm the viz port is actually bound right now ──
probe = socket.socket()
try:
    probe.connect((HOST, VIZ_PORT))
    print(f"[probe] viz port {VIZ_PORT} OPEN")
    probe.close()
except Exception as e:
    print(f"[probe] viz port {VIZ_PORT} NOT OPEN: {e}")

# ── Fake PCM source ──
pcm_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
pcm_sock.connect((HOST, PCM_PORT))
pcm_sock.sendall(b'{"client":"verify"}\n')  # config line (ignored/no-op)

def pcm_producer():
    t = 0.0
    chunk_samples = 2048
    freq = 440.0
    while True:
        buf = bytearray()
        for i in range(chunk_samples):
            sample = int(30000 * __import__('math').sin(2*3.14159265*freq*(t + i/SR)))
            buf += struct.pack('<h', sample)
        try:
            pcm_sock.sendall(buf)
        except Exception:
            break
        t += chunk_samples / SR
        time.sleep(chunk_samples / SR * 0.9)  # ~real-time

threading.Thread(target=pcm_producer, daemon=True).start()

# ── WS consumer (with retry; reports a clear verdict, not a bare PASS) ──
frames = []
latencies = []

async def consume():
    last_err = None
    for attempt in range(5):
        try:
            async with websockets.connect(f"ws://{HOST}:{VIZ_PORT}") as ws:
                end = time.time() + 8
                while time.time() < end:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
                    except Exception:
                        continue
                    f = json.loads(msg)
                    if f.get("type") == "config":
                        print("config:", f)
                        continue
                    now = time.time()
                    lat = (now - f["t"]) * 1000.0
                    frames.append(now)
                    latencies.append(lat)
            return
        except Exception as e:
            last_err = e
            await asyncio.sleep(0.3)
    print(f"WS connect failed after retries: {last_err}")
    print("---- receiver log ----")
    print(recv.stdout.read())

asyncio.run(consume())

print("\n========== VISUALIZER VERIFICATION ==========")
if len(frames) >= 2:
    dur = frames[-1] - frames[0]
    fps = (len(frames) - 1) / dur if dur > 0 else 0
    print(f"Frames received : {len(frames)}")
    print(f"Stream duration : {dur:.2f} s")
    print(f"Effective FPS   : {fps:.1f} (target ~{1/ar.VIZ_FRAME_SEC:.0f})")
    print(f"Latency (server ts -> client):")
    print(f"  min  = {min(latencies):.1f} ms")
    print(f"  mean = {sum(latencies)/len(latencies):.1f} ms")
    print(f"  max  = {max(latencies):.1f} ms")
    print(f"Budget         : {ar.VIZ_MAX_LATENCY_MS:.0f} ms")
    within = max(latencies) <= ar.VIZ_MAX_LATENCY_MS
    print(f"VERDICT        : {'WITHIN BUDGET' if within else 'EXCEEDS BUDGET'}")
else:
    print("Frames received: 0")
    print("VERDICT        : NO DATA (feed not delivering)")
print("==============================================")

# Clean up the receiver subprocess.
recv.terminate()
try:
    recv.wait(timeout=3)
except Exception:
    recv.kill()

