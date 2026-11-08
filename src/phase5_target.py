#!/usr/bin/env python3
"""Phase 5: find candidate target sessions in workspace ~/rust-lab.

For each session log under the rust-lab workspace dir, stream via
`zstd -dc` (read-only) and print ONLY aggregates: uuid, first/last event
time, event-type distribution, top-level keys of the first event (keys and
a few short scalar values only). No raw log lines printed.
"""
import json
import os
import subprocess
from collections import Counter

ROOT = os.path.expanduser("~/.local/share/deepseek-harness/sessions")
WS = "--home-jkzero-rust-lab--"
ME = os.environ.get("DSH_SESSION_ID", "")


def short(v, n=70):
    if isinstance(v, str):
        return v[:n]
    return str(v)[:n]


def main():
    wdir = os.path.join(ROOT, WS)
    for name in sorted(os.listdir(wdir)):
        if not name.startswith("session-"):
            continue
        path = os.path.join(wdir, name, "session.v4.jsonl.zstd")
        if not os.path.isfile(path):
            continue
        out = subprocess.run(["zstd", "-dc", path], stdout=subprocess.PIPE).stdout
        first = None
        last = None
        types = Counter()
        n = 0
        for line in out.decode("utf-8", "replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if not isinstance(ev, dict):
                continue
            n += 1
            t = ev.get("type", "?")
            types[t] += 1
            if first is None:
                first = ev
            last = ev
        if first is None:
            print(f"{name}: EMPTY")
            continue
        print(f"=== {name}{'  <-- THIS SESSION' if ME and name == ME else ''}")
        print(f"  events: {n}")
        print(f"  first type: {first.get('type')}  first ts: {short(first.get('ts') or first.get('time') or first.get('timestamp'))}")
        print(f"  last  type: {last.get('type') if isinstance(last, dict) else '?'}  last ts: {short(last.get('ts') or last.get('time') or last.get('timestamp')) if isinstance(last, dict) else '?'}")
        print(f"  types: {dict(types)}")
        print(f"  first-event keys: {sorted(first.keys())}")


if __name__ == "__main__":
    main()
