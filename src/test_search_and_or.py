#!/usr/bin/env python3
"""AND-first search with OR fallback + consistent result count.

Reproduces the real-world case from session-dfd0089b (2026-11-24): the
5-word query below found nothing under AND (no single session contains
all five words), and the footer then lied ("[1 results]" while the body
was "(no results)"). After the fix the search falls back to OR when AND
matches nothing, the footer/trailer counts the ACTUAL results (0 when
none), and the mode (and/or) is reported.

Hermetic: builds a throwaway fixture DB in a temp directory; never
touches data/library.db.

Run: python3 src/test_search_and_or.py
"""
import datetime
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
NOW = datetime.datetime(2026, 11, 25, 12, 0, 0, tzinfo=datetime.timezone.utc)
REAL_QUERY = "jira-rag de_postgres compose single database"  # real query, seq21

A_ID = "session-dfd0089b-8c6c-4888-96ed-72663121e259"
B_ID = "session-c55a8bfe-9d2e-4a83-9ed5-08456d65ba42"
C_ID = "session-f35a924d-62f7-470e-92e9-72a5f0e2154d"


def build_db(path):
    """Fixture DB shaped like the real schema (cards + both FTS tables).

    A has 4 of the 5 query words (single, database, compose, de_postgres)
    but NOT jira-rag; B and C have de_postgres only. So:
      AND REAL_QUERY  -> nothing
      OR  REAL_QUERY  -> A, B, C
      AND "de_postgres compose" -> A only (OR would give A, B, C)
    """
    con = sqlite3.connect(path)
    con.executescript("""
    CREATE TABLE cards (
        id TEXT PRIMARY KEY, source TEXT, start TEXT, end TEXT, cwd TEXT,
        first_message TEXT, files TEXT, bash_commands TEXT, command_classes TEXT,
        file_classes TEXT, familiar_terms TEXT, familiar_classes TEXT,
        error_count INTEGER, n_commits INTEGER, n_writes INTEGER,
        access_count INTEGER, last_accessed TEXT, project TEXT, source_map TEXT);
    CREATE VIRTUAL TABLE cards_fts USING fts5(
        id, first_message, files_text, commands_text, tokenize='unicode61');
    CREATE VIRTUAL TABLE familiar_fts USING fts5(
        id, terms_text, tokenize='unicode61');
    """)
    fixtures = [
        (A_ID, "2026-11-24T21:19:09", "2026-11-24T22:30:00",
         "Consolidate single database in Docker API",
         ["docker-compose.yml"], ["docker exec -i de_postgres psql -U deuser dedb"],
         ["de_postgres", "docker", "psql", "5432"], 1),
        (B_ID, "2026-11-24T19:57:01", "2026-11-24T20:30:00",
         "SOLO la de .env (de_postgres, puerto 5432); no uses el Postgres del puerto 5433.",
         [], [], [], 0),
        (C_ID, "2026-11-24T01:31:23", "2026-11-24T02:00:00",
         "SOLO la de .env (de_postgres, puerto 5432). Solo LEE de bronze y silver; escribe",
         [], [], [], 0),
    ]
    for sid, start, end, first, files, commands, terms, n_writes in fixtures:
        con.execute(
            "INSERT INTO cards (id, source, start, end, cwd, first_message, files,"
            " bash_commands, command_classes, file_classes, familiar_terms,"
            " familiar_classes, error_count, n_commits, n_writes, access_count,"
            " last_accessed, project, source_map) VALUES"
            " (:id, :source, :start, :end, :cwd, :first_message, :files,"
            " :bash_commands, :command_classes, :file_classes, :familiar_terms,"
            " :familiar_classes, :error_count, :n_commits, :n_writes, :access_count,"
            " :last_accessed, :project, :source_map)",
            dict(
                id=sid, source="dsh", start=start, end=end,
                cwd="/home/jkzero/jira-rag", first_message=first,
                files=json.dumps(files), bash_commands=json.dumps(commands),
                command_classes=json.dumps(["read"] * len(commands)),
                file_classes=json.dumps(["write"] * len(files)),
                familiar_terms=json.dumps(terms),
                familiar_classes=json.dumps(["observed"] * len(terms)),
                error_count=0, n_commits=0, n_writes=n_writes,
                access_count=0, last_accessed=None,
                project="/home/jkzero/jira-rag", source_map="{}",
            ),
        )
        con.execute("INSERT INTO cards_fts VALUES (?,?,?,?)",
                    (sid, first, " ".join(files), " ".join(commands)))
        con.execute("INSERT INTO familiar_fts VALUES (?,?)", (sid, " ".join(terms)))
    con.commit()
    con.close()


