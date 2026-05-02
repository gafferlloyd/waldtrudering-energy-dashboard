"""Minimal HTTP server for output/index.html with no-cache headers."""
import os
import argparse
from http.server import HTTPServer, SimpleHTTPRequestHandler


class NoCacheHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        super().end_headers()

    def log_message(self, fmt, *args):
        pass  # suppress per-request noise in the service log


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8082)
    ap.add_argument("--dir", default=".")
    args = ap.parse_args()
    os.chdir(args.dir)
    print(f"Serving {args.dir} on port {args.port}")
    with HTTPServer(("", args.port), NoCacheHandler) as httpd:
        httpd.serve_forever()
