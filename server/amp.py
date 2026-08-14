#!/usr/bin/env python3
"""Single entry point for the AMP Silver stack.

Starts the audio receiver (PCM/control/viz WebSocket) and the web visualizer in ONE
process — no shell background jobs, no orphaned children. Ports are auto-allocated in
8090..8099 unless overridden. A /health endpoint reports resolved ports and status.

Usage:
    python3 server/amp.py                      # receiver + web UI on :8093
    python3 server/amp.py --no-viz             # receiver only
    python3 server/amp.py --web-port 8093      # override web UI port
    python3 server/amp.py --pcm 8090 --control 8091 --viz 8092

For an always-on service use the systemd unit (server/amp-receiver.service), which
invokes this same entry point.
"""
import argparse
import json
import os
import signal
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import audio_receiver as ar                       # noqa: E402
sys.path.insert(0, os.path.join(HERE, '..', 'web'))
from serve import Handler as _WebHandler          # noqa: E402

from http.server import BaseHTTPRequestHandler  # noqa: E402
import socketserver                               # noqa: E402


class HealthHandler(_WebHandler):
    """Static file server + a /health JSON endpoint for probes."""
    _state = {}  # populated by main(): {pcm, control, viz, web, ready, viz_enabled}

    def do_GET(self):
        if self.path.split('?')[0] in ('/health', '/health/'):
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(HealthHandler._state).encode('utf-8'))
            return
        super().do_GET()

    def log_message(self, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description='AMP Silver stack (receiver + web UI)')
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--pcm', type=int, default=None, help='PCM port (default: hunt in 8090..8099)')
    parser.add_argument('--control', type=int, default=None, help='Control port (default: hunt)')
    parser.add_argument('--viz', type=int, default=None, help='Viz WebSocket port (default: hunt)')
    parser.add_argument('--web-port', type=int, default=8093, help='Web UI port (default 8093)')
    parser.add_argument('--no-viz', action='store_true', help='Disable the visualization WebSocket feed')
    parser.add_argument('--no-breathing', action='store_true', help='Disable breathing detection')
    parser.add_argument('--config', default=ar.CONFIG_PATH)
    parser.add_argument('--output-dir', default=ar.OUTPUT_DIR)
    args = parser.parse_args()

    # Resolve receiver ports within the safe range.
    try:
        pcm_port, control_port, viz_port = ar.allocate_ports(
            host=args.host, rng=ar.PORT_RANGE,
            pcm=args.pcm, control=args.control,
            viz=None if args.no_viz else args.viz)
    except RuntimeError as e:
        print(f"[{_ts()}] Fatal: {e}")
        sys.exit(1)

    print(f"Resolved ports -> PCM:{pcm_port} control:{control_port} viz:{viz_port} web:{args.web_port}")

    receiver = ar.AudioReceiver(args.host, pcm_port, config_path=args.config,
                                control_port=control_port, viz_port=viz_port)
    receiver.output_dir = args.output_dir
    os.makedirs(args.output_dir, exist_ok=True)
    receiver.storage = ar.StorageManager(args.output_dir, ar.MAX_STORAGE_SECONDS)
    if args.no_breathing:
        receiver.breathing_detector = None
    if args.no_viz:
        receiver.viz_feed.running = False
        receiver.viz_feed = None

    HealthHandler._state = {
        "pcm": pcm_port, "control": control_port, "viz": viz_port,
        "web": args.web_port, "ready": False, "viz_enabled": not args.no_viz,
    }

    # Start web UI in a daemon thread (single process; no orphan risk).
    web_server = None
    if not args.no_viz:
        try:
            socketserver.TCPServer.allow_reuse_address = True
            web_server = socketserver.TCPServer((args.host, args.web_port), HealthHandler)
            threading.Thread(target=web_server.serve_forever, daemon=True).start()
            print(f"Web UI: http://{args.host}:{args.web_port}/")
        except Exception as e:
            print(f"[{_ts()}] Web UI failed to start (continuing without UI): {e}")
            web_server = None

    # Start the receiver (blocks in its accept loop until stopped).
    stop = threading.Event()

    def shutdown(signum, frame):
        print(f"\n[{_ts()}] Received signal {signum}; shutting down...")
        stop.set()
        receiver.stop()
        if web_server is not None:
            try:
                web_server.shutdown()
            except Exception as e:
                print(f"[{_ts()}] web_server shutdown failed: {e}")

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    # Mark ready once the receiver reports listening (it prints that line in start()).
    threading.Thread(target=_mark_ready, args=(receiver, stop), daemon=True).start()

    try:
        receiver.start()
    except KeyboardInterrupt:
        print(f"\n[{_ts()}] Interrupted.")
    finally:
        receiver.stop()
        if web_server is not None:
            try:
                web_server.shutdown()
            except Exception as e:
                print(f"[{_ts()}] web_server shutdown failed: {e}")
        HealthHandler._state["ready"] = False
        print("Stopped.")


def _mark_ready(receiver, stop):
    deadline = time.time() + 10
    while not stop.is_set() and time.time() < deadline:
        # receiver.start() sets running=True synchronously before entering accept().
        if getattr(receiver, 'running', False):
            HealthHandler._state["ready"] = True
            return
        time.sleep(0.1)
    if not stop.is_set():
        # If we timed out but the process is up, still flag ready (best-effort).
        HealthHandler._state["ready"] = bool(getattr(receiver, 'running', False))


def _ts():
    return __import__('datetime').datetime.now().isoformat()


if __name__ == '__main__':
    main()
