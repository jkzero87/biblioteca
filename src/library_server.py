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
# Coarse-to-fine outline (phase 4 fix step 2; outline fix 2): library_open
# with only an id returns the card header, then the last 3 user messages,
# then the first user message, then tool landmark lines by class
# (error > commit > test > write, ties -> later in session, since later
# results are closer to the end state), each with its outcome, all
# rendered in session order until the cap is met. User lines are clipped
# to OUTLINE_USER_CHARS, tool lines to OUTLINE_LINE_CHARS. User messages
# starting with "<system-reminder>" are harness text and never appear.
# OUTLINE_CHAR_CAP is chosen by src/measure_outline.py so the worst
# outline over the 5 largest sessions stays <= 350 tokens.
OUTLINE_USER_CHARS = 60
OUTLINE_LINE_CHARS = 100
OUTLINE_CHAR_CAP = 723  # incl. next: trailer; outline fix 2: worst over 5 largest sessions = 322 tokens <= 350 (803 was 356)
# Landmark outcome (phase 4 fix 2 step 2): appended after " -> " to each
# tool-call landmark line - "error" if isError, else the first non-empty
# result line containing a digit or an outcome keyword, clipped to
# OUTCOME_CHARS; nothing if no line qualifies.
OUTCOME_CHARS = 60
OUTCOME_KEYWORDS = ("passed", "failed", "error", "ok", "finished",
                    "warning", "created", "committed")
# Test-run outcome (outline fix 2 step 2): first result line containing
# "passed", "failed", "test result", "ok" or "FAILED" (case-insensitive,
# "FAILED" == lowercased "failed"), clipped to OUTCOME_CHARS (<= 60 chars).
TEST_OUTCOME_KEYWORDS = ("passed", "failed", "test result", "ok")
# Per-session read budget (phase 4 fix 2 step 3): library_open serves at
# most BUDGET_TOKENS (estimated as chars/2.5) within a BUDGET_WINDOW_S
# window from the first serve for that session. from_seq calls without a
# query are refused once the budget is spent; outline, at="end" and query
# calls are always allowed but still count toward it.
BUDGET_TOKENS = 4000
BUDGET_WINDOW_S = 30 * 60
_BUDGET = {}  # sid -> [first_serve_epoch, tokens_served_estimate]

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
            "Search past work sessions. Returns ≤5 one-line cards. Then use "
            "library_open on an id."
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
            "Open a past session. No args = outline (~300 tok). "
            "from_seq=<seq> for detail; at='end' for how it ended. "
            "from_seq without query stops after ~4000 tokens served in 30 min."
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

CLASS_RANK = {"error": 4, "commit": 3, "test": 2, "write": 1, "read": 1, "other": 0}


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
        if any(mk in e["cmd"] for mk in extract.TEST_MARKERS):
            return "test"
        if any(mk in e["cmd"] for mk in extract.WRITE_MARKERS):
            return "write"
        return "other"
    if e["name"] in extract.WRITE_TOOLS:
        return "write"
    if e["name"] in extract.FILE_TOOLS:
        return "read"
    return "other"


def _is_harness_user(e):
    """Harness text, never shown as [user] (outline fix 2 step 2): user
    messages whose text starts with "<system-reminder>" after stripping
    whitespace (the harness injects them, the user did not type them)."""
    return e.get("text", "").strip().startswith("<system-reminder>")


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


def _outcome(result):
    """Outcome text for a tool-call landmark line (phase 4 fix 2 step 2):
    the first non-empty result line containing a digit or one of
    OUTCOME_KEYWORDS (case-insensitive), clipped to OUTCOME_CHARS; None
    when no line qualifies. Callers use "error" directly for isError."""
    if not result:
        return None
    for line in result.split("\n"):
        line = line.strip()
        if not line:
            continue
        low = line.lower()
        if any(ch.isdigit() for ch in line) or any(k in low for k in OUTCOME_KEYWORDS):
            return line[:OUTCOME_CHARS]
    return None


def _test_outcome(result):
    """Outcome for a test-run landmark (outline fix 2 step 2): the first
    non-empty result line containing "passed", "failed", "test result",
    "ok" or "FAILED" (case-insensitive), clipped to OUTCOME_CHARS (<= 60
    chars); None when no line qualifies."""
    if not result:
        return None
    for line in result.split("\n"):
        line = line.strip()
        if not line:
            continue
        if any(k in line.lower() for k in TEST_OUTCOME_KEYWORDS):
            return line[:OUTCOME_CHARS]
    return None


