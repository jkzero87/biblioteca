#!/usr/bin/env python3
"""Phase 4: resident biblioteca MCP server (stdio transport, stdlib only).

Protocol (MCP spec, stdio transport):
- read stdin line by line; one JSON-RPC 2.0 message per line, UTF-8.
- stdout carries ONLY MCP messages, flushed after each.
- all logging goes to stderr.
- exit cleanly when stdin reaches EOF.

Wiring:
- initialize: protocolVersion = client's requested version if it is in
  ["2025-11-25","2025-06-18","2025-03-26","2024-11-05"], else "2025-11-25".
- notifications (no id) never get a reply.
- ping -> {}; any other unknown request -> error -32601 immediately.
- the dsh client strips DSH_* env vars, so NO env var is read; the db path
  is resolved from this script's own absolute path.
- the db is opened once and reopened before each call when its mtime or
  inode changed (atomic replace by the sleep job is picked up).
"""
import collections
import datetime
import json
import os
import sqlite3
import subprocess
import sys
import time

import extract
import search

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "data", "library.db")

# Token budget, measured in Phase 4 step 3 against llama-server /tokenize
# (5 real Phase 3 queries): worst search was 1350 chars / 702 tokens, worst
# open 7788 chars / 2793 tokens. Caps sized so worst <= 300 / <= 1500 tokens.
SEARCH_CHAR_CAP = 570    # worst library_search output <= 300 tokens
OPEN_CHAR_CAP = 3350     # worst library_open output <= 1500 tokens

SERVER_VERSION = "0.1.0"
SUPPORTED_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_VERSION = "2025-11-25"

TOOL_CALL_CHARS = 300
RESULT_CHARS = 1200
MSG_CHARS = 300

