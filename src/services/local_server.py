"""Simple HTTP server for serving Horizon HTML reports."""

import argparse
import http.server
import os
from pathlib import Path


def main():
    """CLI entry point: horizon-server [--port PORT]"""
    parser = argparse.ArgumentParser(description="Horizon HTML report server")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    html_dir = Path("data/html")
    html_dir.mkdir(parents=True, exist_ok=True)

    os.chdir(html_dir)
    handler = http.server.SimpleHTTPRequestHandler
    server = http.server.ThreadingHTTPServer(("0.0.0.0", args.port), handler)
    print(f"Serving Horizon reports at http://localhost:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