def _outline_landmarks(events):
    """Landmark events in session order: user messages and tool calls of
    class write/commit/test/error that carry an outcome. Returns (event,
    tag, text): user text clipped to OUTLINE_USER_CHARS; tool text is
    "name [cls]: cmd -> outcome" with the cmd clipped so the whole line
    stays within OUTLINE_LINE_CHARS (never the outcome). Tool events
    without an outcome are not landmarks. User messages that are harness
    text (<system-reminder>, outline fix 2) are not landmarks."""
    out = []
    for e in events:
        if e["kind"] == "user":
            if _is_harness_user(e):
                continue
            # clip appends an ellipsis, so clip to n-1 to stay <= n chars
            out.append((e, "user", clip(e["text"], OUTLINE_USER_CHARS - 1)))
        elif e["kind"] == "tool":
            cls = event_class(e)
            if cls in ("write", "commit", "error", "test"):
                if cls == "error":
                    out_txt = "error"
                elif cls == "test":
                    out_txt = _test_outcome(e.get("result") or "")
                else:
                    out_txt = _outcome(e.get("result") or "")
                if out_txt:
                    base = "%s [%s]: %s" % (e["name"], cls, e["cmd"])
                    room = max(10, OUTLINE_LINE_CHARS - 4 - len(out_txt))
                    out.append((e, "observed", clip(base, room) + " \u2192 " + out_txt))
    return out


def _outline_text(events, header, cap, sid):
    """Coarse-to-fine outline (outline fix 2): header, then the last 3
    user messages, then the first user message, then tool landmark lines
    by class (error > commit > test > write, ties -> later in session,
    since later results are closer to the end state), each with its
    outcome; all rendered in session order until the cap is met. The
    omitted lines are counted per kind (user / tool) in the note."""
    lands = _outline_landmarks(events)
    lines = ["[%s] [%s] %s" % (e.get("seq"), tag, text) for e, tag, text in lands]

    def total(idx, note_=None):
        t = len(header) + sum(len(lines[k]) + 1 for k in idx)
        if note_:
            t += len(note_) + 1
        return t

    user_idx = [i for i, (e, tag, _) in enumerate(lands) if tag == "user"]
    tool_idx = sorted((i for i, (e, tag, _) in enumerate(lands) if tag == "observed"),
                      key=lambda i: (-CLASS_RANK[event_class(lands[i][0])], -i))
    user_set = set(user_idx)
    tool_set = set(tool_idx)

    def note_for(idx):
        omitted_u = len(user_set - idx)
        omitted_t = len(tool_set - idx)
        if omitted_u or omitted_t:
            return "[omitted %d user, %d tool line(s)]" % (omitted_u, omitted_t)
        return None
    # priority: last 3 user messages, then the first one
    must = user_idx[-3:]
    if user_idx and user_idx[0] not in must:
        must.append(user_idx[0])
    kept = []
    for i in must:
        if total(sorted(kept + [i])) <= cap:
            kept.append(i)
    # then tool landmarks by class, until the cap is met (all tool lines are
    # <= OUTLINE_LINE_CHARS, so the first line that does not fit stops the
    # run: none after it can fit either)
    for i in tool_idx:
        if total(sorted(kept + [i])) <= cap:
            kept.append(i)
        else:
            break
    kept_set = set(kept)
    kept_idx = sorted(kept)
    note = note_for(kept_set)
    # when still over the cap, drop lines in reverse priority: tool lines
    # first (lowest priority), then user fillers, last-added first
    while note and total(kept_idx, note) > cap:
        if not kept:
            note = None
            break
        kept.pop()
        kept_set = set(kept)
        kept_idx = sorted(kept)
        note = note_for(kept_set)
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


