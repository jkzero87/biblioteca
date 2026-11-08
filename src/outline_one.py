#!/usr/bin/env python3
"""Phase 4 close-out STEP 3: print the library_open outline for one
session id, computed with the current code (offline, no server, no DB
write; the db is opened read-only).

Usage: outline_one.py <session-id>
"""
import collections
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import library_server as L


def main():
    sid = sys.argv[1]
    con = sqlite3.connect("file:" + L.DB_PATH + "?mode=ro", uri=True)
    row = con.execute(
        "SELECT source, start, end, project, first_message FROM cards WHERE id = ?",
        (sid,),
    ).fetchone()
    con.close()
    if not row:
        sys.exit("unknown session id: %s" % sid)
    source, start, end, project, first_message = row
    events = L.decode_session(source, collections.OrderedDict())
    if not events:
        sys.exit("session %s has no events" % sid)
    header = (
        "[card] %s  %s -> %s  project=%s  first: %s"
        % (sid, start, end, project, L.clip(first_message or "", 100))
    )
    print("\n".join(L._outline_text(events, header, L.OUTLINE_CHAR_CAP, sid)) + "\n")


if __name__ == "__main__":
    main()
