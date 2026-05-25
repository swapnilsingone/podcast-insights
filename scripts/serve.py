#!/usr/bin/env python3
"""Serve the podcast-insights project over HTTP.

Library pages (dashboard, entities, predictions, speakers, ask, compare, highlights)
read JSON files via fetch(), which Chrome blocks under file://. This launches a tiny
local HTTP server so the pages work.

Usage:
  python3 scripts/serve.py            # serve on http://localhost:8765
  python3 scripts/serve.py --port 9000
  python3 scripts/serve.py --open     # also open the dashboard in default browser

Idempotent: if a server is already running on the port, prints the URLs and exits 0.
"""
import argparse, http.server, socketserver, threading, webbrowser, socket, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return False
        except OSError:
            return True

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--open", action="store_true")
    ap.add_argument("--page", default="library/dashboard.html",
                    help="page to print/open (default: library/dashboard.html)")
    args = ap.parse_args()

    dashboard_url = f"http://localhost:{args.port}/{args.page}"
    library_url   = f"http://localhost:{args.port}/library/index.html"

    if port_in_use(args.port):
        print(f"Already running on :{args.port}")
        print(f"  Dashboard: {dashboard_url}")
        print(f"  Library:   {library_url}")
        if args.open:
            webbrowser.open(dashboard_url)
        return 0

    import os
    os.chdir(ROOT)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def end_headers(self):
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            super().end_headers()

        def log_message(self, fmt, *a):
            sys.stderr.write(f"[serve] {self.address_string()} {fmt % a}\n")

    httpd = socketserver.TCPServer(("127.0.0.1", args.port), Handler)
    print(f"Serving {ROOT}")
    print(f"  Dashboard: {dashboard_url}")
    print(f"  Library:   {library_url}")
    print(f"Stop with Ctrl-C.\n")

    if args.open:
        threading.Timer(0.3, lambda: webbrowser.open(dashboard_url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")

if __name__ == "__main__":
    sys.exit(main() or 0)
