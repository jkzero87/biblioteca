#!/usr/bin/env python3
"""Step 1: inspect real session logs (read-only, streamed via `zstd -dc`).

Prints (and saves to data/inspection/inspect_report.txt):
  - per log: candidate event-type field(s) -> value counts
  - per (log, type value): top-level and nested (parent.child, depth<=2) key names
  - per (type value, field): at most 2 short truncated sample values
  - total line counts, unparseable lines
No decompressed data is ever written to disk; no values kept beyond samples.
Usage: python3 src/inspect_events.py [--exclude=session-uuid-prefix]
"""
import json
import os
import subprocess
import sys
from collections import defaultdict, OrderedDict

SESSIONS_ROOT = os.path.expanduser("~/.local/share/deepseek-harness/sessions")
SAMPLES_PER_FIELD = 2
SAMPLE_MAX_CHARS = 80
MAX_TYPE_DISTINCT = 40          # a string field is "type-like" if <= this many distinct values
MAX_TYPE_VALUES_SHOWN = 25
MAX_KEYS_SHOWN = 60
MAX_LOGS_FULL = 147             # print per-log type lines for all logs; structure section per type


def find_logs(exclude_names):
    logs = []
    for ws in sorted(os.listdir(SESSIONS_ROOT)):
        wdir = os.path.join(SESSIONS_ROOT, ws)
        if not os.path.isdir(wdir):
            continue
        for name in sorted(os.listdir(wdir)):
            if not name.startswith("session-"):
                continue
            path = os.path.join(wdir, name, "session.v4.jsonl.zstd")
            if not os.path.isfile(path):
                continue
            if any(name.startswith(x) for x in exclude_names):
                continue
            logs.append(path)
    return logs


def short(path):
    # workspace--/session-uuid
    parts = path.split(os.sep)
    return f"{parts[-3]}/{parts[-2]}"


def collect_keys(obj, prefix, depth, keys):
    if depth > 2:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            full = f"{prefix}.{k}" if prefix else k
            keys.add(full)
            if isinstance(v, dict):
                collect_keys(v, full, depth + 1, keys)
            elif isinstance(v, list):
                for el in v[:1]:
                    if isinstance(el, dict):
                        collect_keys(el, full + "[]", depth + 1, keys)
            elif isinstance(v, str) and len(keys) < 100000:
                pass


def collect_keys_with_samples(obj, prefix, depth, keys, samples):
    if depth > 2:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            full = f"{prefix}.{k}" if prefix else k
            keys.add(full)
            if isinstance(v, dict):
                collect_keys_with_samples(v, full, depth + 1, keys, samples)
            elif isinstance(v, list):
                for el in v[:1]:
                    if isinstance(el, dict):
                        collect_keys_with_samples(el, full + "[]", depth + 1, keys, samples)
            elif isinstance(v, str):
                if len(samples.get(full, [])) < SAMPLES_PER_FIELD:
                    samples.setdefault(full, []).append(v.replace("\n", "\\n")[:SAMPLE_MAX_CHARS])


def main():
    exclude_names = []
    for a in sys.argv[1:]:
        if a.startswith("--exclude="):
            exclude_names.append(a.split("=", 1)[1])
    logs = find_logs(exclude_names)
    print(f"logs to inspect: {len(logs)}")

    out = []
    w = out.append

    # --- per-log discovery ---
    # per log: string field (depth 0 or 1) -> value -> count
    log_field_values = OrderedDict()      # path -> OrderedDict(field -> OrderedDict(val -> count))
    log_field_lines = {}                  # path -> {field: lines}
    # per (path, type-field, type-value): keys, samples
    structure = {}                        # (path, field, value) -> {"keys": set, "samples": {}}
    total_lines = 0
    bad_lines = 0

    for path in logs:
        proc = subprocess.Popen(["zstd", "-dc", path], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        assert proc.stdout is not None
        field_vals = OrderedDict()
        field_lines = {}
        for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            total_lines += 1
            try:
                ev = json.loads(line)
            except Exception:
                bad_lines += 1
                continue
            if not isinstance(ev, dict):
                continue
            for k, v in ev.items():
                if isinstance(v, str) and len(v) <= 120:
                    fv = field_vals.setdefault(k, OrderedDict())
                    fv[v] = fv.get(v, 0) + 1
                    field_lines[k] = field_lines.get(k, 0) + 1
        proc.wait()
        log_field_values[path] = field_vals
        log_field_lines[path] = field_lines

    # decide per-log "type field": depth-0 string field with few distinct values, highest coverage
    chosen = {}
    for path, fv in log_field_values.items():
        best = None
        best_cov = -1
        for field, vals in fv.items():
            n = len(vals)
            if 2 <= n <= MAX_TYPE_DISTINCT:
                cov = sum(vals.values())
                if cov > best_cov:
                    best, best_cov = field, cov
        chosen[path] = best

    # second pass for structure of chosen type fields (only those logs)
    for path in logs:
        tfield = chosen.get(path)
        if tfield is None:
            continue
        proc = subprocess.Popen(["zstd", "-dc", path], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            tv = ev.get(tfield)
            if not isinstance(tv, str):
                continue
            key = (path, tfield, tv)
            st = structure.setdefault(key, {"keys": set(), "samples": {}})
            collect_keys_with_samples(ev, "", 0, st["keys"], st["samples"])
        proc.wait()

    # --- report ---
    w(f"logs: {len(logs)}  total non-empty lines: {total_lines}  unparseable: {bad_lines}")
    w("")
    w("=== PER-LOG TYPE FIELDS (field: value=count ...) ===")
    for path, fv in log_field_values.items():
        tfield = chosen.get(path)
        parts = []
        for field, vals in fv.items():
            if len(vals) > MAX_TYPE_DISTINCT:
                continue
            mark = "*" if field == tfield else ""
            shown = dict(list(vals.items())[:MAX_TYPE_VALUES_SHOWN])
            parts.append(f"{mark}{field}({len(vals)}): " + ", ".join(f"{v}={c}" for v, c in shown.items()))
        w(f"{short(path)}: " + (" | ".join(parts) if parts else "(no small-distinct string fields)"))
    w("")
    w("=== STRUCTURE PER (LOG, TYPE VALUE): keys + <=2 samples/field ===")
    for (path, tfield, tv), st in sorted(structure.items(), key=lambda kv: (kv[0][0], kv[0][2])):
        keys = sorted(st["keys"])
        w(f"--- {short(path)} {tfield}={tv!r} keys({len(keys)}):")
        for k in keys[:MAX_KEYS_SHOWN]:
            sm = st["samples"].get(k)
            s = ""
            if sm:
                s = "  e.g. " + " || ".join(f"{x!r}" for x in sm[:SAMPLES_PER_FIELD])
            w(f"    {k}{s}")
        if len(keys) > MAX_KEYS_SHOWN:
            w(f"    ... {len(keys) - MAX_KEYS_SHOWN} more keys")

    text = "\n".join(out) + "\n"
    rep_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "inspection")
    os.makedirs(rep_dir, exist_ok=True)
    rep_path = os.path.join(rep_dir, "inspect_report.txt")
    with open(rep_path, "w") as f:
        f.write(text)
    # condensed stdout: only lines about chosen type fields + totals
    print(text[:2000])
    print(f"... report saved to {rep_path} ({len(text)} chars, {text.count(chr(10))} lines)")


if __name__ == "__main__":
    main()
