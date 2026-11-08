#!/usr/bin/env python3
"""Phase 5 step 2: incremental refresh of the library index.

Re-index only the session logs whose size or mtime changed since they were
last indexed (table indexed_logs in data/library.db), or that were never
indexed. Each such session is re-extracted by extract.extract_card, cut at
its last completed turn (a turn/end after the last user message): a turn
still in progress is left out, so the session currently running is never
indexed while it runs. The card row and its two FTS rows are replaced in one
transaction; last_accessed/access_count are carried over from the old card.
A session with no completed turn is skipped and NOT recorded in
indexed_logs, so the next refresh retries it.

Stdlib only; log files are read-only (decompressed with the zstd CLI via
extract.iter_lines). n_new_files is exact when the batch is the whole
library (full rebuild) and approximate otherwise: the reference path set
for sessions not in the batch comes from the path-like entries of their
familiar_terms (the lossless term list), since their raw _paths are not
kept. n_new_files is a display bio field; search never uses it.

Usage:  python3 src/refresh.py            # one refresh, prints the result
"""
import datetime
import json
import os
import sqlite3
import sys
import time

import build_index
import extract

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "data", "library.db")

INDEXED_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS indexed_logs (\n"
    "  path TEXT PRIMARY KEY,\n"
    "  session_id TEXT,\n"
    "  size INTEGER,\n"
    "  mtime REAL,\n"
    "  indexed_at TEXT\n"
    ")"
)


def _cutoff_lines(path):
    """The log's lines up to and including its last completed turn.

    A turn is completed by a turn/end event that followed a user message
    (user/message events with any source kind). Returns None when the log
    has no completed turn (e.g. the session is still in its first turn).
    """
    lines = []
    pending = False
    last_completed = None
    for line in extract.iter_lines(path):
        lines.append(line)
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if not isinstance(ev, dict):
            continue
        et = ev.get("type")
        if et == "user/message":
            pending = True
        elif et == "turn/end":
            if pending:
                last_completed = len(lines) - 1
            pending = False
    if last_completed is None:
        return None
    return lines[: last_completed + 1]


def _path_like(term):
    """Approximation of 'this familiar_terms entry is a file path'."""
    return term.startswith(("/", "~")) and "\n" not in term


def refresh(db=DB_PATH):
    """Re-index changed session logs. Returns (n_checked, n_updated, ms).

    n_checked = session logs found on disk; n_updated = cards (re)written
    in this run. Exceptions propagate; callers that must never fail (the
    server) catch them.
    """
    t0 = time.perf_counter()
    logs = extract.find_logs(extract.DEFAULT_ROOT, [])
    con = sqlite3.connect(db)
    create_schema_and_indexed(con)
    known = dict(
        (path, (sid, size, mtime))
        for path, sid, size, mtime in con.execute(
            "SELECT path, session_id, size, mtime FROM indexed_logs"
        )
    )
    changed = []
    for path in logs:
        try:
            st = os.stat(path)
        except OSError:
            continue
        rec = known.get(path)
        if rec is None or rec[1] != st.st_size or rec[2] != st.st_mtime:
            changed.append((path, st))
    n_updated = 0
    if changed:
        # 1) re-extract the changed sessions, outside the write transaction
        batch = []
        for path, st in changed:
            try:
                upto = _cutoff_lines(path)
            except Exception:
                continue
            if upto is None:
                continue  # no completed turn: skip, retry next refresh
            try:
                card = extract.extract_card(path, lines=upto)
            except Exception:
                continue
            batch.append((path, st, card))
        if batch:
            batch.sort(key=lambda t: (t[2].get("start") or "", t[2]["id"]))
            batch_ids = set(t[2]["id"] for t in batch)
            # 2) reference path set for n_new_files: path-like familiar_terms
            #    of all cards not in this batch (exact for a full rebuild)
            ref = set()
            for sid, terms in con.execute(
                    "SELECT id, familiar_terms FROM cards"):
                if sid in batch_ids:
                    continue
                try:
                    for t in json.loads(terms or "[]"):
                        if _path_like(t):
                            ref.add(t)
                except Exception:
                    pass
            carry = build_index.carry_access(db)
            repos = build_index.find_git_repos()
            seen = set(ref)
            now = datetime.datetime.now(datetime.timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ")
            con.execute("BEGIN IMMEDIATE")
            for path, st, card in batch:
                sid = card["id"]
                paths = card.pop("_paths", set())
                card["n_new_files"] = len(paths - seen)
                seen |= paths
                last_accessed, access_count = carry.get(sid, (None, 0))
                project = build_index.assign_project(
                    card.get("cwd"), card.get("files_touched") or [], repos)
                con.execute("DELETE FROM cards WHERE id = ?", (sid,))
                con.execute("DELETE FROM cards_fts WHERE id = ?", (sid,))
                con.execute("DELETE FROM familiar_fts WHERE id = ?", (sid,))
                build_index.insert_card(con, card, project,
                                       last_accessed, access_count)
                con.execute(
                    "INSERT OR REPLACE INTO indexed_logs VALUES (?,?,?,?,?)",
                    (path, sid, st.st_size, st.st_mtime, now))
                n_updated += 1
            con.commit()
    con.close()
    return len(logs), n_updated, (time.perf_counter() - t0) * 1000.0


def create_schema_and_indexed(con):
    """Make sure every table exists (idempotent; IF NOT EXISTS)."""
    con.executescript(build_index.SCHEMA)
    con.execute(INDEXED_SCHEMA)


def main():
    n_checked, n_updated, ms = refresh(DB_PATH)
    print("refresh: %d logs checked, %d cards updated, %.1f ms"
          % (n_checked, n_updated, ms))
    return 0


if __name__ == "__main__":
    sys.exit(main())
