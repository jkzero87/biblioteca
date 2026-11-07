#!/usr/bin/env python3
"""Phase 2 (option B): build the two-level SQLite FTS5 index.

Reads data/cards.jsonl, rebuilds data/library.db atomically (temp file +
os.replace). Idempotent: same cards -> same db -> same search results.

Two FTS5 tables:
  cards_fts    - recollection: first_message, files_touched, bash_commands
                 (curated, capped, salience-ordered display fields), one row
                 per session
  familiar_fts - familiarity: familiar_terms (lossless per-session list of
                 every file path and bash command, no cap), one row per session

Usage: python3 src/build_index.py [--cards FILE] [--db FILE]
"""
import json
import os
import sqlite3
import sys


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

    tmp = db_path + ".tmp"
    for p in (tmp,):
        if os.path.exists(p):
            os.remove(p)
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
            tool_call_count INTEGER,
            error_count INTEGER,
            commit_hashes TEXT
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
    for c in cards:
        con.execute(
            "INSERT INTO cards VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
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
                c.get("tool_call_count", 0),
                c.get("error_count", 0),
                json.dumps(c.get("commit_hashes", [])),
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
    print(f"db: {db_path}  cards: {len(cards)}  size: {size} bytes")


if __name__ == "__main__":
    main()
