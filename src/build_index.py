#!/usr/bin/env python3
"""Phase 2 step 4: build a SQLite FTS5 index over the cards.

Reads data/cards.jsonl, rebuilds data/library.db atomically (temp file +
os.replace). Idempotent: same cards -> same db -> same search results.

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
            tool_call_count INTEGER,
            error_count INTEGER,
            commit_hashes TEXT
        );
        CREATE VIRTUAL TABLE cards_fts USING fts5(
            id, first_message, files_text, commands_text, tokenize='unicode61'
        );
        """
    )
    for c in cards:
        con.execute(
            "INSERT INTO cards VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                c["id"],
                c["source"],
                c.get("start"),
                c.get("end"),
                c.get("cwd"),
                c.get("first_message"),
                json.dumps(c.get("files_touched", [])),
                json.dumps(c.get("bash_commands", [])),
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
    con.commit()
    con.close()
    os.replace(tmp, db_path)
    size = os.path.getsize(db_path)
    print(f"db: {db_path}  cards: {len(cards)}  size: {size} bytes")


if __name__ == "__main__":
    main()
