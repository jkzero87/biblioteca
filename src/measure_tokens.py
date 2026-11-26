#!/usr/bin/env python3
"""Phase 4, step 3: measure MCP outputs in tokens (llama-server /tokenize)
and verify the caps keep them under budget.

Usage:
  measure  - print chars/tokens table for tools/list JSON + 5 search +
             5 open outputs (real Phase 3 queries, not a biblioteca session)
  verify   - re-measure and assert the search invariants (at most
             SEARCH_MAX_CARDS cards, each with its full header line and a
             text fragment <= SEARCH_SNIPPET_CHARS, trailer always whole)
             and worst open <= 1500 tokens.

Only network call allowed here: POST http://127.0.0.1:8092/tokenize.
"""
import collections
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import library_server as L

TOKENIZER = "http://127.0.0.1:8092/tokenize"
API_KEY_PATH = os.path.expanduser("~/.config/llama/api_key")
QUERIES = ["cat", "cd", "adb", "curl", "Cargo.toml"]
OPEN_TOKEN_BUDGET = 1500


def tokenize(text):
    # llama-server requires the API key since the auth change; the key is
    # read from disk and only ever used in the request header.
    key = ""
    if os.path.exists(API_KEY_PATH):
        with open(API_KEY_PATH, "r", encoding="utf-8") as f:
            key = f.read().strip()
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(
        TOKENIZER,
        data=json.dumps({"content": text}).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.loads(r.read())
    return len(d["tokens"])


def items():
    """(label, text) pairs: tools/list JSON, 5 searches, 5 opens."""
    db = L.Db(L.DB_PATH)
    cache = collections.OrderedDict()
    out = []
    out.append(("tools/list JSON",
                json.dumps({"tools": L.TOOLS}, ensure_ascii=False)))
    for q in QUERIES:
        text = L.library_search(db, q, None)["content"][0]["text"]
        out.append(("search %r" % q, text))
        sid = text.splitlines()[0].split()[1]
        text = L.library_open(db, sid, None, cache)["content"][0]["text"]
        out.append(("open %s (%s)" % (sid.split("-")[1], q), text))
    db.close()
    return out


def search_ok(text):
    """Search invariants: at most SEARCH_MAX_CARDS cards, each header line
    complete (ending at its score field), text fragment capped, trailer
    whole (starts with '[', carries the count and the mode)."""
    lines = text.rstrip("\n").split("\n")
    trailer = lines[-1]
    body = lines[:-1]
    if not (trailer.startswith("[") and "results" in trailer
            and "mode:" in trailer):
        return False
    if len(body) > L.SEARCH_MAX_CARDS:
        return False
    for l in body:
        if l == "(no results)":
            continue
        header, snip = L._split_card(l)
        if not L._CARD_SCORE_RE.search(header):
            return False
        if len(snip) > L.SEARCH_SNIPPET_CHARS:
            return False
    return True


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "measure"
    rows = []
    for label, text in items():
        toks = tokenize(text)
        rows.append((label, text, len(text), toks))

    if mode == "measure":
        print("%-34s %8s %8s %10s" % ("item", "chars", "tokens", "chars/tok"))
        for label, _text, chars, toks in rows:
            print("%-34s %8d %8d %10.2f" % (label, chars, toks, chars / toks))
        worst_o = max((t for l, _t, c, t in rows if l.startswith("open")),
                      default=0)
        print("worst open: %d tokens (budget %d)"
              % (worst_o, OPEN_TOKEN_BUDGET))
        return

    if mode == "verify":
        ok = True
        print("caps in library_server.py: SEARCH_MAX_CARDS=%d SEARCH_SNIPPET_CHARS=%d OPEN=%d"
              % (L.SEARCH_MAX_CARDS, L.SEARCH_SNIPPET_CHARS, L.OPEN_CHAR_CAP))
        print("%-34s %8s %8s %6s" % ("item", "chars", "tokens", "ok"))
        for label, text, chars, toks in rows:
            if label.startswith("search"):
                good = search_ok(text)
            elif label.startswith("open"):
                good = toks <= OPEN_TOKEN_BUDGET
            else:
                good = True
            ok = ok and good
            print("%-34s %8d %8d %6s" % (label, chars, toks, "ok" if good else "FAIL"))
        print("VERIFY %s" % ("PASS" if ok else "FAIL"))
        sys.exit(0 if ok else 1)

    sys.exit("unknown mode %r" % mode)


if __name__ == "__main__":
    main()
