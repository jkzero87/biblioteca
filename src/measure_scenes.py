#!/usr/bin/env python3
r"""Phase 2 (measurement only): per-session scene metrics over every session log.

Streams each session.v4.jsonl.zstd exactly once (read-only, via `zstd -dc`)
and computes per session:
  user_messages  <- count of `user/message` events with data.source.kind == 'user'
  chars          <- total decoded chars of the decompressed log (utf-8, replace)
  tool_calls     <- count of `tool/call` events
  commits        <- distinct commit shas, same regex and source as src/extract.py:
                    COMMIT_RE = \[[^\s\]]+ ([0-9a-f]{7,40}) applied to
                    text_of(data.message.content) of non-error tool/result events
                    (data.message.isError is not True). No cap, distinct shas.

Prints aggregates only (never raw log lines), and writes the same report to
--out (default: reports/phase-2-scene-measure.md) with the exact re-run command.
No model, no network. stdlib + zstd CLI.

Usage:
  python3 src/measure_scenes.py [--root DIR] [--exclude PREFIX]... [--out FILE]

Default --exclude: the DSH_SESSION_ID environment variable (this session), so
this measurement never includes itself.
"""
import json
import os
import re
import statistics
import subprocess
import sys

DEFAULT_ROOT = os.path.expanduser("~/.local/share/deepseek-harness/sessions")
DEFAULT_OUT = "reports/phase-2-scene-measure.md"
COMMIT_RE = re.compile(r"\[[^\s\]]+ ([0-9a-f]{7,40})")  # same regex as src/extract.py


def find_logs(root, excludes):
    logs = []
    for ws in sorted(os.listdir(root)):
        wdir = os.path.join(root, ws)
        if not os.path.isdir(wdir):
            continue
        for name in sorted(os.listdir(wdir)):
            if not name.startswith("session-"):
                continue
            path = os.path.join(wdir, name, "session.v4.jsonl.zstd")
            if not os.path.isfile(path):
                continue
            if any(name.startswith(x) for x in excludes):
                continue
            logs.append(path)
    return logs


