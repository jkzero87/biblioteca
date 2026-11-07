#!/usr/bin/env python3
"""Phase 2 (option B): two-level search (recollection + familiarity).

Usage: python3 src/search.py <query words...>

Every query word is quoted as an FTS5 phrase, so dots and hyphens work
(`compatibility.json`, `dsh-recall`, `load.py` -> phrases of adjacent
tokens).

Level 1 (recollection): cards_fts over first_message, files_touched,
bash_commands. Level 2 (familiarity): familiar_fts over the lossless
familiar_terms list, excluding sessions already returned. Max 5 lines
total. Each line: [card]|[familiar], session id, start date, which field
matched, and a <=80-char snippet from FTS5 snippet(). Prints the output
size in bytes and the query time.
"""
import os
import re
import sqlite3
import sys
import time

MAX_LINES = 5
SNIPPET_CHARS = 80
SNIPPET_TOKENS = 24

# column name -> (fts table, column index for snippet(), display name)
CARD_FIELDS = (
    ("first_message", "cards_fts", 1),
    ("files_text", "cards_fts", 2),
    ("commands_text", "cards_fts", 3),
)
FAMILIAR_TABLE = "familiar_fts"


def fts_query(text):
    """Quote every word as an FTS5 phrase (doubles inner quotes)."""
    return " ".join('"%s"' % w.replace('"', '""') for w in text.split() if w)


def snippet_of(con, table, colidx, rowid, q):
    # snippet() is an FTS auxiliary: it needs a MATCH in the same statement.
    row = con.execute(
        f"SELECT snippet({table}, {colidx}, '', '', '', {SNIPPET_TOKENS}) "
        f"FROM {table} WHERE {table} MATCH ? AND rowid = ?",
        (q, rowid),
    ).fetchone()
    if not row or row[0] is None:
        return ""
    s = row[0].replace("\n", " ")
    return s[:SNIPPET_CHARS]


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    db_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "library.db"
    )
    q = fts_query(" ".join(sys.argv[1:]))
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    t0 = time.monotonic()
    lines = []
    seen = set()
    starts = {}

    def start_of(sid):
        if sid not in starts:
            row = con.execute("SELECT start FROM cards WHERE id = ?", (sid,)).fetchone()
            starts[sid] = (row[0] if row else "") or ""
        return starts[sid]

    # Level 1: cards_fts (recollection)
    rows = con.execute(
        "SELECT rowid, id FROM cards_fts WHERE cards_fts MATCH ? ORDER BY rank, rowid", (q,)
    ).fetchall()
    for rowid, sid in rows:
        if len(lines) >= MAX_LINES or sid in seen:
            continue
        seen.add(sid)
        matched = []
        snip = ""
        for name, table, colidx in CARD_FIELDS:
            hit = con.execute(
                f"SELECT 1 FROM {table} WHERE {name} MATCH ? AND rowid = ?", (q, rowid)
            ).fetchone()
            if hit:
                matched.append(name)
                if not snip:
                    snip = snippet_of(con, table, colidx, rowid, q)
        lines.append(
            f"[card] {sid}  {start_of(sid)}  matched={','.join(matched)}  {snip}"
        )

    # Level 2: familiar_fts (familiarity), skipping already-returned sessions
    if len(lines) < MAX_LINES:
        rows = con.execute(
            f"SELECT rowid, id FROM {FAMILIAR_TABLE} "
            f"WHERE {FAMILIAR_TABLE} MATCH ? ORDER BY rank, rowid",
            (q,),
        ).fetchall()
        for rowid, sid in rows:
            if len(lines) >= MAX_LINES or sid in seen:
                continue
            seen.add(sid)
            snip = snippet_of(con, FAMILIAR_TABLE, 1, rowid, q)
            lines.append(
                f"[familiar] {sid}  {start_of(sid)}  matched=familiar_terms  {snip}"
            )

    ms = (time.monotonic() - t0) * 1000
    con.close()
    if not lines:
        lines = ["(no results)"]
    text = "\n".join(lines) + "\n"
    sys.stdout.write(text)
    print(f"[{len(lines)} results, {len(text.encode('utf-8'))} bytes, {ms:.1f} ms, query: {q}]")


if __name__ == "__main__":
    main()
