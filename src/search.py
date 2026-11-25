#!/usr/bin/env python3
"""Phase 2b fix pass: two-level scored search (recollection + familiarity).

Usage: python3 src/search.py <query words...> [--project NAME] [--touch]

Every query word is quoted as an FTS5 phrase, so dots and hyphens work
(`compatibility.json`, `dsh-recall`, `load.py` -> phrases of adjacent
tokens).

The search runs as AND first (every word must match); if it returns
nothing, it is re-run as OR (any word matches) with the same ranking, and
the footer says which query returned the results (mode=and / mode=or).

Three labels in fixed order — [card] > [familiar] > [searched] — max 5 lines:
  level 1 (recollection): cards_fts over first_message, files_touched,
                           bash_commands (retrieval commands never appear
                           here — extract.is_retrieval)
  level 2 (familiarity):  familiar_fts over the lossless familiar_terms,
                           skipping sessions already returned. A hit whose
                           matched familiar terms are ALL retrieval acts is
                           labelled [searched] (it only searched for the term,
                           never used it) and ranks after every [familiar]
                           hit. Nothing is dropped; the 5-line cap still
                           applies.

Ranking INSIDE each level, after FTS returns candidates (top 50 per level):
  score          = bm25 * recency * local_salience
  bm25           = -FTS5 rank (BM25 as computed by FTS5; higher is better)
  recency        = 0.98 ** days since last_accessed, or since session end if
                   the card was never accessed
  local_salience = 1 + 0.2 * min(N, 10), where N is the number of MATCHED
                   items — a command or file in the card that contains a
                   query term (case-insensitive) — whose class is error,
                   commit or write. first_message matches count as class
                   'user' and add no boost.
  session_sal    = the old session-wide salience
                   1 + 0.2 * min(n_errors + n_commits + n_writes, 10);
                   PRINTED FOR COMPARISON ONLY, never used in the score.
Every factor plus the source tag of every matched field are printed with
each result.

--touch (used by the Phase 4 server) is the ONLY mode that records access:
returned sessions get access_count += 1 and last_accessed = now. Default
(no --touch): the db is opened read-only and is never written. Session
logs are never touched.

--project NAME restricts both levels to sessions whose project is NAME
(the repo path, or the repo basename).
"""
import datetime
import json
import os
import sqlite3
import sys
import time

MAX_LINES = 5
MAX_CANDIDATES = 50
SNIPPET_CHARS = 80
SNIPPET_TOKENS = 24

BOOST_CLASSES = ("error", "commit", "write")

# cards_fts column -> (fts column index for snippet(), source_map key)
CARD_FIELDS = (
    ("first_message", 1, "first_message"),
    ("files_text", 2, "files_touched"),
    ("commands_text", 3, "bash_commands"),
)


def fts_query(text, or_mode=False):
    """Quote every word as an FTS5 phrase (doubles inner quotes).

    By default the phrases are AND-combined (every word must match);
    with or_mode=True they are joined with OR (any word matches), so the
    query can be used as a fallback when the AND form matches nothing.
    """
    joiner = " OR " if or_mode else " "
    return joiner.join('"%s"' % w.replace('"', '""') for w in text.split() if w)


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


def factors(con, sid, rank, now, terms):
    """(bm25, recency, session_sal, local_sal, score, source_map) for one hit.

    local_salience counts only MATCHED items (a command or file in the card
    containing a query term, case-insensitive) whose class is error, commit
    or write; first_message matches are class 'user' and add no boost.
    session_sal is the old session-wide value, printed for comparison only.
    """
    row = con.execute(
        "SELECT end, last_accessed, error_count, n_commits, n_writes, "
        "bash_commands, command_classes, files, file_classes, source_map "
        "FROM cards WHERE id = ?",
        (sid,),
    ).fetchone()
    end, last_accessed, err, ncomm, nwrite, bcmds, bcls, files, fcls, smap_json = row
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
    session_sal = 1 + 0.2 * min((err or 0) + (ncomm or 0) + (nwrite or 0), 10)
    n_matched_boost = 0
    for item, cls in zip(json.loads(bcmds or "[]"), json.loads(bcls or "[]")):
        if cls in BOOST_CLASSES and any(t in item.lower() for t in terms):
            n_matched_boost += 1
    for item, cls in zip(json.loads(files or "[]"), json.loads(fcls or "[]")):
        if cls in BOOST_CLASSES and any(t in item.lower() for t in terms):
            n_matched_boost += 1
    local_sal = 1 + 0.2 * min(n_matched_boost, 10)
    score = bm25 * recency * local_sal
    smap = json.loads(smap_json) if smap_json else {}
    return bm25, recency, session_sal, local_sal, score, smap


def project_match(projs, project):
    """Stored project paths equal to `project` or whose basename equals it.

    Returns (clause, params) appending to a WHERE, or (None, []) if the
    filter was not given.
    """
    if not project:
        return None, []
    matches = [p for p in projs if p == project or os.path.basename(p) == project]
    if not matches:
        matches = ["__none__"]
    marks = "?" * len(matches)
    return f"project IN ({marks})", matches


def match_where(table, q, projs, project):
    w = f"SELECT rowid, id, rank FROM {table} WHERE {table} MATCH ?"
    params = [q]
    extra, p2 = project_match(projs, project)
    if extra:
        w += f" AND id IN (SELECT id FROM cards WHERE {extra})"
        params += p2
    return w, params


