#!/usr/bin/env python3
"""给微信小程序提供 API：查询最新数据 + 手动刷新。"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from realtime_house_trends import run

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))
SNAPSHOT_FILE = Path(os.getenv("SNAPSHOT_FILE", "data/snapshots.csv"))
OUTPUT_HTML = Path(os.getenv("OUTPUT_HTML", "output/house_trends.html"))
OUTPUT_JSON = Path(os.getenv("OUTPUT_JSON", "output/latest.json"))


class Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            self._json(200, {"ok": True})
            return
        if self.path == "/api/latest":
            if not OUTPUT_JSON.exists():
                self._json(404, {"error": "latest.json 不存在，请先刷新"})
                return
            self._json(200, json.loads(OUTPUT_JSON.read_text(encoding="utf-8")))
            return
        if self.path == "/house_trends.html":
            if OUTPUT_HTML.exists():
                body = OUTPUT_HTML.read_text(encoding="utf-8").encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
        self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/api/refresh":
            result = run(SNAPSHOT_FILE, OUTPUT_HTML, OUTPUT_JSON)
            self._json(
                200,
                {
                    "ok": True,
                    "output_html": result["output_html"],
                    "output_json": result["output_json"],
                    "city_count": len(result["snapshots"]),
                },
            )
            return
        self._json(404, {"error": "not found"})


def main() -> None:
    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    if not OUTPUT_JSON.exists():
        run(SNAPSHOT_FILE, OUTPUT_HTML, OUTPUT_JSON)
    server = HTTPServer((HOST, PORT), Handler)
    print(f"Server running: http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