def _budget_check(sid):
    """(spent, tokens_served_int, minutes_elapsed) for the in-memory
    per-session budget; the budget resets once the 30-min window from the
    first serve has elapsed (phase 4 fix 2 step 3)."""
    rec = _BUDGET.get(sid)
    if rec is None:
        return False, 0, 0
    now = time.time()
    if now - rec[0] > BUDGET_WINDOW_S:
        del _BUDGET[sid]
        return False, 0, 0
    return rec[1] >= BUDGET_TOKENS, int(rec[1]), int((now - rec[0]) // 60)


def _budget_add(sid, text):
    """Count len(text)/2.5 tokens toward the session budget, opening a
    fresh 30-min window when the previous one has elapsed."""
    now = time.time()
    rec = _BUDGET.get(sid)
    if rec is None or now - rec[0] > BUDGET_WINDOW_S:
        _BUDGET[sid] = [now, len(text) / 2.5]
    else:
        rec[1] += len(text) / 2.5


def _serve(sid, text):
    """Record served text against the session budget and return it."""
    _budget_add(sid, text)
    return tool_ok(text)


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
    # always included, and the whole output (incl. trailer) respects
    # OPEN_CHAR_CAP: when the required content does not fit, the assistant
    # text is truncated first (keep its start, marked "[…cut]"), then the
    # user message (phase 4 fix 2 step 1).
    if at == "end":
        last_user = last_assist = None
        for i, e in enumerate(events):
            if e["kind"] == "user" and not _is_harness_user(e):
                last_user = i
            elif e["kind"] == "assistant":
                last_assist = i  # parse_events drops assistant events with no visible text
        # harness user events (<system-reminder>, outline fix 2) are never
        # rendered as [user]: they cost nothing in the window
        blocks = [render_event(e)
                  if not (e["kind"] == "user" and _is_harness_user(e)) else []
                  for e in events]
        # largest tail that fits the cap: extend backward from the last
        # event while it still fits
        s0 = len(events) - 1
        total = sum(len(l) for l in blocks[s0])
        while (s0 > 0 and blocks[s0 - 1]
               and total + 1 + sum(len(l) for l in blocks[s0 - 1])
               <= OPEN_CHAR_CAP):
            total += 1 + sum(len(l) for l in blocks[s0 - 1])
            s0 -= 1
        # the required events may sit before the cap window: include them
        # (session order) even though they fall outside it
        idxs = sorted(i for i in (last_user, last_assist) if i is not None and i < s0)
        idxs += list(range(s0, len(events)))
        lines = [l for i in idxs for l in blocks[i]]
        pos = {}
        p = 0
        for i in idxs:
            pos[i] = p
            p += len(blocks[i])
        total = sum(len(l) for l in lines) + max(0, len(lines) - 1)
        shown = set(idxs)
        # harness user events are never shown: the trailer passes them even
        # when they fall before the window
        for i, e in enumerate(events):
            if e["kind"] == "user" and _is_harness_user(e):
                shown.add(i)
        first_unshown = next((i for i in range(len(events)) if i not in shown), None)
        trailer = _trailer_line(events, first_unshown, sid) \
            if first_unshown is not None else None
        if trailer:
            total += len(trailer) + 1
        if total > OPEN_CHAR_CAP:
            # the required content does not fit: truncate the assistant text
            # first (keep its start, mark "[…cut]"), then the user message
            marker = " […cut]"
            for i in (last_assist, last_user):
                if i is None or i not in pos or total <= OPEN_CHAR_CAP:
                    continue
                body = clip(events[i]["text"], MSG_CHARS)
                line = lines[pos[i]]
                keep = max(0, len(body) - (total - OPEN_CHAR_CAP) - len(marker))
                new = line[:len(line) - len(body)] + body[:keep] + marker
                total += len(new) - len(line)
                lines[pos[i]] = new
        out = lines + ([trailer] if trailer else [])
        return _serve(sid, "\n".join(out) + "\n")

    # --- from_seq: start the window at the first event with seq >= from_seq
    # and go forward (no anchor search).
    if from_seq is not None:
        spent, served, mins = _budget_check(sid)
        if spent and not query:
            return tool_ok("Budget for this session reached (~%d tokens served in %d min). Answer with what you have, or call library_open with id and query=<what you need> for one targeted window." % (served, mins))
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
        return _serve(sid, "\n".join(out) + "\n")

    # --- default (no query, no from_seq, no at): coarse outline (phase 4
    # fix step 2) — card header plus one line per landmark event, so the
    # caller can jump to detail with from_seq=<seq>
    if query is None or query == "":
        return _serve(sid, "\n".join(_outline_text(events, header, OUTLINE_CHAR_CAP, sid)) + "\n")

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
    return _serve(sid, "\n".join(out) + "\n")


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
