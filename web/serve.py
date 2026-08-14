#!/usr/bin/env python3
"""Minimal static file server for the AMP web visualizer.

Serves web/index.html so the visualizer can be opened in a browser on Silver
(or any machine with access to Silver). The page connects back to the
receiver's WebSocket viz feed (port 8082 by default).

Usage:
    python3 serve.py --host 0.0.0.0 --port 8083
Then open http://<silver-ip>:8083/
"""
import argparse
import http.server
import os
import socketserver

HERE = os.path.dirname(os.path.abspath(__file__))


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=HERE, **kwargs)

    def log_message(self, fmt, *args):
        # Quieter logging.
        pass


def make_server(host='0.0.0.0', port=8093):
    """Build (but do not start) the static HTTP server for the visualizer UI."""
    socketserver.TCPServer.allow_reuse_address = True
    return socketserver.TCPServer((host, port), Handler)


def main():
    parser = argparse.ArgumentParser(description='AMP visualizer static server')
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=8093)
    args = parser.parse_args()

    httpd = make_server(args.host, args.port)
    print(f"AMP visualizer serving {HERE} at http://{args.host}:{args.port}/")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == '__main__':
    main()
