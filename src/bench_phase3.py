#!/usr/bin/env python3
"""Phase 3 benchmark: search latency in three process modes + episode cost.

Stdlib only; zstd CLI for decompression. No network, no model, no sudo.
search.py is always run WITHOUT --touch; the db is opened read-only.

Modes:
  A new-process:    subprocess `python3 src/search.py <words>` per query
  B one-proc-disk:  one process, sqlite read-only on data/library.db
  C one-proc-mem:   one process, db attached into :memory:
Cold file cache: NOT measured (dropping the page cache needs root).

run_search() below mirrors src/search.py main() (lines 180-277) exactly, so
modes B and C run the same query logic as the CLI.
"""
import datetime
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "library.db")
QUERIES = json.load(open(os.path.join(ROOT, "data", "phase3_queries.json")))
TOP5 = json.load(open(os.path.join(ROOT, "data", "phase3_top5.json")))
OUT = os.path.join(ROOT, "data", "phase3_results.json")
EXC_DIR = os.path.join(ROOT, "data", "episodes")

MAX_LINES = 5
MAX_CANDIDATES = 50
SNIPPET_CHARS = 80
SNIPPET_TOKENS = 24
BOOST_CLASSES = ("error", "commit", "write")
CARD_FIELDS = (
    ("first_message", 1, "first_message"),
    ("files_text", 2, "files_touched"),
    ("commands_text", 3, "bash_commands"),
)
EXCERPT_BYTES = 65536  # excerpt = first 64 KiB of the decompressed log

_spec = importlib.util.spec_from_file_location("search", os.path.join(ROOT, "src", "search.py"))
search = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(search)


def env():
    """loadavg + swap (free -m) + llama-server visibility, as compact text."""
    with open("/proc/loadavg") as f:
        load = f.read().strip()
    free = subprocess.run(["free", "-m"], capture_output=True, text=True).stdout
    lines = [l for l in free.splitlines() if l.startswith("Swap:")]
    swap = lines[0].strip() if lines else "n/a"
    pg = subprocess.run(["pgrep", "-a", "llama-server"], capture_output=True, text=True)
    llama = pg.stdout.strip() or "(not visible from this PID namespace)"
    return {"loadavg": load, "swap": swap, "llama_pgrep": llama}


def quantile(sorted_x, p):
    """linear-interpolation quantile, numpy 'linear' method."""
    if not sorted_x:
        return None
    k = (len(sorted_x) - 1) * p
    f, c = int(k), min(int(k) + 1, len(sorted_x) - 1)
    return sorted_x[f] + (sorted_x[c] - sorted_x[f]) * (k - f)


