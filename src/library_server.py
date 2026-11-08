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
import math
import os
import sqlite3
import subprocess
import sys
import time

import extract
import search

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "data", "library.db")
# Per-session rendered chars/token (data/char_ratio.json, built by
# src/measure_char_ratio.py). Read on every library_open call so edits take
# effect without a restart; fallback when the file or the id is missing.
CHAR_RATIO_PATH = os.path.join(ROOT, "data", "char_ratio.json")
CHAR_RATIO_FALLBACK = 2.5

# Token budget, measured in Phase 4 step 3 against llama-server /tokenize
# (5 real Phase 3 queries): worst search was 1350 chars / 702 tokens, worst
# open 7788 chars / 2793 tokens. Caps sized so worst <= 300 / <= 1500 tokens.
SEARCH_CHAR_CAP = 570    # worst library_search output <= 300 tokens
OPEN_CHAR_CAP = 3350     # worst library_open output <= 1500 tokens
# Coarse-to-fine outline (phase 4 fix step 2): library_open with only an id
# returns the card header plus one line per landmark event (user messages
# and tool calls of class write/commit/error). OUTLINE_CHAR_CAP is chosen
# by src/measure_outline.py so the worst outline over the 5 largest
# sessions stays <= 350 tokens; each landmark line's text is clipped to
# OUTLINE_LINE_CHARS.
OUTLINE_LINE_CHARS = 100
OUTLINE_CHAR_CAP = 940  # incl. next: trailer; worst outline over 5 largest sessions = 333 tokens <= 350

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
            "Open a session by id: card header + seq-numbered blocks "
            "([observed], [user], [model said \u2014 verify]). Query anchors "
            "start; from_seq resumes cuts; at=\"end\" reads the tail. Cut "
            "ends: next: from_seq=<n>."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Session id from library_search."},
                "query": {
                    "type": "string",
                    "description": "Optional term narrowing the anchor event.",
                },
                "from_seq": {
                    "type": "integer",
                    "description":
                        "Start the window at the first event with this seq and go "
                        "forward (no anchor search); continues a previous cut.",
                },
                "at": {
                    "type": "string",
                    "enum": ["end"],
                    "description":
                        "at=\"end\": window ends at the last event, extends backward "
                        "to the cap; always includes the last user message and the "
                        "last assistant text.",
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
        seq = ev.get("seq")
        if t == "user/message":
            events.append({"kind": "user", "seq": seq,
                           "text": extract.text_of(data.get("content"))})
        elif t == "assistant/message":
            m = data.get("message") if isinstance(data.get("message"), dict) else {}
            text = extract.visible_text(m)
            # skip messages with no visible text: never render an empty
            # "[model said — verify]" block (reasoning-only / tool-call-only
            # messages carry no user-visible content)
            if text.strip():
                events.append({"kind": "assistant", "seq": seq, "text": text})
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
            e = {"kind": "tool", "seq": seq, "name": name, "cmd": cmd,
                 "result": "", "is_error": False}
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
    # Every block starts with its event seq number.
    tag = "[%s] " % e["seq"] if e.get("seq") is not None else ""
    cls = event_class(e)
    if e["kind"] == "user":
        return [tag + "[user] " + clip(e["text"], MSG_CHARS)]
    if e["kind"] == "assistant":
        return [tag + "[model said \u2014 verify] " + clip(e["text"], MSG_CHARS)]
    err = " (error)" if e["is_error"] else ""
    out = [tag + "[observed] %s [%s]: %s%s" % (e["name"], cls, clip(e["cmd"], TOOL_CALL_CHARS), err)]
    if e["result"]:
        out.append("[observed] result: " + clip(e["result"], RESULT_CHARS))
    return out


def _outline_landmarks(events):
    """Landmark events in session order: user messages and tool calls of
    class write/commit/error. Returns (event, tag, text) with the line text
    clipped to OUTLINE_LINE_CHARS (phase 4 fix step 2)."""
    out = []
    for e in events:
        if e["kind"] == "user":
            out.append((e, "user", clip(e["text"], OUTLINE_LINE_CHARS)))
        elif e["kind"] == "tool":
            cls = event_class(e)
            if cls in ("write", "commit", "error"):
                out.append((e, "observed",
                            clip("%s [%s]: %s" % (e["name"], cls, e["cmd"]),
                                 OUTLINE_LINE_CHARS)))
    return out


def _outline_text(events, header, cap, sid):
    """Coarse-to-fine outline: header + one line per landmark, session
    order. When the full list exceeds cap: keep the first 3 and last 3
    user messages, then fill with the highest-class tool landmarks
    (error > commit > write, session order for ties) until cap is met,
    and state how many landmark lines were omitted."""
    lands = _outline_landmarks(events)
    lines = ["[%s] [%s] %s" % (e.get("seq"), tag, text) for e, tag, text in lands]

    def total(idx, note_=None):
        t = len(header) + sum(len(lines[k]) + 1 for k in idx)
        if note_:
            t += len(note_) + 1
        return t

    kept_idx = list(range(len(lines)))
    note = None
    if total(kept_idx) > cap:
        user_idx = [i for i, (e, tag, _) in enumerate(lands) if tag == "user"]
        kept = set(user_idx[:3] + user_idx[-3:])
        tools = sorted((i for i in range(len(lands)) if i not in kept),
                       key=lambda i: (-CLASS_RANK[event_class(lands[i][0])], i))
        for i in tools:
            kept.add(i)
            if total(sorted(kept)) > cap:
                kept.remove(i)
                break
        kept_idx = sorted(kept)
        note = None
        while True:
            omitted = len(lands) - len(kept_idx)
            note = "[omitted %d landmark line(s)]" % omitted if omitted else None
            if total(kept_idx, note) <= cap:
                break
            # free space, least valuable first: lowest-class kept tool lines,
            # then the last-3 user lines, then the first-3 (reverse session
            # order within each tier)
            kept_set = set(kept_idx)
            drop = []
            seen = set()
            for i in (
                sorted((k for k in kept_idx if lands[k][1] == "observed"),
                       key=lambda i: (CLASS_RANK[event_class(lands[i][0])], i))
                + sorted((k for k in user_idx[-3:] if k in kept_set), reverse=True)
                + sorted((k for k in user_idx[:3] if k in kept_set), reverse=True)
            ):
                if i not in seen:
                    seen.add(i)
                    drop.append(i)
            if not drop:
                note = None
                break
            kept_idx.remove(drop[0])
            kept_idx = sorted(kept_idx)
    out = [header] + [lines[i] for i in kept_idx]
    if note:
        out.append(note)
    # trailer: jump to the first omitted landmark in session order
    kept_set = set(kept_idx)
    for i in range(len(lands)):
        if i not in kept_set:
            trailer = _trailer_line(events, i, sid)
            if trailer is not None:
                out.append(trailer)
            break
    return out


def _char_ratio(sid):
    """Rendered chars/token for sid from data/char_ratio.json; fallback 2.5."""
    try:
        with open(CHAR_RATIO_PATH) as f:
            r = json.load(f).get(sid)
    except (OSError, ValueError):
        r = None
    if isinstance(r, (int, float)) and not isinstance(r, bool) and r > 0:
        return float(r)
    return CHAR_RATIO_FALLBACK


def _trailer_line(events, first_unshown_idx, sid):
    """Exact last line when the window is cut:
    next: from_seq=<n> (<m> events left, ~<t> tokens).
    m = events from first_unshown_idx to the end; t = their full rendered
    chars / the session's measured chars-per-token (fallback 2.5)."""
    if first_unshown_idx is None or first_unshown_idx >= len(events):
        return None
    seq = events[first_unshown_idx].get("seq")
    if seq is None:
        return None
    m = len(events) - first_unshown_idx
    chars = len("\n".join(
        l for e in events[first_unshown_idx:] for l in render_event(e)))
    t = int(math.ceil(chars / _char_ratio(sid)))
    return "next: from_seq=%s (%d events left, ~%d tokens)" % (seq, m, t)


def library_open(db, sid, query, cache, from_seq=None, at=None):
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
    if not events:
        return tool_error("session %s has no events" % sid)

    header = (
        "[card] %s  %s -> %s  project=%s  first: %s"
        % (sid, start, end, project, clip(first_message or "", 100))
    )

    # --- at="end": window ENDS at the last event, extends backwards to the
    # cap; the last user message and the last non-empty assistant text are
    # always included.
    if at == "end":
        last_user = last_assist = None
        for i, e in enumerate(events):
            if e["kind"] == "user":
                last_user = i
            elif e["kind"] == "assistant":
                last_assist = i  # parse_events drops assistant events with no visible text
        required = [i for i in (last_user, last_assist) if i is not None]
        # window start must stay at or before this index so the last user
        # message and last assistant text are always included; when neither
        # exists, the cap alone decides (may cut down to the last event)
        floor = min(required) if required else len(events) - 1
        blocks = [render_event(e) for e in events]
        all_lines = [header] + [l for b in blocks for l in b]
        total_len = sum(len(l) for l in all_lines) + (len(all_lines) - 1)
        # smallest s in [0, floor] such that events[s:] fits the cap
        s = 0
        while s < floor and total_len > OPEN_CHAR_CAP:
            b = blocks[s]
            # dropping block s removes its lines, the newlines inside it, and
            # the one newline after it
            total_len -= sum(len(l) for l in b) + len(b)
            s += 1
        starts = []
        li = 1
        for b in blocks:
            starts.append(li)
            li += len(b)
        out = all_lines[starts[s]:]
        if s > 0:
            # the cut-off prefix is events[0:s]; first unshown event is events[0]
            out.append(_trailer_line(events, 0, sid))
        return tool_ok("\n".join(out) + "\n")

    # --- from_seq: start the window at the first event with seq >= from_seq
    # and go forward (no anchor search).
    if from_seq is not None:
        start = None
        for i, e in enumerate(events):
            if e.get("seq") is not None and e["seq"] >= from_seq:
                start = i
                break
        if start is None:
            return tool_error("no event with seq >= %d in %s" % (from_seq, sid))
        out = [header]
        # always include the requested start event (like the anchor), so the
        # trailer always points past it and chained from_seq calls progress
        out += render_event(events[start])
        last_shown = start
        for i in range(start + 1, len(events)):
            block = render_event(events[i])
            if len("\n".join(out + block)) > OPEN_CHAR_CAP:
                break
            out += block
            last_shown = i
        trailer = _trailer_line(events, last_shown + 1, sid)
        if trailer is not None:
            out.append(trailer)
        return tool_ok("\n".join(out) + "\n")

    # --- default (no query, no from_seq, no at): coarse outline (phase 4
    # fix step 2) — card header plus one line per landmark event, so the
    # caller can jump to detail with from_seq=<seq>
    if query is None or query == "":
        return tool_ok("\n".join(_outline_text(events, header, OUTLINE_CHAR_CAP, sid)) + "\n")

    # --- anchor search: a query narrows the window to the highest-class
    # matching event
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
        return tool_error(
            "no event in %s contains any of: %s" % (sid, ", ".join(terms))
        )
    idx, anchor, rank = best

    out = [header]
    if idx > 0:
        out += render_event(events[idx - 1])
    out += render_event(anchor)
    last_shown = idx
    for i in range(idx + 1, len(events)):
        block = render_event(events[i])
        if len("\n".join(out + block)) > OPEN_CHAR_CAP:
            break
        out += block
        last_shown = i
    trailer = _trailer_line(events, last_shown + 1, sid)
    if trailer is not None:
        out.append(trailer)
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
            from_seq = args.get("from_seq")
            at = args.get("at")
            if from_seq is not None and (
                isinstance(from_seq, bool) or not isinstance(from_seq, int)
            ):
                return tool_error("library_open 'from_seq' must be an integer")
            if at is not None and at != "end":
                return tool_error("library_open 'at' must be \"end\"")
            if from_seq is not None and at is not None:
                return tool_error("library_open: 'from_seq' and 'at' are mutually exclusive")
            try:
                return library_open(db, sid, query, cache, from_seq=from_seq, at=at)
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
