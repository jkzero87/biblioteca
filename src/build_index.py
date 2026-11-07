#!/usr/bin/env python3
"""Phase 2b: build the two-level SQLite FTS5 index with bio fields.

Reads data/cards.jsonl, rebuilds data/library.db atomically (temp file +
os.replace). `last_accessed` and `access_count` are carried over from the
previous db, so a rebuild does not reset them.

Two FTS5 tables (unchanged from Phase 2 option B):
  cards_fts    - recollection: first_message, files_touched, bash_commands
                 (bash_commands never contains retrieval commands — see
                 extract.is_retrieval; searching is not evidence)
  familiar_fts - familiarity: familiar_terms (lossless, no cap, retrieval
                 included)

New bio fields from the fix pass: n_retrieval, command_classes, file_classes
(per-item classes for local salience in search.py).

Fix pass 2 adds familiar_classes: one class per familiar_terms entry
('retrieval' / 'write' / 'read' / 'error' / 'commit'), feeding the
[searched] label in search.py.

Project assignment (schema, design.md 3.6): each card is assigned to the
git repo whose path best matches its cwd and files:
  1. if a git repo equals the cwd or is an ancestor of the cwd, the
     deepest such repo wins;
  2. otherwise, the repo under which the most files_touched paths fall
     (the count must be > 0; ties broken by the lexicographically
     smallest repo path);
  3. otherwise the card is `orphan`.
Repos are discovered by scanning the home directory for `.git`
(dirs and files), depth <= 4, skipping hidden dirs and
node_modules/.venv/venv/__pycache__.

Usage: python3 src/build_index.py [--cards FILE] [--db FILE]
"""
import json
import os
import sqlite3
import sys

HOME = os.path.expanduser("~")
SKIP_DIRS = {"node_modules", ".venv", "venv", "__pycache__"}
REPO_MAX_DEPTH = 4


def find_git_repos(root=HOME, max_depth=REPO_MAX_DEPTH):
    """All dirs (level <= max_depth below root) containing a .git entry."""
    repos = set()

    def check(d):
        for kind in (os.path.isdir, os.path.isfile):
            if kind(os.path.join(d, ".git")):
                repos.add(d)
                return

    def walk(d, level):
        check(d)
        if level >= max_depth:
            return
        try:
            entries = list(os.scandir(d))
        except OSError:
            return
        for e in entries:
            try:
                if not e.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            if e.name == ".git" or e.name.startswith(".") or e.name in SKIP_DIRS:
                continue
            walk(e.path, level + 1)

    walk(root, 0)
    return sorted(repos)


def _under(repo, p):
    return p == repo or p.startswith(repo.rstrip("/") + "/")


def assign_project(cwd, files, repos):
    """Deterministic rule stated in the module docstring; returns repo path
    or 'orphan'."""
    if cwd:
        cands = [r for r in repos if _under(r, cwd)]
        if cands:
            return max(cands, key=len)
    if files:
        best = max(sum(1 for f in files if _under(r, f)) for r in repos) if repos else 0
        if best > 0:
            return min(r for r in repos if sum(1 for f in files if _under(r, f)) == best)
    return "orphan"


def carry_access(db_path):
    """id -> (last_accessed, access_count) from the old db, if present."""
    out = {}
    if not os.path.exists(db_path):
        return out
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cols = {r[1] for r in con.execute("PRAGMA table_info(cards)")}
        if "last_accessed" in cols and "access_count" in cols:
            for sid, la, ac in con.execute(
                "SELECT id, last_accessed, access_count FROM cards"
            ):
                out[sid] = (la, ac)
        con.close()
    except sqlite3.Error:
        pass
    return out


def main():
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    cards_path = os.path.join(base, "cards.jsonl")
    db_path = os.path.join(base, "library.db")
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--cards":
            cards_path = args[i + 1]
        elif a == "--db":
            db_path = args[i + 1]

    cards = []
    with open(cards_path) as f:
        for line in f:
            if line.strip():
                cards.append(json.loads(line))
    cards.sort(key=lambda c: c["id"])

    carry = carry_access(db_path)
    repos = find_git_repos()

    tmp = db_path + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    if os.path.exists(db_path):
        os.remove(db_path)
    con = sqlite3.connect(tmp)
    con.executescript(
        """
        CREATE TABLE cards (
            id TEXT PRIMARY KEY,
            source TEXT,
            start TEXT,
            end TEXT,
            cwd TEXT,
            first_message TEXT,
            files TEXT,
            bash_commands TEXT,
            familiar_terms TEXT,
            familiar_classes TEXT,
            tool_call_count INTEGER,
            error_count INTEGER,
            commit_hashes TEXT,
            n_writes INTEGER,
            n_commits INTEGER,
            n_new_files INTEGER,
            n_retrieval INTEGER,
            command_classes TEXT,
            file_classes TEXT,
            project TEXT,
            last_accessed TEXT,
            access_count INTEGER,
            source_map TEXT
        );
        -- recollection: curated, capped display fields
        CREATE VIRTUAL TABLE cards_fts USING fts5(
            id, first_message, files_text, commands_text, tokenize='unicode61'
        );
        -- familiarity: lossless per-session term list
        CREATE VIRTUAL TABLE familiar_fts USING fts5(
            id, terms_text, tokenize='unicode61'
        );
        """
    )
    project_counts = {}
    for c in cards:
        project = assign_project(c.get("cwd"), c.get("files_touched", []), repos)
        project_counts[project] = project_counts.get(project, 0) + 1
        la, ac = carry.get(c["id"], (None, 0))
        con.execute(
            "INSERT INTO cards VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                c["id"],
                c["source"],
                c.get("start"),
                c.get("end"),
                c.get("cwd"),
                c.get("first_message"),
                json.dumps(c.get("files_touched", [])),
                json.dumps(c.get("bash_commands", [])),
                json.dumps(c.get("familiar_terms", [])),
                json.dumps(c.get("familiar_classes", [])),
                c.get("tool_call_count", 0),
                c.get("error_count", 0),
                json.dumps(c.get("commit_hashes", [])),
                c.get("n_writes", 0),
                c.get("n_commits", 0),
                c.get("n_new_files", 0),
                c.get("n_retrieval", 0),
                json.dumps(c.get("command_classes", [])),
                json.dumps(c.get("file_classes", [])),
                project,
                la,
                ac,
                json.dumps(c.get("source_map", {})),
            ),
        )
        con.execute(
            "INSERT INTO cards_fts VALUES (?,?,?,?)",
            (
                c["id"],
                c.get("first_message") or "",
                " ".join(c.get("files_touched", [])),
                "\n".join(c.get("bash_commands", [])),
            ),
        )
        con.execute(
            "INSERT INTO familiar_fts VALUES (?,?)",
            (c["id"], " ".join(c.get("familiar_terms", []))),
        )
    con.commit()
    con.close()
    os.replace(tmp, db_path)
    size = os.path.getsize(db_path)
    orphans = project_counts.get("orphan", 0)
    top = sorted(project_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    print(f"db: {db_path}  cards: {len(cards)}  size: {size} bytes")
    print(f"repos scanned: {len(repos)}  orphan cards: {orphans}")
    for p, n in top:
        print(f"  project {p}: {n}")


if __name__ == "__main__":
    main()
