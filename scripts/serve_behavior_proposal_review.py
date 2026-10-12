#!/usr/bin/env python3
"""Serve only the offline review package on localhost; no upload/write/model endpoint."""

import argparse
import hashlib
import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--package", type=Path, required=True)
p.add_argument("--port", type=int, default=8780)
p.add_argument("--session", type=Path, help="Read-only six-crop view; original package stays unchanged")
a = p.parse_args()
if not (a.package / "review.html").is_file():
    raise ValueError("Generated review package required")
session_html = None
if a.session:
    session = json.loads(a.session.read_text())
    digest = hashlib.sha256((a.package / "manifest.json").read_bytes()).hexdigest()
    if session["package_sha256"] != digest or len(set(session["crop_ids"])) != 6:
        raise ValueError("Six unique crops from matching package required")
    html = (a.package / "review.html").read_text()
    prefix, rest = html.split("window.REVIEW_DATA=", 1)
    data, end = json.JSONDecoder().raw_decode(rest)
    by_id = {r["id"]: r for r in data["queue"]["crops"]}
    data["queue"]["crops"] = [by_id[i] for i in session["crop_ids"]]
    if [r["light"] for r in data["queue"]["crops"]].count("day") != 3:
        raise ValueError("Session needs three day and three night crops")
    data["queue"]["full_frames"] = []
    data["session"] = session
    session_html = (
        (
            prefix
            + "window.REVIEW_DATA="
            + json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
            + rest[end:]
        )
        .replace('src="review.js"', 'src="/_session/review.js"')
        .encode()
    )


class ReviewHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        payload = None
        if session_html and self.path.split("?", 1)[0] in ("/", "/review.html"):
            payload = session_html
        elif session_html and self.path.split("?", 1)[0] == "/_session/review.js":
            payload = (Path(__file__).parent / "behavior_review/review.js").read_bytes()
        if payload is None:
            return super().do_GET()
        self.send_response(200)
        self.send_header(
            "Content-Type",
            "application/javascript" if self.path.endswith(".js") else "text/html; charset=utf-8",
        )
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)


print(f"İnceleme: http://127.0.0.1:{a.port}/review.html", flush=True)
with ThreadingHTTPServer(
    ("127.0.0.1", a.port), partial(ReviewHandler, directory=str(a.package.resolve()))
) as server:
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
