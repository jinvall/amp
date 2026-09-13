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
import io
import json
import os
import signal
import sys
import threading
import time
import cgi
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..'))  # project root for tools.* imports

import audio_receiver as ar                       # noqa: E402
sys.path.insert(0, os.path.join(HERE, '..', 'web'))
from serve import Handler as _WebHandler          # noqa: E402

from http.server import BaseHTTPRequestHandler  # noqa: E402
import socketserver                               # noqa: E402


class HealthHandler(_WebHandler):
    """Static file server + /health and /stems JSON endpoints."""
    _state = {}  # populated by main(): {pcm, control, viz, web, ready, viz_enabled}

    def do_GET(self):
        path = self.path.split('?')[0]
        if path in ('/health', '/health/'):
            # Build a strictly-serializable payload. _state contains a live
            # AudioReceiver instance ("receiver") which json.dumps cannot
            # serialize; expose only ports/status plus a live connection
            # snapshot via connection_state().
            state = dict(HealthHandler._state)
            receiver = state.pop('receiver', None)
            try:
                state['connections'] = receiver.connection_state() if receiver is not None else {}
            except Exception as e:
                # Do NOT silently swallow — surface it so a broken receiver
                # state is visible instead of masquerading as "healthy".
                print(f"[{_ts()}] /health: connection_state() failed: {e}")
                state['connections'] = {}
                state['connection_state_error'] = str(e)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(state).encode('utf-8'))
            return
        if path == '/stems':
            self._serve_stems()
            return
        if path in ('/stream', '/stream/'):
            self._handle_stream()
            return
        super().do_GET()

    def do_POST(self):
        path = self.path.split('?')[0]
        if path == '/tools/process':
            self._handle_tools_process()
            return
        if path == '/stems/toggle':
            self._handle_stem_toggle()
            return
        if path == '/stems/extract':
            self._handle_stem_extract()
            return
        if path == '/stems/clear':
            self._handle_stem_clear()
            return
        if path == '/upload':
            self._handle_upload()
            return
        self.send_response(404)
        self.end_headers()
        self.wfile.write(b'{"error":"not found"}')

    def _handle_stream(self):
        """Stream raw PCM audio to the client.

        Accessible from VLC, ffplay, or any audio player:
          http://silver.local:8093/stream
          ffplay -f s16le -ar 44100 -ac 1 http://silver.local:8093/stream
        """
        receiver = HealthHandler._state.get('receiver')
        if receiver is None:
            self.send_response(503)
            self.end_headers()
            return

        self.send_response(200)
        self.send_header('Content-Type', 'audio/L16; rate=44100; channels=1')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Connection', 'close')
        self.end_headers()

        try:
            print(f"[{_ts()}] /stream: client connected, receiver={'yes' if receiver else 'no'}")
            if receiver:
                print(f"[{_ts()}] /stream: stream_buffer={'yes' if hasattr(receiver, 'stream_buffer') else 'no'}")
            while True:
                if hasattr(receiver, 'stream_buffer') and receiver.stream_buffer is not None:
                    data = receiver.stream_buffer.read(timeout=1.0)
                    if data:
                        print(f"[{_ts()}] /stream: sending {len(data)} bytes")
                        self.wfile.write(data)
                        self.wfile.flush()
                    else:
                        print(f"[{_ts()}] /stream: no data from buffer")
                else:
                    time.sleep(0.1)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            print(f"[{_ts()}] /stream error: {e}")

    def _handle_stem_toggle(self):
        try:
            receiver = HealthHandler._state.get('receiver')
            if receiver is None or receiver.stem_manager is None:
                self.send_response(503)
                self.end_headers()
                self.wfile.write(b'{"error":"stem manager unavailable"}')
                return
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            enabled = body.get('enabled')
            new_state = receiver.toggle_stem_extraction(enabled)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"enabled": new_state}).encode('utf-8'))
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))

    def _handle_stem_extract(self):
        try:
            receiver = HealthHandler._state.get('receiver')
            if receiver is None or receiver.stem_manager is None:
                self.send_response(503)
                self.end_headers()
                self.wfile.write(b'{"error":"stem manager unavailable"}')
                return
            count = receiver.extract_all_stems()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"queued": count}).encode('utf-8'))
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))

    def _handle_stem_clear(self):
        try:
            receiver = HealthHandler._state.get('receiver')
            if receiver is None or receiver.stem_manager is None:
                self.send_response(503)
                self.end_headers()
                self.wfile.write(b'{"error":"stem manager unavailable"}')
                return
            count = receiver.clear_all_stems()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"cleared": count}).encode('utf-8'))
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))

    def _serve_stems(self):
        try:
            receiver = HealthHandler._state.get('receiver')
            if receiver is None or receiver.stem_manager is None:
                self.send_response(503)
                self.end_headers()
                self.wfile.write(b'{"error":"stem manager unavailable"}')
                return
            data = {
                "stats": receiver.stem_manager.stats(),
                "segments": {},
                "service_enabled": receiver.stem_extraction_enabled,
                "exceptions": receiver.stem_manager.recent_exceptions(),
            }
            for sid, stems in receiver.stem_manager.list_stems().items():
                data["segments"][sid] = {
                    "stems": stems,
                    "segment_path": os.path.join(
                        receiver._wav_output_dir, sid + ".wav"
                    ),
                }
            body = json.dumps(data).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))

    def _handle_tools_get(self):
        try:
            from tools.filters.feature_options import get_all_filter_names, get_filter_params
            data = {
                "filters": get_all_filter_names(),
                "params": {name: get_filter_params(name) for name in get_all_filter_names()}
            }
            body = json.dumps(data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))

    def _handle_tools_process(self):
        try:
            from tools.io.reader import AudioReader
            from tools.io.writer import AudioWriter
            from tools.filters.chain import FilterChain
            from tools.filters.noise_cancellation import NoiseCancellationFilter
            from tools.filters.spectral_difference import SpectralDifferenceFilter
            from tools.filters.voice_removal import VoiceRemovalFilter
            from tools.filters.voice_isolation import VoiceIsolationFilter
            from tools.filters.ambient_removal import AmbientRemovalFilter
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            source = body.get("source")
            filters = body.get("filters", [])
            if not source:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"{\"error\": \"missing source\"}")
                return
            samples, sr = AudioReader.read(source)
            chain = FilterChain()
            for f in filters:
                name = f.get("name")
                params = f.get("params", {})
                if name == "noise_cancellation": chain.add(NoiseCancellationFilter(sr, **params))
                elif name == "spectral_difference": chain.add(SpectralDifferenceFilter(sr, **params))
                elif name == "voice_removal": chain.add(VoiceRemovalFilter(sr, **params))
                elif name == "voice_isolation": chain.add(VoiceIsolationFilter(sr, **params))
                elif name == "ambient_removal": chain.add(AmbientRemovalFilter(sr, **params))
            processed = chain.process(samples.tobytes())
            import numpy as np
            out_samples = np.frombuffer(processed, dtype=np.int16).astype(np.float64) / 32768.0
            out_path = source.replace(".wav", "_processed.wav").replace(".flac", "_processed.flac")
            AudioWriter.write_with_metadata(out_path, out_samples, sr, {"filters": filters})
            body = json.dumps({"saved": out_path, "duration": len(out_samples) / sr}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))

    def _handle_upload(self):
        try:
            receiver = HealthHandler._state.get('receiver')
            if receiver is None:
                self.send_response(503)
                self.end_headers()
                self.wfile.write(b'{"error":"receiver unavailable"}')
                return
            content_type = self.headers.get('Content-Type', '')
            if 'multipart/form-data' not in content_type:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'{"error":"expected multipart/form-data"}')
                return
            form = cgi.FieldStorage(
                fp=self.rfile,
                headers=self.headers,
                environ={'REQUEST_METHOD': 'POST',
                         'CONTENT_TYPE': content_type},
            )
            uploaded = form['file'] if 'file' in form else None
            if uploaded is None or not uploaded.filename:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'{"error":"missing file field"}')
                return
            filename = uploaded.filename
            if not filename.lower().endswith('.wav'):
                self.send_response(415)
                self.end_headers()
                self.wfile.write(b'{"error":"only .wav files are supported"}')
                return
            data = uploaded.file.read()
            if not data:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'{"error":"empty file"}')
                return
            out_dir = receiver._wav_output_dir
            os.makedirs(out_dir, exist_ok=True)
            base = os.path.splitext(filename)[0]
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            out_name = f"upload_{ts}_{base}.wav"
            out_path = os.path.join(out_dir, out_name)
            with open(out_path, 'wb') as f:
                f.write(data)
            segment_id = os.path.splitext(out_name)[0]
            queued = False
            if receiver.stem_manager is not None and receiver.stem_extraction_enabled:
                threading.Thread(
                    target=receiver._extract_stems_async,
                    args=(segment_id, out_path),
                    daemon=True,
                ).start()
                queued = True
            body = json.dumps({
                "saved": out_name,
                "segment_id": segment_id,
                "size": len(data),
                "stem_extraction_queued": queued,
            }).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))

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
        "receiver": receiver,
    }

    # Start web UI in a daemon thread (single process; no orphan risk).
    web_server = None
    if not args.no_viz:
        try:
            socketserver.TCPServer.allow_reuse_address = True
            web_server = socketserver.ThreadingTCPServer((args.host, args.web_port), HealthHandler)
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

