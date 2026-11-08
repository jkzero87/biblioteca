#!/usr/bin/env python3
"""Phase 4, step 2: diagnose the end-state gap of session c5f86181.

The card says the session ended 2026-10-25T21:45:51 (card end = max `time`
over ALL events, see extract.py), but library_open at="end" stops at
seq 398 (2026-10-23 ~18:20). This script prints AGGREGATES ONLY: counts,
time ranges, seq statistics, and event-type tallies. No raw log lines and
no message text ever reach stdout. Log is read read-only via `zstd -dc`.
Stdlib only. No network.
"""
import datetime
import json
import os
import sqlite3
import subprocess
import sys
from collections import Counter
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import extract  # noqa: E402  same visible-text logic library_server uses

LOG = ("/home/jkzero/.local/share/deepseek-harness/sessions/"
       "--home-jkzero-rust-lab--/"
       "session-c5f86181-d002-4a54-b7a1-7d647da9c2e4/session.v4.jsonl.zstd")
DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "data", "library.db")
TZ = ZoneInfo("America/Bogota")
CUT = datetime.datetime(2026, 10, 23, 18, 20, tzinfo=TZ)  # ~ time of seq 398
SEQ_MARK = 398

raw = subprocess.run(["zstd", "-dc", LOG], stdout=subprocess.PIPE).stdout
lines = [l for l in raw.decode("utf-8", "replace").splitlines() if l.strip()]
evs, bad = [], 0
for ln in lines:
    try:
        e = json.loads(ln)
    except Exception:
        bad += 1
        continue
    if isinstance(e, dict):
        evs.append(e)
    else:
        bad += 1


def ts(e):
    t = e.get("time")
    return t if isinstance(t, int) else None


def iso(ms):
    return datetime.datetime.fromtimestamp(ms / 1000, TZ).isoformat(timespec="seconds")


times = [ts(e) for e in evs if ts(e) is not None]
seqs = [e.get("seq") for e in evs if isinstance(e.get("seq"), int)]
print("== raw log aggregates (no lines printed)")
print("non-empty lines: %d  parsed dict events: %d  unparsed/other: %d"
      % (len(lines), len(evs), bad))
print("min time: %s   max time: %s" % (iso(min(times)), iso(max(times))))
print("seq: min=%d  max=%d  events with numeric seq=%d  unique seqs=%d"
      % (min(seqs), max(seqs), len(seqs), len(set(seqs))))
dup = {s: c for s, c in Counter(seqs).items() if c > 1}
print("duplicate seq values: %d (events involved: %d)" % (len(dup), sum(dup.values())))
if dup:
    dk = sorted(dup)
    print("  duplicated seq span: %d .. %d (e.g. smallest 5: %s)"
          % (dk[0], dk[-1], dk[:5]))
adj_seq_dec = adj_time_dec = 0
for i in range(len(evs) - 1):
    a, b = evs[i].get("seq"), evs[i + 1].get("seq")
    if isinstance(a, int) and isinstance(b, int) and b < a:
        adj_seq_dec += 1
    ta, tb = ts(evs[i]), ts(evs[i + 1])
    if ta is not None and tb is not None and tb < ta:
        adj_time_dec += 1
print("adjacent seq decreases in file order: %d" % adj_seq_dec)
print("adjacent time decreases in file order: %d" % adj_time_dec)

mark = [e for e in evs if e.get("seq") == SEQ_MARK]
print("events with seq == %d: %d  first time: %s  types: %s"
      % (SEQ_MARK, len(mark),
         iso(ts(mark[0])) if mark and ts(mark[0]) is not None else "-",
         dict(Counter(e.get("type") for e in mark))))
t398 = ts(mark[0]) if mark else None
after398 = [e for e in evs if t398 is not None and ts(e) is not None and ts(e) > t398]
print("events with time > time(seq %d): %d" % (SEQ_MARK, len(after398)))

cut_ms = int(CUT.timestamp() * 1000)
after = [e for e in evs if ts(e) is not None and ts(e) > cut_ms]
print("== events after %s: %d" % (CUT.isoformat(timespec="minutes"), len(after)))
print("event types:", dict(sorted(Counter(e.get("type") for e in after).items())))


def kept(e):
    """Would parse_events (library_server.py:330-367) keep this event?"""
    t = e.get("type")
    data = e.get("data") if isinstance(e.get("data"), dict) else {}
    if t == "user/message":
        return True
    if t == "assistant/message":
        m = data.get("message") if isinstance(data.get("message"), dict) else {}
        return bool(extract.visible_text(m).strip())
    if t in ("tool/call", "tool/result"):
        return True
    return False


ka = [e for e in after if kept(e)]
sa = [e for e in after if not kept(e)]
print("of those, parse_events would KEEP: %d  types: %s"
      % (len(ka), dict(sorted(Counter(e.get("type") for e in ka).items()))))
print("of those, parse_events would SKIP: %d  types: %s"
      % (len(sa), dict(sorted(Counter(e.get("type") for e in sa).items()))))
for name, grp in (("kept", ka), ("skipped", sa)):
    ss = [e.get("seq") for e in grp if isinstance(e.get("seq"), int)]
    if ss:
        print("  %s-after-cut seq range: %d .. %d" % (name, min(ss), max(ss)))
ranges = {}
for e in sa:
    r = ranges.setdefault(e.get("type"), [e.get("seq"), e.get("seq")])
    if isinstance(e.get("seq"), int):
        r[0] = min(r[0], e["seq"])
        r[1] = max(r[1], e["seq"])
print("skipped-by-type seq ranges:", ranges)

ka_all = [e for e in evs if kept(e)]
ks = [e.get("seq") for e in ka_all if isinstance(e.get("seq"), int)]
print("== whole file: parse_events keeps %d events; kept seq range %d .. %d"
      % (len(ka_all), min(ks), max(ks)))
kt = [ts(e) for e in ka_all if ts(e) is not None]
print("   kept events time range: %s .. %s" % (iso(min(kt)), iso(max(kt))))

con = sqlite3.connect(DB)
row = con.execute(
    "SELECT source, start, end, project FROM cards WHERE id LIKE 'session-c5f86181%'"
).fetchone()
con.close()
print("== card row: source=%s start=%s end=%s project=%s" % row)
