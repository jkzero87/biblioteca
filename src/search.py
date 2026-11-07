#!/usr/bin/env python3
"""Phase 2b: two-level scored search (recollection + familiarity).

Usage: python3 src/search.py <query words...> [--project NAME]

Every query word is quoted as an FTS5 phrase, so dots and hyphens work
(`compatibility.json`, `dsh-recall`, `load.py` -> phrases of adjacent
tokens).

Two levels, [card] hits always come before [familiar] hits, max 5 lines:
  level 1 (recollection): cards_fts over first_message, files_touched,
                          bash_commands
  level 2 (familiarity):  familiar_fts over the lossless familiar_terms,
                          skipping sessions already returned

Ranking INSIDE each level (design.md 4.4):
  score    = bm25 * recency * salience
  bm25     = -FTS5 rank (BM25 as computed by FTS5; higher is better)
  recency  = 0.98 ** days since last_accessed, or since session end if
             the card was never accessed
  salience = 1 + 0.2 * min(n_errors + n_commits + n_writes, 10)
All four numbers plus the source tag of every matched field are printed
with each result. (n_new_files is recorded but not part of the formula.)

--project NAME restricts both levels to sessions whose project is NAME
(the repo path, or the repo basename).

Returned sessions get access_count += 1 and last_accessed = now; the db
is opened read-write. Session logs are never touched.
"""
import datetime
import json
import os
import sqlite3
import sys
import time

MAX_LINES = 5
SNIPPET_CHARS = 80
SNIPPET_TOKENS = 24

# cards_fts column -> (fts column index for snippet(), source_map key)
CARD_FIELDS = (
    ("first_message", 1, "first_message"),
    ("files_text", 2, "files_touched"),
    ("commands_text", 3, "bash_commands"),
)


def fts_query(text):
    """Quote every word as an FTS5 phrase (doubles inner quotes)."""
    return " ".join('"%s"' % w.replace('"', '""') for w in text.split() if w)


def parse_iso(s):
    return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%S").replace(
        tzinfo=datetime.timezone.utc
    )


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


def factors(con, sid, rank, now):
    """(bm25, recency, salience, score, source_map) for one hit."""
    row = con.execute(
        "SELECT end, last_accessed, error_count, n_commits, n_writes, source_map "
        "FROM cards WHERE id = ?",
        (sid,),
    ).fetchone()
    end, last_accessed, err, ncomm, nwrite, smap_json = row
    bm25 = -rank
    base = last_accessed or end
    if base:
        try:
            days = max(0.0, (now - parse_iso(base)).total_seconds() / 86400.0)
        except ValueError:
            days = 0.0
    else:
        days = 0.0
    recency = 0.98 ** days
    salience = 1 + 0.2 * min((err or 0) + (ncomm or 0) + (nwrite or 0), 10)
    score = bm25 * recency * salience
    smap = json.loads(smap_json) if smap_json else {}
    return bm25, recency, salience, score, smap


def project_match(projs, project):
    """Stored project paths equal to `project` or whose basename equals it.

    Returns (clause, params) appending to a WHERE, or (None, []) if the
    filter was not given.
    """
    if not project:
        return None, []
    matches = [p for p in projs if p == project or os.path.basename(p) == project]
    if not matches:
        return "1 = 0", []  # unknown project: no rows, no error
    marks = ", ".join("?" for _ in matches)
    return f"project IN ({marks})", matches


def match_where(table, q, projs, project):
    w = f"SELECT rowid, id, rank FROM {table} WHERE {table} MATCH ?"
    params = [q]
    extra, p2 = project_match(projs, project)
    if extra:
        w += f" AND id IN (SELECT id FROM cards WHERE {extra})"
        params += p2
    return w, params


def main():
    argv = sys.argv[1:]
    project = None
    words = []
    for a in argv:
        if a.startswith("--project="):
            project = a.split("=", 1)[1]
        elif a == "--project":
            sys.exit("--project needs a value: --project NAME")
        else:
            words.append(a)
    if not words:
        sys.exit(__doc__)
    db_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "library.db"
    )
    q = fts_query(" ".join(words))
    con = sqlite3.connect(f"file:{db_path}?mode=rw", uri=True)
    t0 = time.monotonic()
    now = datetime.datetime.now(datetime.timezone.utc)
    lines = []
    seen = set()
    starts = {}
    projs = [r[0] for r in con.execute("SELECT DISTINCT project FROM cards")]

    def start_of(sid):
        if sid not in starts:
            row = con.execute("SELECT start FROM cards WHERE id = ?", (sid,)).fetchone()
            starts[sid] = (row[0] if row else "") or ""
        return starts[sid]

    def ranked_rows(table, q, project):
        w, params = match_where(table, q, projs, project)
        rows = con.execute(w, params).fetchall()
        scored = []
        for rowid, sid, rank in rows:
            if sid in seen:
                continue
            bm25, recency, salience, score, smap = factors(con, sid, rank, now)
            scored.append((rowid, sid, bm25, recency, salience, score, smap))
        scored.sort(key=lambda r: (-r[5], r[0]))
        return scored

    # Level 1: cards_fts (recollection)
    for rowid, sid, bm25, recency, salience, score, smap in ranked_rows("cards_fts", q, project):
        if len(lines) >= MAX_LINES:
            break
        seen.add(sid)
        tags = []
        snip = ""
        for name, colidx, smap_key in CARD_FIELDS:
            hit = con.execute(
                f"SELECT 1 FROM cards_fts WHERE {name} MATCH ? AND rowid = ?", (q, rowid)
            ).fetchone()
            if hit:
                tags.append(f"{name}:{smap.get(smap_key, 'observed')}")
                if not snip:
                    snip = snippet_of(con, "cards_fts", colidx, rowid, q)
        lines.append(
            f"[card] {sid}  {start_of(sid)}  matched={','.join(tags)}  "
            f"bm25={bm25:.2f} recency={recency:.3f} salience={salience:.1f} "
            f"score={score:.3f}  {snip}"
        )

    # Level 2: familiar_fts (familiarity), skipping already-returned sessions
    for rowid, sid, bm25, recency, salience, score, smap in ranked_rows("familiar_fts", q, project):
        if len(lines) >= MAX_LINES:
            break
        seen.add(sid)
        tag = f"familiar_terms:{smap.get('familiar_terms', 'observed')}"
        snip = snippet_of(con, "familiar_fts", 1, rowid, q)
        lines.append(
            f"[familiar] {sid}  {start_of(sid)}  matched={tag}  "
            f"bm25={bm25:.2f} recency={recency:.3f} salience={salience:.1f} "
            f"score={score:.3f}  {snip}"
        )

    ms = (time.monotonic() - t0) * 1000
    if seen:
        for sid in seen:
            con.execute(
                "UPDATE cards SET access_count = access_count + 1, last_accessed = ? "
                "WHERE id = ?",
                (now.strftime("%Y-%m-%dT%H:%M:%S"), sid),
            )
        con.commit()
    con.close()
    if not lines:
        lines = ["(no results)"]
    text = "\n".join(lines) + "\n"
    sys.stdout.write(text)
    proj = f", project: {project}" if project else ""
    print(f"[{len(lines)} results, {len(text.encode('utf-8'))} bytes, {ms:.1f} ms, query: {q}{proj}]")


if __name__ == "__main__":
    main()