def run_search(con, words, q, project, now):
    """Run the two-level ranked search with the FTS5 query `q`.

    Level 1 searches cards_fts (recollection), level 2 searches familiar_fts
    (familiarity) for sessions level 1 did not return. Returns
    (lines, seen, card_hits): the result lines (up to MAX_LINES, possibly
    empty), every session id returned, and the ids returned at level 1.
    """
    lines = []
    seen = set()
    card_hits = []
    starts = {}
    terms = [w.lower() for w in words if w]
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
            f = factors(con, sid, rank, now, terms)
            scored.append((rowid, sid, *f))
        scored.sort(key=lambda r: (-r[6], r[0]))
        return scored[:MAX_CANDIDATES]

    # Level 1: cards_fts (recollection)
    for rowid, sid, bm25, recency, session_sal, local_sal, score, smap in ranked_rows("cards_fts", q, project):
        if len(lines) >= MAX_LINES:
            break
        seen.add(sid)
        card_hits.append(sid)
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
            f"bm25={bm25:.2f} recency={recency:.3f} salience={local_sal:.1f} "
            f"session_sal={session_sal:.1f} score={score:.3f}  {snip}"
        )

    # Level 2: familiar_fts (familiarity), skipping already-returned sessions.
    # Which familiar terms of a hit matched is attributed with an in-memory
    # FTS5 table holding the session's terms as separate documents — same
    # tokenizer as familiar_fts, so attribution matches the hit exactly.
    tmp_con = sqlite3.connect(":memory:")
    tmp_con.execute("CREATE VIRTUAL TABLE tmp_terms USING fts5(term)")

    def match_classes(sid):
        """Classes of the familiar terms of `sid` that match a query word."""
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
            # bound parameter: the FTS5 query is the quoted phrase itself
            phrase = '"%s"' % w.replace('"', '""')
            for (t,) in tmp_con.execute(
                "SELECT term FROM tmp_terms WHERE tmp_terms MATCH ?", (phrase,)
            ):
                out.append(cls_of[t])
        return out

    fam = []
    searched = []
    for rowid, sid, bm25, recency, session_sal, local_sal, score, smap in ranked_rows("familiar_fts", q, project):
        cls = match_classes(sid)
        is_searched = bool(cls) and all(c == "retrieval" for c in cls)
        label = "searched" if is_searched else "familiar"
        tag = f"familiar_terms:{smap.get('familiar_terms', 'observed')}"
        snip = snippet_of(con, "familiar_fts", 1, rowid, q)
        line = (
            f"[{label}] {sid}  {start_of(sid)}  matched={tag}  "
            f"bm25={bm25:.2f} recency={recency:.3f} salience={local_sal:.1f} "
            f"session_sal={session_sal:.1f} score={score:.3f}  {snip}"
        )
        if is_searched:
            searched.append((sid, line))
        else:
            fam.append((sid, line))
    # [familiar] first, then [searched]: order [card] > [familiar] >
    # [searched]; nothing is dropped, the 5-line cap still applies.
    for sid, line in (fam + searched)[:MAX_LINES - len(lines)]:
        lines.append(line)
        seen.add(sid)
    tmp_con.close()
    return lines, seen, card_hits


def main():
    argv = sys.argv[1:]
    project = None
    touch = False
    words = []
    for a in argv:
        if a.startswith("--project="):
            project = a.split("=", 1)[1]
        elif a == "--project":
            sys.exit("--project needs a value: --project NAME")
        elif a == "--touch":
            touch = True
        else:
            words.append(a)
    if not words:
        sys.exit(__doc__)
    db_path = os.environ.get("LIBRARY_DB") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "library.db"
    )
    q_and = fts_query(" ".join(words))
    q_or = fts_query(" ".join(words), or_mode=True)
    # default: read-only, no access recorded; --touch is the only writer
    mode = "rw" if touch else "ro"
    con = sqlite3.connect(f"file:{db_path}?mode={mode}", uri=True)
    t0 = time.monotonic()
    now = datetime.datetime.now(datetime.timezone.utc)
    q_used, (lines, seen, _card_hits) = q_and, run_search(con, words, q_and, project, now)
    search_mode = "and"
    if not lines and len(words) > 1:
        # AND matched nothing: retry with any word (OR), ranked by relevance
        lines, seen, _card_hits = run_search(con, words, q_or, project, now)
        if lines:
            q_used, search_mode = q_or, "or"

    ms = (time.monotonic() - t0) * 1000
    if touch and seen:
        for sid in seen:
            con.execute(
                "UPDATE cards SET access_count = access_count + 1, last_accessed = ? "
                "WHERE id = ?",
                (now.strftime("%Y-%m-%dT%H:%M:%S"), sid),
            )
        con.commit()
    con.close()
    n = len(lines)
    if not lines:
        lines = ["(no results)"]
    text = "\n".join(lines) + "\n"
    sys.stdout.write(text)
    proj = f", project: {project}" if project else ""
    touch_note = ", touch" if touch else ""
    print(f"[{n} results, {len(text.encode('utf-8'))} bytes, {ms:.1f} ms, query: {q_used}{proj}{touch_note}, mode={search_mode}]")


if __name__ == "__main__":
    main()