def run_search(con, words):
    """Same logic as src/search.py main() (read-only, no --touch)."""
    q = search.fts_query(" ".join(words))
    terms = [w.lower() for w in " ".join(words).split() if w]
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

    def ranked_rows(table):
        w, params = search.match_where(table, q, projs, None)
        rows = con.execute(w, params).fetchall()
        scored = []
        for rowid, sid, rank in rows:
            if sid in seen:
                continue
            f = search.factors(con, sid, rank, now, terms)
            scored.append((rowid, sid, *f))
        scored.sort(key=lambda r: (-r[6], r[0]))
        return scored[:MAX_CANDIDATES]

    for rowid, sid, bm25, recency, session_sal, local_sal, score, smap in ranked_rows("cards_fts"):
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
                    snip = search.snippet_of(con, "cards_fts", colidx, rowid, q)
        lines.append(
            f"[card] {sid}  {start_of(sid)}  matched={','.join(tags)}  "
            f"bm25={bm25:.2f} recency={recency:.3f} salience={local_sal:.1f} "
            f"session_sal={session_sal:.1f} score={score:.3f}  {snip}"
        )

    tmp_con = sqlite3.connect(":memory:")
    tmp_con.execute("CREATE VIRTUAL TABLE tmp_terms USING fts5(term)")

    def match_classes(sid):
        row = con.execute(
            "SELECT familiar_terms, familiar_classes FROM cards WHERE id = ?", (sid,)
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
        tag = f"familiar_terms:{smap.get('familiar_terms', 'observed')}"
        snip = search.snippet_of(con, "familiar_fts", 1, rowid, q)
        line = (
            f"[{label}] {sid}  {start_of(sid)}  matched={tag}  "
            f"bm25={bm25:.2f} recency={recency:.3f} salience={local_sal:.1f} "
            f"session_sal={session_sal:.1f} score={score:.3f}  {snip}"
        )
        if is_searched:
            searched.append((sid, line))
        else:
            fam.append((sid, line))
    for sid, line in (fam + searched)[:MAX_LINES - len(lines)]:
        lines.append(line)
        seen.add(sid)
    tmp_con.close()
    if not lines:
        lines = ["(no results)"]
    return lines


def main():
    res = {"queries": QUERIES, "modes": {}, "episodes": [], "env": {}}

    # ---- sanity: in-process B must match CLI output ------------------------
    con_disk = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    words = QUERIES[0].split()
    cli_out = subprocess.run(
        [sys.executable, "src/search.py"] + words,
        capture_output=True, text=True, cwd=ROOT,
    ).stdout
    cli_lines = [l for l in cli_out.splitlines() if not l.startswith("[") or l.startswith("[card]") or l.startswith("[familiar]") or l.startswith("[searched]")]
    inproc = run_search(con_disk, words)
    res["sanity"] = {
        "query": " ".join(words),
        "cli_lines": len(cli_lines),
        "inproc_lines": len(inproc),
        "match": cli_lines == inproc,
    }
    print("sanity B==CLI:", res["sanity"])

    # ---- mode A: new process per query --------------------------------------
    res["env"]["A_start"] = env()
    ms, inner = [], []
    for q in QUERIES:
        t0 = time.monotonic()
        p = subprocess.run(
            [sys.executable, "src/search.py", q],
            capture_output=True, text=True, cwd=ROOT,
        )
        wall = (time.monotonic() - t0) * 1000
        ms.append(wall)
        # search.py prints its own internal time in the trailing stats line
        import re
        m = re.search(r"([\d.]+) ms, query:", p.stdout)
        inner.append(float(m.group(1)) if m else None)
    res["env"]["A_end"] = env()
    s = sorted(ms)
    overhead = sorted(w - i for w, i in zip(ms, inner) if i is not None)
    res["modes"]["A_new_process"] = {
        "median_ms": quantile(s, 0.50), "p95_ms": quantile(s, 0.95), "min_ms": s[0], "max_ms": s[-1],
        "per_query_ms": ms,
        "inner_query_ms": inner,
        "startup_overhead_median_ms": quantile(overhead, 0.50),
    }
    print("A median %.1f p95 %.1f (startup+open overhead median %.1f)"
          % (res["modes"]["A_new_process"]["median_ms"], res["modes"]["A_new_process"]["p95_ms"],
             res["modes"]["A_new_process"]["startup_overhead_median_ms"]))

    # ---- mode B: one process + disk db --------------------------------------
    res["env"]["B_start"] = env()
    ms = []
    for q in QUERIES:
        t1 = time.monotonic()
        run_search(con_disk, q.split())
        ms.append((time.monotonic() - t1) * 1000)
    con_disk.close()
    # one-time cost: opening the disk db in a fresh process
    t0 = time.monotonic()
    con_x = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    db_open_ms = (time.monotonic() - t0) * 1000
    con_x.close()
    res["env"]["B_end"] = env()
    s = sorted(ms)
    res["modes"]["B_one_proc_disk"] = {
        "median_ms": quantile(s, 0.50), "p95_ms": quantile(s, 0.95), "min_ms": s[0], "max_ms": s[-1],
        "per_query_ms": ms,
        "db_open_ms": db_open_ms,
    }
    print("B median %.1f p95 %.1f (db open %.1f ms)"
          % (res["modes"]["B_one_proc_disk"]["median_ms"], res["modes"]["B_one_proc_disk"]["p95_ms"], db_open_ms))

    # ---- mode C: one process + in-memory db ---------------------------------
    res["env"]["C_start"] = env()
    t0 = time.monotonic()
    con_mem = sqlite3.connect(":memory:")
    con_mem.deserialize(open(DB, "rb").read())  # whole db loaded into RAM
    attach_ms = (time.monotonic() - t0) * 1000
    ms = []
    for q in QUERIES:
        t1 = time.monotonic()
        run_search(con_mem, q.split())
        ms.append((time.monotonic() - t1) * 1000)
    con_mem.close()
    res["env"]["C_end"] = env()
    s = sorted(ms)
    res["modes"]["C_one_proc_mem"] = {
        "median_ms": quantile(s, 0.50), "p95_ms": quantile(s, 0.95), "min_ms": s[0], "max_ms": s[-1],
        "per_query_ms": ms,
        "attach_ms": attach_ms,
    }
    print("C median %.1f p95 %.1f (attach into memory %.1f ms)"
          % (res["modes"]["C_one_proc_mem"]["median_ms"], res["modes"]["C_one_proc_mem"]["p95_ms"], attach_ms))

    # ---- episode cost: 5 largest sessions -----------------------------------
    os.makedirs(EXC_DIR, exist_ok=True)
    res["env"]["E_start"] = env()
    for item in TOP5:
        sid, path = item["sid"], item["path"]
        comp = os.path.getsize(path)
        env_b = env()
        t0 = time.monotonic()
        proc = subprocess.run(["zstd", "-dc", path], capture_output=True)
        dec_ms = (time.monotonic() - t0) * 1000
        dec = len(proc.stdout)
        exc = os.path.join(EXC_DIR, sid + ".excerpt.txt")
        if not os.path.exists(exc):  # pre-cut, once, at "sleep-job" time
            with open(exc, "wb") as f:
                f.write(proc.stdout[:EXCERPT_BYTES])
        exc_size = os.path.getsize(exc)
        t1 = time.monotonic()
        subprocess.run(
            [sys.executable, "-c",
             "import sys; open(sys.argv[1],'rb').read(); print()", exc],
            capture_output=True,
        )
        exc_ms = (time.monotonic() - t1) * 1000
        env_a = env()
        res["episodes"].append({
            "sid": sid, "compressed": comp, "decompressed": dec,
            "decompress_ms": dec_ms, "excerpt_bytes": exc_size, "excerpt_ms": exc_ms,
            "env_before": env_b, "env_after": env_a,
        })
        print("E %s comp %d dec %d | dec %.1f ms | exc %d B %.1f ms"
              % (sid, comp, dec, dec_ms, exc_size, exc_ms))
    res["env"]["E_end"] = env()

    with open(OUT, "w") as f:
        json.dump(res, f, indent=1)
    print("results ->", OUT)


if __name__ == "__main__":
    main()