# ── Editor endpoints ──────────────────────────────────────────────────────
def _register_editor_endpoints(HealthHandler):
    """Add editor endpoints to the HealthHandler class."""
    orig_post = HealthHandler.do_POST

    def do_POST(self):
        path = self.path.split('?')[0]
        if path == '/editor/filters':
            self._handle_editor_filters()
        elif path == '/editor/transcribe':
            self._handle_editor_transcribe()
        elif path == '/editor/stems':
            self._handle_editor_stems()
        elif path == '/editor/midi':
            self._handle_editor_midi()
        elif path == '/editor/midi/test':
            self._handle_editor_midi_test()
        elif path == '/editor/profiles/activate':
            self._handle_profile_activate()
        else:
            orig_post(self)

    def do_GET(self):
        path = self.path.split('?')[0]
        if path == '/editor/profiles':
            self._handle_profiles_get()
        elif path == '/editor.js':
            self._serve_static_file('web/editor.js', 'application/javascript')
        elif path == '/editor.css':
            self._serve_static_file('web/editor.css', 'text/css')
        elif path == '/editor.html':
            self._serve_static_file('web/editor.html', 'text/html')
        else:
            super().do_GET()

    def _serve_static_file(self, relpath, content_type):
        full = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), relpath)
        if not os.path.exists(full):
            self.send_response(404)
            self.end_headers()
            return
        with open(full, 'rb') as f:
            data = f.read()
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle_profiles_get(self):
        try:
            from server.profile_manager import get_manager
            pm = get_manager()
            body = json.dumps({'profiles': pm.list_profiles()}).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode('utf-8'))

    def _handle_profile_activate(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            name = body.get('name', '')
            from server.profile_manager import get_manager
            pm = get_manager()
            ok = pm.set_active(name)
            self.send_response(200 if ok else 404)
            self.end_headers()
            self.wfile.write(json.dumps({'status': f'Activated {name}'} if ok else {'error': f'Unknown profile: {name}'}).encode('utf-8'))
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode('utf-8'))

    def _handle_editor_filters(self):
        try:
            from server.profile_manager import get_manager
            from server.analysis_bridge import get_bridge
            pm = get_manager()
            bridge = get_bridge(pm)
            source = self._receive_upload()
            if not source:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'{"error":"no file uploaded"}')
                return
            # Parse filters from form field
            import cgi as cgi_mod
            # Re-read if needed — already consumed
            result = bridge.apply_filters(source, ['noise_cancellation'], {})
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(result).encode('utf-8'))
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode('utf-8'))

    def _handle_editor_transcribe(self):
        try:
            from server.profile_manager import get_manager
            from server.analysis_bridge import get_bridge
            pm = get_manager()
            bridge = get_bridge(pm)
            source = self._receive_upload()
            if not source:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'{"error":"no file uploaded"}')
                return
            result = bridge.transcribe(source)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(result).encode('utf-8'))
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode('utf-8'))

    def _handle_editor_stems(self):
        try:
            from server.profile_manager import get_manager
            pm = get_manager()
            stem_backend = pm.get_stems_config().get('backend', 'frequency_band')
            source = self._receive_upload()
            if not source:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'{"error":"no file uploaded"}')
                return
            # Run extraction
            stem_id = os.path.splitext(os.path.basename(source))[0]
            stem_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'audio_stems', stem_id)
            os.makedirs(stem_dir, exist_ok=True)
            stems = None
            if stem_backend == 'demucs':
                from stem_backends import DemucsBackend
                stems = DemucsBackend.extract(source, stem_dir)
            elif stem_backend == 'audio_separator':
                from audio_separator_backend import AudioSeparatorBackend
                stems = AudioSeparatorBackend.extract(source, stem_dir)
            else:
                from stem_backends import FrequencyBandBackend
                stems = FrequencyBandBackend.extract(source, stem_dir)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'stems': stems or {}, 'backend': stem_backend}).encode('utf-8'))
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode('utf-8'))

    def _handle_editor_midi(self):
        self.send_response(501)
        self.end_headers()
        self.wfile.write(json.dumps({'status': 'MIDI endpoint placeholder — wire to midi_output module'}).encode('utf-8'))

    def _handle_editor_midi_test(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps({'status': 'test note sent (placeholder)'}).encode('utf-8'))

    def _receive_upload(self):
        """Receive a file upload and return the saved path."""
        import cgi as cgi_mod
        import tempfile
        content_type = self.headers.get('Content-Type', '')
        if 'multipart/form-data' not in content_type:
            return None
        form = cgi_mod.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={'REQUEST_METHOD': 'POST', 'CONTENT_TYPE': content_type},
        )
        field = form['file'] if 'file' in form else None
        if field is None or not field.filename:
            return None
        data = field.file.read()
        if not data:
            return None
        suffix = '.wav' if field.filename.lower().endswith('.wav') else '.flac'
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False, dir='/tmp') as f:
            f.write(data)
            return f.name

    HealthHandler.do_POST = do_POST
    HealthHandler.do_GET = do_GET
    HealthHandler._serve_static_file = _serve_static_file
    HealthHandler._handle_profiles_get = _handle_profiles_get
    HealthHandler._handle_profile_activate = _handle_profile_activate
    HealthHandler._handle_editor_filters = _handle_editor_filters
    HealthHandler._handle_editor_transcribe = _handle_editor_transcribe
    HealthHandler._handle_editor_stems = _handle_editor_stems
    HealthHandler._handle_editor_midi = _handle_editor_midi
    HealthHandler._handle_editor_midi_test = _handle_editor_midi_test
    HealthHandler._receive_upload = _receive_upload


# Register editor endpoints on import
_register_editor_endpoints(HealthHandler)