def ids_of(lines):
    return set(l.split()[1] for l in lines if l.startswith("["))


def run_cli(words, dbpath):
    env = dict(os.environ, LIBRARY_DB=dbpath)
    p = subprocess.run(
        [sys.executable, os.path.join(SRC, "search.py"), *words],
        capture_output=True, text=True, env=env, cwd=ROOT,
    )
    assert p.returncode == 0, "CLI failed: %s" % p.stderr
    out = p.stdout.rstrip("\n").split("\n")
    return out[:-1], out[-1]


def test_cli_fallback():
    """5-word real query: AND empty -> OR fallback, footer consistent."""
    body, footer = run_cli(REAL_QUERY.split(), os.environ["_TEST_DB"])
    assert "(no results)" not in body, body
    assert ids_of(body) == {A_ID, B_ID, C_ID}, body
    assert all(l.startswith("[card] ") for l in body), body
    assert footer.startswith("[3 results"), footer
    assert "mode=or" in footer, footer
    assert " OR " in footer, footer


def test_cli_and_first():
    """AND matches A only; OR would match A, B, C. Must report A only, and."""
    body, footer = run_cli("de_postgres compose".split(), os.environ["_TEST_DB"])
    assert ids_of(body) == {A_ID}, body
    assert all(l.startswith("[card] ") for l in body), body
    assert footer.startswith("[1 results"), footer
    assert "mode=and" in footer, footer


def test_cli_no_match():
    """Nothing matches: body is the sentinel, footer counts 0 (was 1)."""
    body, footer = run_cli("zebra xkcd".split(), os.environ["_TEST_DB"])
    assert body == ["(no results)"], body
    assert footer.startswith("[0 results"), footer
    assert "mode=and" in footer, footer


def test_run_search_direct():
    """AND via run_search directly: A only, card_hits tracks level-1 ids."""
    sys.path.insert(0, SRC)
    import search
    con = sqlite3.connect("file:%s?mode=ro" % os.environ["_TEST_DB"], uri=True)
    words = "de_postgres compose".split()
    lines, seen, card_hits = search.run_search(
        con, words, search.fts_query(" ".join(words)), None, NOW)
    con.close()
    assert ids_of(lines) == {A_ID} and seen == {A_ID}, lines
    assert card_hits == [A_ID], card_hits


def test_server():
    """MCP path: fallback + mode in trailer; access only on [card] hits."""
    sys.path.insert(0, SRC)
    import library_server
    library_server.refresh = types.SimpleNamespace(refresh=lambda p: None)
    db = library_server.Db(os.environ["_TEST_DB"])
    try:
        text = library_server.library_search(db, REAL_QUERY, None)["content"][0]["text"]
        lines = text.rstrip("\n").split("\n")
        trailer = lines[-1]
        body = lines[:-1]
        # fixture lines are long, so the 570-char cap truncates the body;
        # the trailer (possibly wrapped in "[truncated at ...]") must still
        # report the real count and mode.
        assert "(no results)" not in body, text
        assert body and ids_of(body) <= {A_ID, B_ID, C_ID}, text
        assert all(l.startswith("[card] ") for l in body), text
        assert "[3 results" in trailer, trailer
        assert "mode: or" in trailer, trailer

        con = sqlite3.connect(os.environ["_TEST_DB"])
        for sid in (A_ID, B_ID, C_ID):  # [card] hits: access recorded
            acc, last = con.execute(
                "SELECT access_count, last_accessed FROM cards WHERE id = ?",
                (sid,)).fetchone()
            assert acc == 1 and last is not None, (sid, acc, last)
        con.close()

        text = library_server.library_search(db, "zebra xkcd", None)["content"][0]["text"]
        lines = text.rstrip("\n").split("\n")
        assert lines[0] == "(no results)", text
        assert lines[1].startswith("[0 results"), text
        assert "mode: and" in text, text
    finally:
        db.close()


def main():
    tmp = tempfile.mkdtemp(prefix="libtest-")
    os.environ["_TEST_DB"] = os.path.join(tmp, "library.db")
    build_db(os.environ["_TEST_DB"])
    tests = [test_cli_fallback, test_cli_and_first, test_cli_no_match,
             test_run_search_direct, test_server]
    for t in tests:
        t()
        print("PASS %s" % t.__name__)
    print("PASS: all AND/OR tests (%d)" % len(tests))


if __name__ == "__main__":
    main()