TOOLS = [
    {
        "name": "library_search",
        "description": (
            "Search your work library (past sessions) for a query. Up to 5 "
            "one-line hits labelled [card], [familiar] or [searched] with "
            "scoring factors. Use it before re-investigating anything."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Words to search for."},
                "project": {
                    "type": "string",
                    "description": "Restrict to this project (path or repo basename).",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "library_open",
        "description": (
            "Open a session by id: card header plus events from one before the "
            "salient anchor onward, labelled [observed], [user] or "
            "[model said \u2014 verify]. Optional query anchors on events "
            "containing its terms."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Session id from library_search."},
                "query": {
                    "type": "string",
                    "description": "Optional term narrowing the anchor event.",
                },
            },
            "required": ["id"],
            "additionalProperties": False,
        },
    },
]

CLASS_RANK = {"error": 4, "commit": 3, "write": 2, "read": 1, "other": 0}


def log(msg):
    print("[biblioteca] %s" % msg, file=sys.stderr, flush=True)


def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def clip(s, n):
    s = s.replace("\n", " ").strip()
    return s[:n] + ("\u2026" if len(s) > n else "")


class Db:
    """One connection, reopened when the db file's mtime or inode changes."""

    def __init__(self, path):
        self.path = path
        self._key = None
        self._con = None

    def con(self):
        st = os.stat(self.path)
        key = (st.st_dev, st.st_ino, st.st_mtime_ns)
        if self._con is None or key != self._key:
            if self._con is not None:
                self._con.close()
            self._con = sqlite3.connect("file:%s?mode=rw" % self.path, uri=True)
            self._key = key
            log("db opened (inode %d, mtime %d)" % (st.st_ino, st.st_mtime_ns))
        return self._con

    def close(self):
        if self._con is not None:
            self._con.close()
            self._con = None


# ---------------------------------------------------------------- search

def library_search(db, query, project):
    """Same two-level logic as src/search.py main(); records access only for
    the returned [card] hits (the server is the --touch consumer)."""
    words = query.split()
    if not words:
        return tool_error("empty query")
    t0 = time.monotonic()
    con = db.con()
    q = search.fts_query(" ".join(words))
    terms = [w.lower() for w in words]
    now = datetime.datetime.now(datetime.timezone.utc)
    lines = []
    seen = set()
    card_hits = []
    starts = {}
    projs = [r[0] for r in con.execute("SELECT DISTINCT project FROM cards")]

    def start_of(sid):
        if sid not in starts:
            row = con.execute(
                "SELECT start FROM cards WHERE id = ?", (sid,)
            ).fetchone()
            starts[sid] = (row[0] if row else "") or ""
        return starts[sid]

    def ranked_rows(table):
        w, params = search.match_where(table, q, projs, project)
        rows = con.execute(w, params).fetchall()
        scored = []
        for rowid, sid, rank in rows:
            if sid in seen:
                continue
            f = search.factors(con, sid, rank, now, terms)
            scored.append((rowid, sid, *f))
        scored.sort(key=lambda r: (-r[6], r[0]))
        return scored[:search.MAX_CANDIDATES]

    # Level 1: cards_fts (recollection)
    for rowid, sid, bm25, recency, session_sal, local_sal, score, smap in ranked_rows("cards_fts"):
        if len(lines) >= search.MAX_LINES:
            break
        seen.add(sid)
        tags = []
        snip = ""
        for name, colidx, smap_key in search.CARD_FIELDS:
            hit = con.execute(
                "SELECT 1 FROM cards_fts WHERE %s MATCH ? AND rowid = ?" % name,
                (q, rowid),
            ).fetchone()
            if hit:
                tags.append("%s:%s" % (name, smap.get(smap_key, "observed")))
                if not snip:
                    snip = search.snippet_of(con, "cards_fts", colidx, rowid, q)
        lines.append(
            "[card] %s  %s  matched=%s  bm25=%.2f recency=%.3f salience=%.1f "
            "session_sal=%.1f score=%.3f  %s"
            % (sid, start_of(sid), ",".join(tags), bm25, recency, local_sal,
               session_sal, score, snip)
        )
        card_hits.append(sid)

    # Level 2: familiar_fts (familiarity), skipping already-returned sessions.
    tmp_con = sqlite3.connect(":memory:")
    tmp_con.execute("CREATE VIRTUAL TABLE tmp_terms USING fts5(term)")

    def match_classes(sid):
        row = con.execute(
            "SELECT familiar_terms, familiar_classes FROM cards WHERE id = ?",
            (sid,),
        ).fetchone()
        if not row:
            return []
        fts_terms = json.loads(row[0] or "[]")
        cls_of = dict(zip(fts_terms, json.loads(row[1] or "[]")))
        tmp_con.execute("DELETE FROM tmp_terms")
        for t in fts_terms:
            tmp_con.execute("INSERT INTO tmp_terms VALUES (?)", (t,))
        out = []
        for w in words:
            phrase = '"%s"' % w.replace('"', '""')
            for (t,) in tmp_con.execute(
                "SELECT term FROM tmp_terms WHERE tmp_terms MATCH ?", (phrase,)
            ):
                out.append(cls_of[t])
        return out

    fam = []
    searched = []
    for rowid, sid, bm25, recency, session_sal, local_sal, score, smap in ranked_rows("familiar_fts"):
        cls = match_classes(sid)
        is_searched = bool(cls) and all(c == "retrieval" for c in cls)
        label = "searched" if is_searched else "familiar"
        tag = "familiar_terms:%s" % smap.get("familiar_terms", "observed")
        snip = search.snippet_of(con, "familiar_fts", 1, rowid, q)
        line = (
            "[%s] %s  %s  matched=%s  bm25=%.2f recency=%.3f salience=%.1f "
            "session_sal=%.1f score=%.3f  %s"
            % (label, sid, start_of(sid), tag, bm25, recency, local_sal,
               session_sal, score, snip)
        )
        if is_searched:
            searched.append((sid, line))
        else:
            fam.append((sid, line))
    tmp_con.close()

    for sid, line in (fam + searched)[:search.MAX_LINES - len(lines)]:
        lines.append(line)
        seen.add(sid)

    # access recorded only for returned [card] hits (same as --touch)
    if card_hits:
        for sid in card_hits:
            con.execute(
                "UPDATE cards SET access_count = access_count + 1, "
                "last_accessed = ? WHERE id = ?",
                (now.strftime("%Y-%m-%dT%H:%M:%S"), sid),
            )
        con.commit()

    if not lines:
        lines = ["(no results)"]
    ms = (time.monotonic() - t0) * 1000
    trailer = "[%d results, %d bytes, %.1f ms, project: %s]" % (
        len(lines), sum(len(l.encode("utf-8")) for l in lines) + len(lines) - 1,
        ms, project or "-",
    )
    text = "\n".join(lines) + "\n" + trailer
    if len(text) > SEARCH_CHAR_CAP:
        # keep as many whole lines as fit under the cap, drop the rest
        trailer = "[truncated at %d chars: %s]" % (SEARCH_CHAR_CAP, trailer)
        kept = []
        for l in lines:
            cand = "\n".join(kept + [l]) + "\n" + trailer
            if kept and len(cand) > SEARCH_CHAR_CAP:
                break
            kept.append(l)
        text = "\n".join(kept) + "\n" + trailer
    return tool_ok(text)


# ---------------------------------------------------------------- open

def decode_session(path, cache):
    """Decompress with zstd -dc; LRU cache of the last 8 decoded sessions."""
    st = os.stat(path)
    key = (st.st_dev, st.st_ino, st.st_mtime_ns)
    if key in cache:
        cache.move_to_end(key)
        return cache[key]
    proc = subprocess.run(
        ["zstd", "-dc", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "zstd exit %d: %s"
            % (proc.returncode, proc.stderr.decode("utf-8", "replace")[:200])
        )
    events = parse_events(proc.stdout.decode("utf-8", "replace"))
    cache[key] = events
    cache.move_to_end(key)
    while len(cache) > 8:
        cache.popitem(last=False)
    return events


def parse_events(text):
    events = []
    calls = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if not isinstance(ev, dict):
            continue
        t = ev.get("type")
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if t == "user/message":
            events.append({"kind": "user", "text": extract.text_of(data.get("content"))})
        elif t == "assistant/message":
            m = data.get("message") if isinstance(data.get("message"), dict) else {}
            events.append({"kind": "assistant", "text": extract.text_of(m.get("content"))})
        elif t == "tool/call":
            name = data.get("name") or ""
            args = extract.args_of(ev)
            cmd = str(args.get("command", "")) if name == "bash" else json.dumps(
                args, ensure_ascii=False
            )
            m = data.get("message") if isinstance(data.get("message"), dict) else {}
            cid = m.get("toolCallId")
            if cid is None:
                cid = data.get("callId")
            e = {"kind": "tool", "name": name, "cmd": cmd, "result": "", "is_error": False}
            events.append(e)
            if cid is not None:
                calls[str(cid)] = e
        elif t == "tool/result":
            m = data.get("message") if isinstance(data.get("message"), dict) else {}
            cid = m.get("toolCallId")
            if cid is None:
                cid = data.get("callId")
            e = calls.get(str(cid)) if cid is not None else None
            if e is not None:
                e["result"] = extract.text_of(m.get("content"))
                e["is_error"] = m.get("isError") is True
    return events


def event_class(e):
    if e["kind"] != "tool":
        return "other"
    if e["is_error"]:
        return "error"
    if e["name"] == "bash":
        if any(mk in e["cmd"] for mk in extract.COMMIT_MARKERS):
            return "commit"
        if any(mk in e["cmd"] for mk in extract.WRITE_MARKERS):
            return "write"
        return "other"
    if e["name"] in extract.WRITE_TOOLS:
        return "write"
    if e["name"] in extract.FILE_TOOLS:
        return "read"
    return "other"


def render_event(e):
    cls = event_class(e)
    if e["kind"] == "user":
        return ["[user] " + clip(e["text"], MSG_CHARS)]
    if e["kind"] == "assistant":
        return ["[model said \u2014 verify] " + clip(e["text"], MSG_CHARS)]
    err = " (error)" if e["is_error"] else ""
    out = ["[observed] %s [%s]: %s%s" % (e["name"], cls, clip(e["cmd"], TOOL_CALL_CHARS), err)]
    if e["result"]:
        out.append("[observed] result: " + clip(e["result"], RESULT_CHARS))
    return out


def library_open(db, sid, query, cache):
    con = db.con()
    row = con.execute(
        "SELECT source, start, end, project, first_message FROM cards WHERE id = ?",
        (sid,),
    ).fetchone()
    if not row:
        return tool_error("unknown session id: %s" % sid)
    source, start, end, project, first_message = row
    try:
        events = decode_session(source, cache)
    except Exception as exc:
        return tool_error("cannot decode %s: %s" % (os.path.basename(source), exc))

    terms = [w.lower() for w in (query or "").split() if w]
    best = None  # (index, event, rank)
    for i, e in enumerate(events):
        rank = CLASS_RANK[event_class(e)]
        if terms:
            hay = (e.get("text", "") + e.get("cmd", "") + e.get("result", "")).lower()
            if not any(t in hay for t in terms):
                continue
        if best is None or rank > best[2]:
            best = (i, e, rank)
    if best is None:
        if terms:
            return tool_error(
                "no event in %s contains any of: %s" % (sid, ", ".join(terms))
            )
        return tool_error("session %s has no events" % sid)
    idx, anchor, rank = best

    header = (
        "[card] %s  %s -> %s  project=%s  first: %s"
        % (sid, start, end, project, clip(first_message or "", 100))
    )
    out = [header]
    if idx > 0:
        out += render_event(events[idx - 1])
    out += render_event(anchor)
    truncated = False
    for e in events[idx + 1:]:
        block = render_event(e)
        if len("\n".join(out + block)) > OPEN_CHAR_CAP:
            truncated = True
            break
        out += block
    if truncated:
        out.append("[truncated at %d chars]" % OPEN_CHAR_CAP)
    return tool_ok("\n".join(out) + "\n")


# ---------------------------------------------------------------- dispatch

def tool_ok(text):
    return {"content": [{"type": "text", "text": text}], "isError": False}


def tool_error(text):
    return {"content": [{"type": "text", "text": text}], "isError": True}


def dispatch(method, params, db, cache):
    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in SUPPORTED_VERSIONS else DEFAULT_VERSION
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "biblioteca", "version": SERVER_VERSION},
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if name == "library_search":
            query = args.get("query")
            project = args.get("project")
            if not isinstance(query, str) or not query.strip():
                return tool_error("library_search needs a non-empty string 'query'")
            if project is not None and not isinstance(project, str):
                return tool_error("library_search 'project' must be a string")
            try:
                return library_search(db, query, project)
            except Exception as exc:
                return tool_error("library_search failed: %s" % exc)
        if name == "library_open":
            sid = args.get("id")
            query = args.get("query")
            if not isinstance(sid, str) or not sid:
                return tool_error("library_open needs a non-empty string 'id'")
            if query is not None and not isinstance(query, str):
                return tool_error("library_open 'query' must be a string")
            try:
                return library_open(db, sid, query, cache)
            except Exception as exc:
                return tool_error("library_open failed: %s" % exc)
        return tool_error("unknown tool: %s" % name)
    return None  # unknown request


def main():
    db = Db(DB_PATH)
    cache = collections.OrderedDict()
    log("starting (db %s)" % DB_PATH)
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            t0 = time.monotonic()
            try:
                msg = json.loads(line)
            except Exception:
                send({"jsonrpc": "2.0", "id": None,
                      "error": {"code": -32700, "message": "Parse error"}})
                log("parse error in: %s" % clip(line, 120))
                continue
            if not isinstance(msg, dict):
                send({"jsonrpc": "2.0", "id": None,
                      "error": {"code": -32600, "message": "Invalid request"}})
                continue
            method = msg.get("method")
            mid = msg.get("id")
            ms = (time.monotonic() - t0) * 1000
            if "id" not in msg:
                # notification (e.g. notifications/initialized): never reply
                log("notification %s (%.1f ms)" % (method, ms))
                continue
            result = dispatch(method, msg.get("params") or {}, db, cache)
            if result is None:
                send({"jsonrpc": "2.0", "id": mid,
                      "error": {"code": -32601, "message": "Method not found"}})
                log("unknown request %s -> -32601 (%.1f ms)" % (method, ms))
            else:
                send({"jsonrpc": "2.0", "id": mid, "result": result})
                log("%s id=%s -> ok (%.1f ms)" % (method, mid, ms))
    finally:
        db.close()
        log("stdin EOF, exiting")
    sys.exit(0)


if __name__ == "__main__":
    main()
