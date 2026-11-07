#!/usr/bin/env python3
"""Phase 2 step 5: search the cards index.

Usage: python3 src/search.py <query words...>

Prints one line per result (id, start, cwd, tool/error counts, first
message snippet), then the output size and query time.
"""
import json
import os
import re
import sqlite3
import sys
import time


def fts_query(text):
    words = text.split()
    return " ".join('"%s"' % w.replace('"', "") for w in words if w)


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "library.db")
    q = fts_query(" ".join(sys.argv[1:]))
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    t0 = time.monotonic()
    rows = con.execute(
        """
        SELECT c.id, c.start, c.cwd, c.tool_call_count, c.error_count,
               c.first_message, c.files, c.commit_hashes
        FROM cards_fts f JOIN cards c ON c.id = f.id
        WHERE cards_fts MATCH ?
        ORDER BY rank
        LIMIT 10
        """,
        (q,),
    ).fetchall()
    ms = (time.monotonic() - t0) * 1000
    con.close()
    lines = []
    for r in rows:
        files = json.loads(r[6]) if r[6] else []
        commits = r[7] and json.loads(r[7]) or []
        first = (r[5] or "").replace("\n", " ")[:80]
        lines.append(
            f"{r[0]}  {r[1]}  cwd={r[2]}  calls={r[3]} errors={r[4]}  files={len(files)} commits={len(commits)}  {first}"
        )
    if not lines:
        lines.append("(no results)")
    text = "\n".join(lines) + "\n"
    sys.stdout.write(text)
    print(f"[{len(rows)} results, {len(text.encode('utf-8'))} bytes, {ms:.1f} ms, query: {q}]")


if __name__ == "__main__":
    main()
