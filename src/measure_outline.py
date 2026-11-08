#!/usr/bin/env python3
"""Phase 4 fix step 2: choose OUTLINE_CHAR_CAP so the worst library_open
outline over the 5 largest sessions (by rendered chars) stays <= 350
tokens, measured against llama-server /tokenize.

Usage:
  sweep  - print session sizes, top 5, and per-cap outline token counts
  find   - binary-search the largest cap whose worst outline is <= 350
           tokens and print the chosen OUTLINE_CHAR_CAP

Stdlib only. Network: POST http://127.0.0.1:8092/tokenize only.
"""
import collections
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import library_server as L

TOKENIZER = "http://127.0.0.1:8092/tokenize"
BUDGET = 350
CAP_LO, CAP_HI = 300, 8000


def tokenize(text):
    req = urllib.request.Request(
        TOKENIZER,
        data=json.dumps({"content": text}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return len(json.loads(r.read())["tokens"])


def rendered_size(events):
    n = 0
    for e in events:
        lines = L.render_event(e)
        n += sum(len(l) for l in lines) + (len(lines) - 1)
    return n


def load_tops():
    db = L.Db(L.DB_PATH)
    rows = db.con().execute(
        "SELECT id, source, start, end, project, first_message FROM cards"
    ).fetchall()
    db.close()
    cache = collections.OrderedDict()
    sizes = []
    for sid, source, start, end, project, first in rows:
        try:
            events = L.decode_session(source, cache)
        except Exception as exc:
            print("decode failed %s: %s" % (sid, exc))
            continue
        header = (
            "[card] %s  %s -> %s  project=%s  first: %s"
            % (sid, start, end, project, L.clip(first or "", 100))
        )
        sizes.append((rendered_size(events), sid, header, events))
    sizes.sort(key=lambda t: t[0], reverse=True)
    return sizes


def worst_at(tops, cap):
    """(worst_tokens, per-session [(sid, chars, tokens)]) at this cap."""
    worst, detail = 0, []
    for _, sid, header, events in tops:
        text = "\n".join(L._outline_text(events, header, cap))
        toks = tokenize(text)
        worst = max(worst, toks)
        detail.append((sid, len(text), toks))
    return worst, detail


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "find"
    sizes = load_tops()
    print("sessions: %d" % len(sizes))
    print("top 5 by rendered chars:")
    for n, sid, _, _ in sizes[:5]:
        print("  %-8d %s" % (n, sid))
    tops = sizes[:5]

    if mode == "sweep":
        for cap in (500, 800, 1200, 1600, 2000, 3000, 4000, 6000, 8000):
            worst, _ = worst_at(tops, cap)
            print("cap %-6d worst outline: %d tokens %s"
                  % (cap, worst, "ok" if worst <= BUDGET else "OVER"))
        return

    lo, hi, best = CAP_LO, CAP_HI, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if worst_at(tops, mid)[0] <= BUDGET:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    if best is None:
        print("no cap in [%d, %d] keeps the worst outline <= %d tokens"
              % (CAP_LO, CAP_HI, BUDGET))
        sys.exit(1)
    worst, detail = worst_at(tops, best)
    print("chosen OUTLINE_CHAR_CAP = %d" % best)
    for sid, chars, toks in detail:
        print("  %s  %d chars  %d tokens" % (sid, chars, toks))
    print("worst: %d tokens (budget %d)" % (worst, BUDGET))


if __name__ == "__main__":
    main()
