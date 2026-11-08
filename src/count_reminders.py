#!/usr/bin/env python3
"""Outline fix 2 step 2a: count user/message events whose text starts with
"<system-reminder>" (after stripping whitespace) across all sessions.

This session (passed as an argument, default none) is excluded. Reads logs
read-only via zstd -dc, stdlib only, no network, no model. Prints aggregates
only: sessions scanned, sessions affected, total harness-user events.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import extract
import library_server as L
import collections


def main():
    excludes = sys.argv[1:]
    root = extract.DEFAULT_ROOT
    logs = extract.find_logs(root, excludes)
    cache = collections.OrderedDict()
    total = 0
    affected = 0
    for path in logs:
        n = 0
        try:
            events = L.decode_session(path, cache)
        except Exception as exc:
            print("decode failed %s: %s" % (os.path.basename(os.path.dirname(path)), exc))
            continue
        for e in events:
            if e["kind"] == "user" and _is_harness(e):
                n += 1
        if n:
            affected += 1
        total += n
    print("sessions scanned: %d (excluded: %s)" % (len(logs), ", ".join(excludes) or "none"))
    print("sessions affected: %d" % affected)
    print("system-reminder user events: %d" % total)


def _is_harness(e):
    return e.get("text", "").strip().startswith("<system-reminder>")


if __name__ == "__main__":
    main()
