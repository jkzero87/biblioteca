#!/usr/bin/env python3
"""Phase 4, step 2: stdio test of src/library_server.py.

Spawns the server and, over stdio:
  (a) sends server/discover FIRST and checks a -32601 error comes back <1 s;
  (b) initializes with protocolVersion "2025-11-25" + notifications/initialized;
  (c) tools/list;
  (d) library_search and library_open with a real query from the Phase 3 list
      (data/phase3_queries.json), not a biblioteca session;
  (e) library_open from_seq=<n>, continuing from (d)'s next: trailer (or,
      when (d) was not truncated, from its first block's seq);
  (f) library_open at="end".
Prints each response truncated to 300 chars and the elapsed ms.

Stdlib only; the db is opened by the server (which records access for the
returned [card] hits, as it must).
"""
import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUERY = "adb"  # from data/phase3_queries.json (Phase 3 list), dsh-scratch


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
        print("%-22s %8.1f ms  %s%s" % (label, ms, text[:300],
                                        " …" if len(text) > 300 else ""))

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

    # (d) real Phase 3 query, not a biblioteca session
    msg, ms = call("tools/call", mid=3, params={
        "name": "library_search",
        "arguments": {"query": QUERY},
    })
    show("(d) library_search %r" % QUERY, msg, ms)
    res = msg["result"]
    assert res["isError"] is False
    first = res["content"][0]["text"].splitlines()[0]
    sid = first.split()[1]
    assert "biblioteca" not in sid, sid

    msg, ms = call("tools/call", mid=4, params={
        "name": "library_open",
        "arguments": {"id": sid},
    })
    show("(d) library_open %s" % sid, msg, ms)
    res = msg["result"]
    assert res["isError"] is False
    print("  OK: opened %s (search anchor from first [card] hit)" % sid)

    # (e) from_seq follow-up: continue from the (d) open's next: trailer
    # (or, if that open was not truncated, from its first block's seq)
    text = res["content"][0]["text"]
    lines = text.rstrip("\n").split("\n")
    m = re.match(r"^next: from_seq=(\d+)$", lines[-1])
    if m:
        n, origin = int(m.group(1)), "trailer"
    else:
        n, origin = int(lines[1][1:lines[1].index("]")]), "first block seq"
    msg, ms = call("tools/call", mid=5, params={
        "name": "library_open",
        "arguments": {"id": sid, "from_seq": n},
    })
    res = msg["result"]
    assert res["isError"] is False, res
    t2 = res["content"][0]["text"]
    l2 = t2.rstrip("\n").split("\n")
    first = int(l2[1][1:l2[1].index("]")])
    assert first >= n, "window must start at seq >= %d, got %d" % (n, first)
    m2 = re.match(r"^next: from_seq=(\d+)$", l2[-1])
    if m2:
        assert int(m2.group(1)) > first, "trailer must point past the start"
    print("  OK: from_seq=%d (from %s) -> first shown seq %d, trailer %s, %.1f ms"
          % (n, origin, first, m2.group(1) if m2 else "none", ms))

    # (f) at="end": window ends at the last event and always includes the last
    # user message and the last non-empty assistant text
    msg, ms = call("tools/call", mid=6, params={
        "name": "library_open",
        "arguments": {"id": sid, "at": "end"},
    })
    res = msg["result"]
    assert res["isError"] is False, res
    t3 = res["content"][0]["text"]
    l3 = t3.rstrip("\n").split("\n")
    has_user = any(re.match(r"^\[\d+\] \[user\] ", l) for l in l3)
    has_assist = any(re.match(r"^\[\d+\] \[model said", l) for l in l3)
    assert has_user, "at=end must include the last [user] message"
    assert has_assist, "at=end must include the last [model said] text"
    trailer = re.match(r"^next: from_seq=\d+$", l3[-1])
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
