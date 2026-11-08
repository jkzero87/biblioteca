#!/usr/bin/env python3
"""Phase 4 fix step 3: per-session rendered chars/token for the next: trailer.

For every card in data/library.db: decode the session (zstd -dc), render all
events with library_server.render_event, POST the full rendered text to
llama-server /tokenize, and record ratio = rendered_chars / tokens.

Writes data/char_ratio.json as {session_id: ratio}. The server reads it on
every library_open call and falls back to 2.5 when the file or the id is
missing. Stdlib + zstd CLI + POST 127.0.0.1:8092/tokenize only.
"""
import collections
import json
import os
import sqlite3
import sys
import urllib.request

import library_server as L

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "data", "library.db")
OUT_PATH = os.path.join(ROOT, "data", "char_ratio.json")
TOKENIZE_URL = "http://127.0.0.1:8092/tokenize"


def tokenize(text):
    body = json.dumps({"content": text}).encode("utf-8")
    req = urllib.request.Request(TOKENIZE_URL, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return len(json.load(r)["tokens"])


def main():
    con = sqlite3.connect("file:%s?mode=ro" % DB_PATH, uri=True)
    rows = con.execute("SELECT id, source FROM cards").fetchall()
    con.close()
    cache = collections.OrderedDict()
    ratios = {}
    for sid, source in rows:
        try:
            events = L.decode_session(source, cache)
            rendered = "\n".join(
                l for e in events for l in L.render_event(e))
            chars = len(rendered)
            toks = tokenize(rendered)
            ratios[sid] = round(chars / toks, 4) if toks else None
            print("%-70s %8d chars %7d tokens  %.4f"
                  % (sid, chars, toks, ratios[sid] or 0))
        except Exception as exc:
            print("%-70s FAILED: %s" % (sid, exc), file=sys.stderr)
    with open(OUT_PATH, "w") as f:
        json.dump(ratios, f, indent=1, sort_keys=True)
    vals = [v for v in ratios.values() if v]
    if vals:
        print("wrote %s: %d/%d sessions, ratio min=%.4f max=%.4f"
              % (OUT_PATH, len(vals), len(rows), min(vals), max(vals)))
    else:
        print("wrote %s: 0/%d sessions (all failed)" % (OUT_PATH, len(rows)))


if __name__ == "__main__":
    main()
