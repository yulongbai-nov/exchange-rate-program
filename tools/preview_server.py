#!/usr/bin/env python3
"""Serve the repo statically for tools/preview.html and proxy /api to the local backend.

    python3 -m backend.server --demo --db /tmp/preview.db --port 8098
    python3 tools/preview_server.py --port 8099 --api http://127.0.0.1:8098
    open http://127.0.0.1:8099/tools/preview.html
"""
import argparse
import http.server
import socketserver
import sys
import urllib.error
import urllib.request
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8099)
    parser.add_argument('--api', default='http://127.0.0.1:8098')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *handler_args, **handler_kwargs):
            super().__init__(*handler_args, directory=str(root), **handler_kwargs)

        def log_message(self, fmt, *log_args):
            pass

        def _proxy(self, method):
            length = int(self.headers.get('Content-Length', '0'))
            body = self.rfile.read(length) if length else None
            request = urllib.request.Request(args.api + self.path, data=body, method=method,
                                             headers={'Content-Type': self.headers.get('Content-Type', 'application/json'),
                                                      'Authorization': self.headers.get('Authorization', '')})
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    payload = response.read()
                    status = response.status
            except urllib.error.HTTPError as error:
                payload = error.read()
                status = error.code
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            if self.path.startswith('/api/'):
                return self._proxy('GET')
            return super().do_GET()

        def do_POST(self):
            if self.path.startswith('/api/'):
                return self._proxy('POST')
            return self.send_error(404)

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(('127.0.0.1', args.port), Handler) as server:
        print(f'preview: http://127.0.0.1:{args.port}/tools/preview.html (api -> {args.api})')
        sys.stdout.flush()
        server.serve_forever()


if __name__ == '__main__':
    main()