def measure(path):
    """Stream the log once; return (stats, failures)."""
    stats = {
        "id": None,
        "source": path,
        "user_messages": 0,
        "chars": 0,
        "tool_calls": 0,
        "commit_shas": set(),
        "bad_lines": 0,
    }
    proc = subprocess.Popen(
        ["zstd", "-dc", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    assert proc.stdout is not None
    try:
        for raw in proc.stdout:
            decoded = raw.decode("utf-8", "replace")
            stats["chars"] += len(decoded)
            line = decoded.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                stats["bad_lines"] += 1
                continue
            if not isinstance(ev, dict):
                stats["bad_lines"] += 1
                continue
            et = ev.get("type")
            data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            if et == "session":
                if stats["id"] is None:
                    stats["id"] = ev.get("id")
            elif et == "user/message":
                if data.get("source", {}).get("kind") == "user":
                    stats["user_messages"] += 1
            elif et == "tool/call":
                stats["tool_calls"] += 1
            elif et == "tool/result":
                msg = data.get("message") if isinstance(data.get("message"), dict) else {}
                if msg.get("isError") is not True:
                    content = msg.get("content")
                    if isinstance(content, list):
                        content = " ".join(
                            b.get("text", "") for b in content
                            if isinstance(b, dict) and b.get("text")
                        )
                    if isinstance(content, str):
                        for m in COMMIT_RE.finditer(content):
                            stats["commit_shas"].add(m.group(1))
    finally:
        rc = proc.wait()
        err = proc.stderr.read().decode("utf-8", "replace")[:200]
    if rc != 0:
        raise RuntimeError(f"zstd exit {rc}: {err}")
    if stats["id"] is None:
        stats["id"] = os.path.basename(os.path.dirname(path))
    return stats


def msg_bucket(n):
    if n <= 0:
        return None  # reported separately, not forced into bucket 1
    if n == 1:
        return "1"
    if n <= 3:
        return "2-3"
    if n <= 6:
        return "4-6"
    if n <= 10:
        return "7-10"
    if n <= 20:
        return "11-20"
    return ">20"


def size_bucket(c):
    if c < 100_000:
        return "<100K"
    if c < 500_000:
        return "100K-500K"
    if c < 1_000_000:
        return "500K-1M"
    if c < 3_000_000:
        return "1M-3M"
    return ">3M"


def histogram(stats_list, key_fn, bucket_fn, bucket_order):
    """bucket -> (session count, total chars); pct = bucket chars / total chars."""
    total_chars = sum(s["chars"] for s in stats_list)
    acc = {b: [0, 0] for b in bucket_order}
    for s in stats_list:
        b = bucket_fn(s[key_fn])
        if b in acc:
            acc[b][0] += 1
            acc[b][1] += s["chars"]
    rows = []
    for b in bucket_order:
        n, c = acc[b]
        pct = 100.0 * c / total_chars if total_chars else 0.0
        rows.append((b, n, c, pct))
    return rows, total_chars


def build_report(stats_list, failures, total_chars, args):
    L = []
    L.append("# Phase 2 — scene measurement (per-session aggregates)")
    L.append("")
    L.append("Exact re-run command (from `~/biblioteca`):")
    L.append("")
    L.append("```")
    L.append("cd ~/biblioteca && python3 src/measure_scenes.py --out reports/phase-2-scene-measure.md")
    for x in args["exclude"]:
        L.append(f"# exclude: {x}")
    L.append("```")
    L.append("")
    L.append(f"Sessions measured: {len(stats_list)} (this session excluded via DSH_SESSION_ID)")
    L.append(f"Total decompressed chars: {total_chars}")
    if failures:
        L.append(f"Read failures: {len(failures)}")
        for f in failures[:10]:
            L.append(f"- {f['source']}: {f['reason']}")
    bad = sum(s["bad_lines"] for s in stats_list)
    if bad:
        L.append(f"Unparseable lines across all logs: {bad}")
    L.append("")
    L.append("## 1. Histogram: user messages per session")
    L.append("")
    L.append("| bucket | sessions | total chars | % of total chars |")
    L.append("|---|---:|---:|---:|")
    rows, _ = histogram(stats_list, "user_messages", msg_bucket,
                        ["1", "2-3", "4-6", "7-10", "11-20", ">20"])
    for b, n, c, pct in rows:
        L.append(f"| {b} | {n} | {c} | {pct:.1f}% |")
    zeros = [s for s in stats_list if s["user_messages"] == 0]
    if zeros:
        zc = sum(s["chars"] for s in zeros)
        zp = 100.0 * zc / total_chars if total_chars else 0.0
        L.append(f"| (0 user messages) | {len(zeros)} | {zc} | {zp:.1f}% |")
    L.append("")
    L.append("## 2. Histogram: decompressed size per session")
    L.append("")
    L.append("Bounds: <100K; [100K,500K); [500K,1M); [1M,3M); >=3M chars.")
    L.append("")
    L.append("| bucket | sessions | total chars | % of total chars |")
    L.append("|---|---:|---:|---:|")
    rows, _ = histogram(stats_list, "chars", size_bucket,
                        ["<100K", "100K-500K", "500K-1M", "1M-3M", ">3M"])
    for b, n, c, pct in rows:
        L.append(f"| {b} | {n} | {c} | {pct:.1f}% |")
    L.append("")
    L.append("## 3. Top 10 sessions by decompressed size")
    L.append("")
    L.append("| id | chars | user messages | tool calls | commits |")
    L.append("|---|---:|---:|---:|---:|")
    top = sorted(stats_list, key=lambda s: (-s["chars"], s["id"]))[:10]
    for s in top:
        L.append(f"| {s['id']} | {s['chars']} | {s['user_messages']} | "
                 f"{s['tool_calls']} | {len(s['commit_shas'])} |")
    L.append("")
    L.append("## 4. Median / p90 of user messages per session")
    vals = sorted(s["user_messages"] for s in stats_list)
    med = statistics.median(vals)
    p90 = statistics.quantiles(vals, n=10, method="inclusive")[8] if len(vals) > 1 else float(vals[0])
    L.append(f"- median: {med:g}")
    L.append(f"- p90 (linear interpolation, inclusive): {p90:g}")
    L.append("")
    return "\n".join(L) + "\n"


def main():
    ap = sys.argv[1:]
    args = {"exclude": [], "out": DEFAULT_OUT, "root": DEFAULT_ROOT}
    i = 0
    while i < len(ap):
        a = ap[i]
        if a in ("--root", "--out", "--exclude"):
            if i + 1 >= len(ap):
                sys.exit(f"missing value for {a}")
            v = ap[i + 1]
            if a == "--root":
                args["root"] = v
            elif a == "--out":
                args["out"] = v
            else:
                args["exclude"].append(v)
            i += 2
        else:
            sys.exit(f"unknown arg {a}")
    env_sid = os.environ.get("DSH_SESSION_ID", "")
    if env_sid:
        args["exclude"].append(env_sid)

    logs = find_logs(args["root"], args["exclude"])
    stats_list = []
    failures = []
    for path in logs:
        try:
            stats_list.append(measure(path))
        except Exception as e:
            failures.append({"source": path, "reason": f"{type(e).__name__}: {e}"})
    stats_list.sort(key=lambda s: s["id"])
    total_chars = sum(s["chars"] for s in stats_list)

    report = build_report(stats_list, failures, total_chars, args)
    outdir = os.path.dirname(args["out"])
    if outdir:
        os.makedirs(outdir, exist_ok=True)
    with open(args["out"], "w") as f:
        f.write(report)

    # print aggregates only
    print(f"logs: {len(logs)}  measured: {len(stats_list)}  failures: {len(failures)}")
    print(f"total decompressed chars: {total_chars}")
    vals = sorted(s["user_messages"] for s in stats_list)
    if vals:
        med = statistics.median(vals)
        p90 = statistics.quantiles(vals, n=10, method="inclusive")[8] if len(vals) > 1 else float(vals[0])
        print(f"median user messages: {med:g}  p90: {p90:g}")
    rows, _ = histogram(stats_list, "user_messages", msg_bucket,
                        ["1", "2-3", "4-6", "7-10", "11-20", ">20"])
    print("user-message buckets (sessions, %chars):")
    for b, n, c, pct in rows:
        print(f"  {b}: {n}  {pct:.1f}%")
    rows, _ = histogram(stats_list, "chars", size_bucket,
                        ["<100K", "100K-500K", "500K-1M", "1M-3M", ">3M"])
    print("size buckets (sessions, %chars):")
    for b, n, c, pct in rows:
        print(f"  {b}: {n}  {pct:.1f}%")
    print("report written to", args["out"])


if __name__ == "__main__":
    main()
