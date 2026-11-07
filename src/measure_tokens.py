#!/usr/bin/env python3
"""Phase 4, step 3: measure MCP outputs in tokens (llama-server /tokenize)
and verify the char caps keep them under budget.

Usage:
  measure  - print chars/tokens table for tools/list JSON + 5 search +
             5 open outputs (real Phase 3 queries, not a biblioteca session)
  verify   - re-measure with the caps from library_server.py and assert the
             worst search <= 300 tokens and worst open <= 1500 tokens.

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
QUERIES = ["cat", "cd", "adb", "curl", "Cargo.toml"]
SEARCH_TOKEN_BUDGET = 300
OPEN_TOKEN_BUDGET = 1500


def tokenize(text):
    req = urllib.request.Request(
        TOKENIZER,
        data=json.dumps({"content": text}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
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


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "measure"
    rows = []
    for label, text in items():
        toks = tokenize(text)
        rows.append((label, len(text), toks))

    if mode == "measure":
        print("%-34s %8s %8s %10s" % ("item", "chars", "tokens", "chars/tok"))
        for label, chars, toks in rows:
            print("%-34s %8d %8d %10.2f" % (label, chars, toks, chars / toks))
        worst_s = max((t for l, c, t in rows if l.startswith("search")), default=0)
        worst_o = max((t for l, c, t in rows if l.startswith("open")), default=0)
        print("worst search: %d tokens (budget %d); worst open: %d tokens (budget %d)"
              % (worst_s, SEARCH_TOKEN_BUDGET, worst_o, OPEN_TOKEN_BUDGET))
        return

    if mode == "verify":
        ok = True
        print("caps in library_server.py: SEARCH=%d OPEN=%d"
              % (L.SEARCH_CHAR_CAP, L.OPEN_CHAR_CAP))
        print("%-34s %8s %8s %6s" % ("item", "chars", "tokens", "ok"))
        for label, chars, toks in rows:
            budget = None
            if label.startswith("search"):
                budget = SEARCH_TOKEN_BUDGET
            elif label.startswith("open"):
                budget = OPEN_TOKEN_BUDGET
            good = budget is None or toks <= budget
            ok = ok and good
            print("%-34s %8d %8d %6s" % (label, chars, toks, "ok" if good else "FAIL"))
        print("VERIFY %s" % ("PASS" if ok else "FAIL"))
        sys.exit(0 if ok else 1)

    sys.exit("unknown mode %r" % mode)


if __name__ == "__main__":
    main()
