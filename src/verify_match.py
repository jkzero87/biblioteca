#!/usr/bin/env python3
"""Step 7 / verification: grep a session log for a fixed string.

Prints at most 5 matching lines, each truncated to 200 chars, plus the total
match count. Never writes anything; streams via `zstd -dc`.

Usage: python3 src/verify_match.py <session-dir-or-log-path> <fixed-string>
"""
import os
import subprocess
import sys
from pathlib import Path

MAX_LINES = 5
MAX_CHARS = 200


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    path = Path(sys.argv[1])
    needle = sys.argv[2]
    log = path / "session.v4.jsonl.zstd" if (path / "session.v4.jsonl.zstd").is_file() else path
    if not log.is_file():
        sys.exit(f"no log file at {log}")
    proc = subprocess.Popen(["zstd", "-dc", str(log)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    shown = 0
    total = 0
    for raw in proc.stdout:
        line = raw.decode("utf-8", "replace")
        if needle in line:
            total += 1
            if shown < MAX_LINES:
                shown += 1
                print(line.strip()[:MAX_CHARS])
    proc.wait()
    print(f"[{total} matching lines, {min(total, MAX_LINES)} shown]")


if __name__ == "__main__":
    main()
