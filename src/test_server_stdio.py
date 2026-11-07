#!/usr/bin/env python3
"""Phase 4, step 2: stdio test of src/library_server.py.

Spawns the server and, over stdio:
  (a) sends server/discover FIRST and checks a -32601 error comes back <1 s;
  (b) initializes with protocolVersion "2025-11-25" + notifications/initialized;
  (c) tools/list;
  (d) library_search and library_open with a real query from the Phase 3 list
      (data/phase3_queries.json), not a biblioteca session.
Prints each response truncated to 300 chars and the elapsed ms.

Stdlib only; the db is opened by the server (which records access for the
returned [card] hits, as it must).
"""
import json
import os
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

    proc.stdin.close()
    stderr = proc.stderr.read()
    rc = proc.wait(timeout=10)
    assert rc == 0, "server exited with %r" % rc
    print("server exited cleanly on EOF (rc=0); stderr had %d log lines"
          % len([l for l in stderr.splitlines() if l.strip()]))


if __name__ == "__main__":
    main()
