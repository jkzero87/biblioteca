#!/usr/bin/env python3
"""Phase 4 fix step 5: stdio test of src/library_server.py.

Runs entirely on a shadow copy of the library. A temp tree is built with
  <tmp>/data/{library.db,char_ratio.json}   copies of the real data files
  <tmp>/src/{library_server,search,extract,refresh,build_index}.py
and <tmp>/src/library_server.py is spawned; the server resolves its DB
from its own script path, so every db write (pre-search refresh, access
counts) lands in the copy. The real data/library.db is only read (to
make the copy) and never opened for writing by this test.

Over stdio:
  (a) sends server/discover FIRST and checks a -32601 error comes back <1 s;
  (b) initializes with protocolVersion "2025-11-25" + notifications/initialized;
  (c) tools/list;
  (c2) library_search with a real query from the Phase 3 list
       (data/phase3_queries.json), not a biblioteca session;
  (d) library_open on session-c5f86181 with no query -> coarse outline
       (landmark lines with seq, omitted note, next: trailer);
  (e) library_open from_seq=<landmark seq> from (d) -> detail window;
  (f) library_open at="end";
  (g) budget demo on a FRESH server process (in-memory budget starts
      clean): linear walk of c5f86181 - outline, at=end, then from_seq
      jumps following the trailers - printing estimated tokens served
      (chars/2.5) per call and the running total, until the per-session
      budget (4000 tokens / 30 min) is reached; a query call after the
      budget must still return content;
  (h) refresh-then-search: delete SID's indexed_logs row from the COPY,
      then call library_search - the server's pre-search refresh must
      restore the row with the log's current size/mtime; the newest log
      on disk (this test's own session, mid-turn) must end up neither a
      card nor an indexed row.
Prints each response truncated to 300 chars, the elapsed ms, and the
response token count (POST 127.0.0.1:8092/tokenize, with the API key as
an Authorization: Bearer header - the key is never printed).

Stdlib only.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="libstdio-")
DB = os.path.join(TMP, "data", "library.db")
QUERY = "adb"  # from data/phase3_queries.json (Phase 3 list), dsh-scratch
SID = "session-c5f86181-d002-4a54-b7a1-7d647da9c2e4"
TOKENIZE_URL = "http://127.0.0.1:8092/tokenize"
TRAILER_RE = re.compile(r"^next: from_seq=(\d+) \((\d+) events left, ~(\d+) tokens\)$")
LANDMARK_RE = re.compile(r"^\[(\d+)\] \[(?:user|observed)\] ")


def build_shadow_tree():
    """Copy the db, the char ratio and the 5 server modules to TMP; the
    spawned server resolves ROOT from its own path and works on these
    copies, never on the real data/."""
    os.makedirs(os.path.join(TMP, "data"))
    os.makedirs(os.path.join(TMP, "src"))
    for name in ("library.db", "char_ratio.json"):
        shutil.copyfile(os.path.join(ROOT, "data", name),
                        os.path.join(TMP, "data", name))
    for name in ("library_server", "search", "extract",
                 "refresh", "build_index"):
        shutil.copyfile(os.path.join(ROOT, "src", name + ".py"),
                        os.path.join(TMP, "src", name + ".py"))


def tokenize(text):
    """Token count of text from llama-server /tokenize. The API key is
    read from ~/.config/llama/api_key, sent as Authorization: Bearer,
    and never printed."""
    key = open(os.path.expanduser("~/.config/llama/api_key")).read().strip()
    body = json.dumps({"content": text}).encode("utf-8")
    req = urllib.request.Request(TOKENIZE_URL, data=body,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=120) as r:
        return len(json.load(r)["tokens"])


def main():
    build_shadow_tree()
    queries = json.load(open(os.path.join(ROOT, "data", "phase3_queries.json")))
    if QUERY not in queries:
        sys.exit("query %r is not in the Phase 3 list" % QUERY)

    proc = walk = None
    try:
        proc = subprocess.Popen(
            [sys.executable, os.path.join(TMP, "src", "library_server.py")],
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

        # (h) refresh-then-search: delete SID's indexed_logs row from the
        # COPY, then search - the server's pre-search refresh must detect
        # the change and restore the row with the log's current size/mtime
        import sqlite3
        SRC = os.path.join(ROOT, "src")
        if SRC not in sys.path:
            sys.path.insert(0, SRC)
        import extract
        con = sqlite3.connect(DB)
        try:
            row = con.execute("SELECT path, size, mtime FROM indexed_logs "
                             "WHERE session_id=?", (SID,)).fetchone()
            assert row is not None, "SID has no indexed_logs row"
            path_sid = row[0]
            cur = con.execute("DELETE FROM indexed_logs WHERE session_id=?", (SID,))
            assert cur.rowcount == 1, "deleted %r rows, expected 1" % cur.rowcount
            con.commit()
        finally:
            con.close()
        msg, ms = call("tools/call", mid=7, params={
            "name": "library_search",
            "arguments": {"query": QUERY},
        })
        show("(h) library_search after row delete", msg, ms)
        res = msg["result"]
        assert res["isError"] is False, res
        show_tokens("(h) search response", res)
        con = sqlite3.connect(DB)
        try:
            row = con.execute("SELECT path, size, mtime FROM indexed_logs "
                             "WHERE session_id=?", (SID,)).fetchone()
            assert row is not None, "refresh did not restore the row"
            st = os.stat(path_sid)
            assert row[0] == path_sid, row
            assert row[1] == st.st_size, (row[1], st.st_size)
            assert row[2] == st.st_mtime, (row[2], st.st_mtime)
            card_ids = {r[0] for r in con.execute("SELECT id FROM cards")}
            idx_paths = {r[0] for r in con.execute("SELECT path FROM indexed_logs")}
        finally:
            con.close()
        print("  OK: refresh restored %s with current size/mtime (%d B)"
              % (SID, st.st_size))
        # the newest log on disk is this test's own session, mid-turn: the
        # refresh must have skipped it (no completed turn)
        logs = extract.find_logs(extract.DEFAULT_ROOT, [])
        newest = max(logs, key=lambda p: os.stat(p).st_mtime_ns)
        # read only the first line (the session event) with a bounded read:
        # the newest log is live, so zstd -dc may never reach EOF and
        # extract.iter_lines must not be abandoned mid-iteration (its finally
        # waits on the still-running zstd)
        p = subprocess.Popen(
            ["zstd", "-dc", newest], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        first = p.stdout.readline().decode("utf-8", "replace").strip()
        p.kill()
        p.wait()
        p.stdout.close()
        p.stderr.close()
        ev = json.loads(first)
        sid_new = ev.get("id") if ev.get("type") == "session" else None
        assert sid_new, "no session event in newest log"
        assert sid_new not in card_ids, "in-progress session became a card"
        assert newest not in idx_paths, "in-progress session got indexed"
        print("  OK: newest log %s is neither a card nor indexed" % sid_new)

        # (g) budget demo on a FRESH server process: the in-memory per-session
        # budget (4000 tokens / 30 min) starts clean, so the linear walk -
        # outline, at=end, then from_seq jumps following the trailers - shows
        # exactly which call gets the budget message; a query call after the
        # budget must still return content
        walk = subprocess.Popen(
            [sys.executable, os.path.join(TMP, "src", "library_server.py")],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
        )
        wmid = [0]
        walk_cum = [0.0]

        def wcall(args):
            wmid[0] += 1
            walk.stdin.write(json.dumps({
                "jsonrpc": "2.0", "id": wmid[0],
                "method": "tools/call",
                "params": {"name": "library_open",
                           "arguments": dict({"id": SID}, **args)},
            }) + "\n")
            walk.stdin.flush()
            r = json.loads(walk.stdout.readline())["result"]
            assert r["isError"] is False, r
            return r["content"][0]["text"]

        def walk_step(label, args):
            text = wcall(args)
            est = len(text) / 2.5
            walk_cum[0] += est
            print("  %-18s est tokens: %6.0f  cumulative: %7.0f"
                  % (label, est, walk_cum[0]))
            return text

        walk_step("outline", {})
        walk_step("at=end", {"at": "end"})
        budget_at = None
        seq = 0
        for i in range(1, 11):
            text = walk_step("from_seq=%d" % seq, {"from_seq": seq})
            if "Budget for this session reached" in text:
                budget_at = i
                print("  OK: budget message on from_seq call #%d: %s"
                      % (i, text.strip()))
                break
            m = TRAILER_RE.match(text.rstrip("\n").split("\n")[-1])
            if m is None:
                break
            seq = int(m.group(1))
        assert budget_at, "walk never hit the per-session budget"
        text = wcall({"query": "Cargo.toml"})
        wlines = text.rstrip("\n").split("\n")
        assert wlines[0].startswith("[card] %s " % SID), wlines[0]
        assert len(wlines) >= 3, "query call after budget must return a window"
        print("  OK: query call after budget still returned content (%d lines)"
              % len(wlines))
        walk.stdin.close()
        assert walk.wait(timeout=10) == 0
        print("  walk server exited cleanly on EOF (rc=0)")

        proc.stdin.close()
        stderr = proc.stderr.read()
        rc = proc.wait(timeout=10)
        assert rc == 0, "server exited with %r" % rc
        print("server exited cleanly on EOF (rc=0); stderr had %d log lines"
              % len([l for l in stderr.splitlines() if l.strip()]))
    finally:
        for p in (walk, proc):
            if p is not None and p.poll() is None:
                try:
                    p.kill()
                except OSError:
                    pass
        shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    main()
