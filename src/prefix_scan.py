#!/usr/bin/env python3
"""Phase 4 close-out STEP 2: top-15 first-40-char prefixes of user/message
texts across all library sessions, with counts.

Read-only (zstd -dc, stdlib). Prints prefixes and counts only — never full
messages. Excludes: this session (argv[1], session id), and user messages
whose stripped text starts with the two known harness prefixes
("<system-reminder>", "Time sampled while preparing turn").
No other prefix is excluded; the list is for the user's decision.
"""
import json
import sqlite3
import subprocess
import sys

sys.path.insert(0, "src")
import extract

HARNESS_PREFIXES = ("<system-reminder>", "Time sampled while preparing turn")


def main():
    exclude_sessions = set(a for a in sys.argv[1:] if a)
    con = sqlite3.connect("data/library.db")
    sources = [r[0] for r in con.execute("SELECT source FROM cards")]
    counts = {}
    n_sessions = n_msgs = n_harness = n_fail = 0
    for path in sources:
        sid = path.split("/")[-2]
        if sid in exclude_sessions:
            continue
        n_sessions += 1
        proc = subprocess.run(
            ["zstd", "-dc", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        if proc.returncode != 0:
            n_fail += 1
            continue
        for line in proc.stdout.decode("utf-8", "replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if not isinstance(ev, dict) or ev.get("type") != "user/message":
                continue
            data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            text = extract.text_of(data.get("content"))
            if text.strip().startswith(HARNESS_PREFIXES):
                n_harness += 1
                continue
            n_msgs += 1
            counts[text[:40]] = counts.get(text[:40], 0) + 1
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:15]
    print("sessions=%d user_msgs_counted=%d harness_excluded=%d decode_failures=%d"
          % (n_sessions, n_msgs, n_harness, n_fail))
    for i, (p, c) in enumerate(top, 1):
        print("%2d %4d  %s" % (i, c, repr(p)))


if __name__ == "__main__":
    main()
