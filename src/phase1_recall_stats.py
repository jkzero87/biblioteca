#!/usr/bin/env python3
"""Phase 1 recall diagnostic: per-session dsh-recall injection stats.

Read-only: streams each session log via `zstd -dc` (subprocess), never
touches the file. Prints aggregates only.

Definitions (reproducible):
  session chars  = total Unicode code points in the decompressed .jsonl
                   (UTF-8 decoded, newlines included).
  auto-capped result = a `tool/result` event whose top-level `surfaceOp` is
                   {"op":"replace",...} and whose message text contains the
                   dsh-recall marker  [... N characters removed. call recall
                   with startSeq=S endSeq=S for the full original ...]
  recall event   = one log line that is any of:
                   a) `compaction/prune` (written only by dsh-recall in this
                      profile, verified by grep over node_modules)
                   b) an auto-capped result (definition above)
                   c) a `tool/call` with name in {recall, recall_index}
                   d) the `tool/result` of such a call (matched via its
                      sourceEventSeqs containing the call's seq)
  recall chars   = sum of code-point lengths of those raw JSONL lines.
  marker chars   = sum of the marker strings' lengths (chars the plugin
                   invented, not copied from original tool output).
"""
import json
import re
import subprocess
import sys
from collections import Counter

MARKER_RE = re.compile(
    r"\[\.\.\. (\d+) characters removed\. call recall with startSeq=(\d+) "
    r"endSeq=(\d+) for the full original \.\.\.\]"
)
RECALL_TOOLS = {"recall", "recall_index"}


def stream_lines(path):
    proc = subprocess.run(
        ["zstd", "-dc", path], capture_output=True, check=True
    )
    return proc.stdout.decode("utf-8").splitlines()


def analyze(path):
    lines = stream_lines(path)
    total_chars = sum(len(ln) + 1 for ln in lines)  # +1 newline each
    types = Counter()
    n_auto_capped = 0
    auto_capped_chars = 0
    n_prune = 0
    prune_chars = 0
    marker_chars = 0
    recall_calls = Counter()
    recall_call_chars = 0
    recall_result_chars = 0
    call_seqs = {}  # callId -> (seq, name)
    samples = []
    session_meta = None

    for ln in lines:
        types_json = None
        try:
            o = json.loads(ln)
        except Exception:
            types["<unparseable>"] += 1
            continue
        t = o.get("type", "<no-type>")
        types[t] += 1
        if t == "session" and session_meta is None:
            session_meta = {
                "id": o.get("id"),
                "cwd": o.get("cwd"),
                "createdAt": o.get("createdAt"),
            }
        is_recall_line = False

        if t == "compaction/prune":
            n_prune += 1
            prune_chars += len(ln)
            is_recall_line = True

        if t == "tool/call":
            d = o.get("data") or {}
            name = d.get("name")
            if name in RECALL_TOOLS:
                recall_calls[name] += 1
                recall_call_chars += len(ln)
                is_recall_line = True
                call_seqs[d.get("callId")] = (o.get("seq"), name)
            else:
                call_seqs[d.get("callId")] = (o.get("seq"), name)

        if t == "tool/result":
            capped = isinstance(o.get("surfaceOp"), dict) and o["surfaceOp"].get("op") == "replace"
            markers = MARKER_RE.findall(ln)
            if capped and markers:
                n_auto_capped += 1
                auto_capped_chars += len(ln)
                marker_chars += sum(len(f"[... {a} characters removed. call recall with startSeq={b} endSeq={c} for the full original ...]") for a, b, c in markers)
                if len(samples) < 3:
                    samples.append(ln[ln.find("[...") : ln.find("[...") + 120])
                is_recall_line = True
            src = o.get("sourceEventSeqs") or []
            for s in src:
                if s in call_seqs and call_seqs[s][1] in RECALL_TOOLS:
                    recall_result_chars += len(ln)
                    is_recall_line = True
                    break

    recall_events = n_prune + n_auto_capped + sum(recall_calls.values())
    # results of explicit calls are the events' outcomes; counted as events too
    # (reported separately, included in recall_chars below)
    n_explicit_results = 0
    # recount explicit results for the table
    for ln in lines:
        try:
            o = json.loads(ln)
        except Exception:
            continue
        if o.get("type") != "tool/result":
            continue
        src = o.get("sourceEventSeqs") or []
        for s in src:
            if s in call_seqs and call_seqs[s][1] in RECALL_TOOLS:
                n_explicit_results += 1
                break
    recall_events_total = n_prune + n_auto_capped + sum(recall_calls.values()) + n_explicit_results
    recall_chars = prune_chars + auto_capped_chars + recall_call_chars + recall_result_chars
    pct = 100.0 * recall_chars / total_chars if total_chars else 0.0

    print(f"== {path}")
    if session_meta:
        print(f"   session: {session_meta['id']}  cwd={session_meta['cwd']}  createdAt={session_meta['createdAt']}")
    print(f"   lines={len(lines)}  session_chars={total_chars}")
    print(f"   event types (top 12): {', '.join(f'{k}={v}' for k, v in types.most_common(12))}")
    print(f"   AUTO (dsh-recall capSession):")
    print(f"     capped tool/result events: {n_auto_capped}  (line chars {auto_capped_chars})")
    print(f"     marker chars invented:     {marker_chars}")
    print(f"     compaction/prune events:   {n_prune}  (line chars {prune_chars})")
    print(f"   EXPLICIT tool calls:")
    for name in ("recall", "recall_index"):
        print(f"     {name}: {recall_calls.get(name, 0)} calls")
    print(f"     explicit-call results: {n_explicit_results} (line chars {recall_result_chars})")
    print(f"   TOTAL recall events: {recall_events_total}   recall chars: {recall_chars}   pct of session: {pct:.3f}%")
    for s in samples:
        print(f"   marker sample: {s!r}")
    print()
    return {
        "session": session_meta,
        "session_chars": total_chars,
        "recall_events": recall_events_total,
        "recall_chars": recall_chars,
        "pct": pct,
    }


def main():
    rows = [analyze(p) for p in sys.argv[1:]]
    print("SUMMARY")
    for r in rows:
        sid = (r["session"] or {}).get("id", "?")
        print(f"  {sid}: events={r['recall_events']} recall_chars={r['recall_chars']} "
              f"session_chars={r['session_chars']} pct={r['pct']:.3f}%")


if __name__ == "__main__":
    main()
