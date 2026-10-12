#!/usr/bin/env python3
"""Serve only the offline review package on localhost; no upload/write/model endpoint."""

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--package", type=Path, required=True)
p.add_argument("--port", type=int, default=8780)
a = p.parse_args()
if not (a.package / "review.html").is_file():
    raise ValueError("Generated review package required")
print(f"İnceleme: http://127.0.0.1:{a.port}/review.html", flush=True)
with ThreadingHTTPServer(
    ("127.0.0.1", a.port), partial(SimpleHTTPRequestHandler, directory=str(a.package.resolve()))
) as server:
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
