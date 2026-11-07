#!/usr/bin/env python3
"""Aggregate the inspect report across all logs:
event type -> total count; per type the union of keys (depth<=2) and <=2 samples/field.
Reads data/inspection/inspect_report.txt (no disk logs touched).
"""
import os
import re
import sys
from collections import Counter, defaultdict

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
path = os.path.join(root, "data", "inspection", "inspect_report.txt")

cur = None
type_counts = Counter()
type_keys = defaultdict(set)
type_samples = defaultdict(lambda: defaultdict(list))

for line in open(path):
    if line.startswith("logs:"):
        m = re.search(r"lines: (\d+)", line)
        print("total lines:", m.group(1) if m else "?")
        continue
    if line.startswith("--- "):
        m = re.match(r"^--- (.+?) (\S+?)=(.+?) keys\((\d+)\):$", line)
        assert m, line
        cur = m.group(3).strip().strip("'\"")
        type_counts[cur] += int(m.group(4))  # per-log key count marker; recount below
        continue
    if cur is None:
        continue
    if line.startswith("    "):
        body = line.strip()
        if body == "":
            continue
        if body.startswith("... "):
            continue
        k = body.split("  e.g. ")[0]
        type_keys[cur].add(k)
        if "  e.g. " in body:
            sm = body.split("  e.g. ", 1)[1]
            for x in sm.split(" || "):
                if len(type_samples[cur][k]) < 2:
                    type_samples[cur][k].append(x)

# authoritative event counts: single streaming pass over the raw logs
import json, subprocess
SESSIONS_ROOT = os.path.expanduser("~/.local/share/deepseek-harness/sessions")
direct = Counter()
no_type = 0
for ws in sorted(os.listdir(SESSIONS_ROOT)):
    wdir = os.path.join(SESSIONS_ROOT, ws)
    if not os.path.isdir(wdir):
        continue
    for name in sorted(os.listdir(wdir)):
        p = os.path.join(wdir, name, "session.v4.jsonl.zstd")
        if not os.path.isfile(p) or name.startswith("session-8b1b3fb3"):
            continue
        proc = subprocess.Popen(["zstd", "-dc", p], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            tv = ev.get("type") if isinstance(ev, dict) else None
            if isinstance(tv, str):
                direct[tv] += 1
            else:
                no_type += 1
        proc.wait()
type_counts = direct

print("\nEVENT TYPES (direct pass, field name 'type'; lines without type: %d):" % no_type)
for t, c in type_counts.most_common():
    print(f"  {t}: {c}")

print("\nKEYS PER TYPE (union from report, <=40 shown) + samples:")
for t in sorted(type_keys, key=lambda t: -type_counts.get(t, 0)):
    keys = sorted(type_keys[t])
    print(f"\n### {t!r} ({type_counts.get(t,0)} events) {len(keys)} keys:")
    for k in keys[:40]:
        sm = type_samples[t].get(k)
        s = ""
        if sm:
            s = "  e.g. " + " || ".join(sm[:2])
        print(f"    {k}{s}")
    if len(keys) > 40:
        print(f"    ... {len(keys)-40} more keys")
