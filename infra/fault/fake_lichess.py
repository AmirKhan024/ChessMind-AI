"""Fake Lichess for the 429 failure test: the first FAIL_FIRST game-export requests get HTTP
429, later ones stream the games in GAMES (NDJSON, same shape as the real export).
Usage: python fake_lichess.py GAMES.ndjson [FAIL_FIRST]"""

import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

GAMES = open(sys.argv[1], "rb").read()
FAIL_FIRST = int(sys.argv[2]) if len(sys.argv) > 2 else 1
calls = 0


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        global calls
        calls += 1
        if calls <= FAIL_FIRST:
            self.send_response(429)
            self.end_headers()
        else:
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()
            self.wfile.write(GAMES)
        print(f"request {calls}: {self.path} -> {429 if calls <= FAIL_FIRST else 200}", flush=True)


HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
