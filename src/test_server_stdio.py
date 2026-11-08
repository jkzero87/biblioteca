#!/usr/bin/env python3
"""Phase 4 fix step 5: stdio test of src/library_server.py.

Spawns the server and, over stdio:
  (a) sends server/discover FIRST and checks a -32601 error comes back <1 s;
  (b) initializes with protocolVersion "2025-11-25" + notifications/initialized;
  (c) tools/list;
  (c2) library_search with a real query from the Phase 3 list
       (data/phase3_queries.json), not a biblioteca session;
  (d) library_open on session-c5f86181 with no query -> coarse outline
       (landmark lines with seq, omitted note, next: trailer);
  (e) library_open from_seq=<landmark seq> from (d) -> detail window;
  (f) library_open at="end".
Prints each response truncated to 300 chars, the elapsed ms, and the
response token count (POST 127.0.0.1:8092/tokenize).

Stdlib only; the db is opened by the server (which records access for the
returned [card] hits, as it must).
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUERY = "adb"  # from data/phase3_queries.json (Phase 3 list), dsh-scratch
SID = "session-c5f86181-d002-4a54-b7a1-7d647da9c2e4"
TOKENIZE_URL = "http://127.0.0.1:8092/tokenize"
TRAILER_RE = re.compile(r"^next: from_seq=(\d+) \((\d+) events left, ~(\d+) tokens\)$")
LANDMARK_RE = re.compile(r"^\[(\d+)\] \[(?:user|observed)\] ")


def tokenize(text):
    body = json.dumps({"content": text}).encode("utf-8")
    req = urllib.request.Request(TOKENIZE_URL, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return len(json.load(r)["tokens"])


def main():
    queries = json.load(open(os.path.join(ROOT, "data", "phase3_queries.json")))
    if QUERY not in queries:
        sys.exit("query %r is not in the Phase 3 list" % QUERY)

    proc = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "src", "library_server.py")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )

    def call(method, mid=None, params=None):
        msg = {"jsonrpc": "2.0", "method": method}
        if mid is not None:
            msg["id"] = mid
        if params is not None:
            msg["params"] = params
        t0 = time.monotonic()
        proc.stdin.write(json.dumps(msg) + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        ms = (time.monotonic() - t0) * 1000
        return json.loads(line), ms

    def show(label, msg, ms):
        text = json.dumps(msg, ensure_ascii=False)
        print("%-26s %8.1f ms  %s%s" % (label, ms, text[:300],
                                        " …" if len(text) > 300 else ""))

    def show_tokens(label, res):
        t = res["content"][0]["text"]
        print("  %-24s tokens: %d (%d chars)" % (label, tokenize(t), len(t)))

    # (a) probe first: clients that probe server/discover must get -32601 at once
    msg, ms = call("server/discover", mid=0)
    show("(a) server/discover", msg, ms)
    err = msg.get("error", {})
    assert err.get("code") == -32601, "expected -32601, got %r" % msg
    assert ms < 1000, "discover error took %.1f ms (must be <1000)" % ms
    print("  OK: -32601 in %.1f ms (<1 s)" % ms)

    # (b) initialize + notification
    msg, ms = call("initialize", mid=1, params={
        "protocolVersion": "2025-11-25",
        "capabilities": {},
        "clientInfo": {"name": "test_server_stdio", "version": "0.1.0"},
    })
    show("(b) initialize", msg, ms)
    assert msg["result"]["protocolVersion"] == "2025-11-25"
    assert msg["result"]["serverInfo"] == {"name": "biblioteca", "version": "0.1.0"}
    t0 = time.monotonic()
    proc.stdin.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
    proc.stdin.flush()
    # no reply expected; the next read must be the tools/list response
    msg, ms = call("tools/list", mid=2)
    show("(c) tools/list", msg, ms)
    names = [t["name"] for t in msg["result"]["tools"]]
    assert names == ["library_search", "library_open"], names
    for t in msg["result"]["tools"]:
        assert len(t["description"]) <= 200
    print("  OK: %s, descriptions <= 200 chars" % names)

    # (c2) real Phase 3 query, not a biblioteca session
    msg, ms = call("tools/call", mid=3, params={
        "name": "library_search",
        "arguments": {"query": QUERY},
    })
    show("(c2) library_search %r" % QUERY, msg, ms)
    res = msg["result"]
    assert res["isError"] is False, res
    first = res["content"][0]["text"].splitlines()[0]
    sid0 = first.split()[1]
    assert "biblioteca" not in sid0, sid0
    show_tokens("(c2) search response", res)

    # (d) no query, no from_seq, no at -> coarse outline
    msg, ms = call("tools/call", mid=4, params={
        "name": "library_open",
        "arguments": {"id": SID},
    })
    show("(d) library_open %s (outline)" % SID, msg, ms)
    res = msg["result"]
    assert res["isError"] is False, res
    text = res["content"][0]["text"]
    lines = text.rstrip("\n").split("\n")
    assert lines[0].startswith("[card] %s " % SID), lines[0]
    landmark_seqs = [int(m.group(1)) for l in lines[1:]
                     for m in [LANDMARK_RE.match(l)] if m]
    assert landmark_seqs and landmark_seqs == sorted(landmark_seqs), landmark_seqs
    n_user = sum(1 for l in lines if re.match(r"^\[\d+\] \[user\] ", l))
    assert n_user >= 2, "outline must keep the first/last user messages"
    n_obs = sum(1 for l in lines if re.match(r"^\[\d+\] \[observed\] ", l))
    print("  landmarks: %d user, %d observed" % (n_user, n_obs))
    assert any(l.startswith("[omitted ") for l in lines), "no omitted note"
    m = TRAILER_RE.match(lines[-1])
    assert m, "no next: trailer on outline, got %r" % lines[-1]
    n_outline = int(m.group(1))
    assert n_outline not in landmark_seqs, "trailer seq must be an omitted landmark"
    show_tokens("(d) outline", res)
    print("  OK: outline, %d landmarks, trailer from_seq=%s (%s events left, ~%s tokens)"
          % (len(landmark_seqs), n_outline, m.group(2), m.group(3)))

    # (e) from_seq jump to a landmark seq from (d)
    n = landmark_seqs[0]
    msg, ms = call("tools/call", mid=5, params={
        "name": "library_open",
        "arguments": {"id": SID, "from_seq": n},
    })
    res = msg["result"]
    assert res["isError"] is False, res
    t2 = res["content"][0]["text"]
    l2 = t2.rstrip("\n").split("\n")
    first = int(l2[1][1:l2[1].index("]")])
    assert first >= n, "window must start at seq >= %d, got %d" % (n, first)
    m2 = TRAILER_RE.match(l2[-1])
    if m2:
        assert int(m2.group(1)) > first, "trailer must point past the start"
    show_tokens("(e) from_seq window", res)
    print("  OK: from_seq=%d (landmark) -> first shown seq %d, trailer %s, %.1f ms"
          % (n, first, m2.group(0) if m2 else "none", ms))

    # (f) at="end": window ends at the last event and always includes the last
    # user message and the last non-empty assistant text
    msg, ms = call("tools/call", mid=6, params={
        "name": "library_open",
        "arguments": {"id": SID, "at": "end"},
    })
    res = msg["result"]
    assert res["isError"] is False, res
    t3 = res["content"][0]["text"]
    l3 = t3.rstrip("\n").split("\n")
    has_user = any(re.match(r"^\[\d+\] \[user\] ", l) for l in l3)
    has_assist = any(re.match(r"^\[\d+\] \[model said", l) for l in l3)
    assert has_user, "at=end must include the last [user] message"
    assert has_assist, "at=end must include the last [model said] text"
    trailer = TRAILER_RE.match(l3[-1])
    show_tokens("(f) at=end window", res)
    print("  OK: at=end includes last [user] and [model said] blocks; "
          "front trailer %s, %.1f ms" % ("yes" if trailer else "no", ms))

    proc.stdin.close()
    stderr = proc.stderr.read()
    rc = proc.wait(timeout=10)
    assert rc == 0, "server exited with %r" % rc
    print("server exited cleanly on EOF (rc=0); stderr had %d log lines"
          % len([l for l in stderr.splitlines() if l.strip()]))


if __name__ == "__main__":
    main()
