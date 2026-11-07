#!/usr/bin/env python3
"""Phase 4 close-out, step 1: measure the live use of the biblioteca MCP tools.

Scan every session log (zstd) under the dsh sessions root, excluding this
session ($DSH_SESSION_ID), for tool calls named mcp__biblioteca__library_search
or mcp__biblioteca__library_open. Pick the most recent such session (by its
last event time). For each call print: seq, tool name, arguments, result size
in chars, result tokens (POST http://127.0.0.1:8092/tokenize), and whether the
result was truncated ("[truncated at" marker).

Also (step 2 evidence): across all sessions count assistant/message events
whose visible text (content blocks of type "text") is empty, and print at most
one key-name survey plus one truncated (80 chars) example of visible text.

No raw log lines are printed: only key names, counts, and <=2 truncated values.
Logs are opened read-only. Only network call: POST /tokenize.
"""
import json
import os
import subprocess
import urllib.request

sys_path_dir = os.path.dirname(os.path.abspath(__file__))
ROOT_LOGS = os.path.expanduser("~/.local/share/deepseek-harness/sessions")
TOKENIZER = "http://127.0.0.1:8092/tokenize"
BIB_NAMES = {"mcp__biblioteca__library_search", "mcp__biblioteca__library_open"}
ME = os.environ.get("DSH_SESSION_ID", "")


def visible_text(message):
    """Visible assistant text: only content blocks of type 'text'."""
    c = message.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts = [b.get("text", "") for b in c if isinstance(b, dict)
                 and b.get("type") == "text"]
        return " ".join(p for p in parts if p)
    return ""


def text_of(content):  # current library_server behavior (all 'text' keys)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content if isinstance(b, dict)]
        return " ".join(p for p in parts if p)
    return ""


def tokenize(text):
    req = urllib.request.Request(
        TOKENIZER,
        data=json.dumps({"content": text}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.loads(r.read())
    return len(d["tokens"])


def scan(path):
    out = subprocess.run(["zstd", "-dc", path], stdout=subprocess.PIPE).stdout
    events = []
    for line in out.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if isinstance(ev, dict):
            events.append(ev)
    return events


def all_logs():
    for cwd_dir in sorted(os.listdir(ROOT_LOGS)):
        d = os.path.join(ROOT_LOGS, cwd_dir)
        if not os.path.isdir(d):
            continue
        for sdir in sorted(os.listdir(d)):
            sd = os.path.join(d, sdir)
            if not os.path.isdir(sd) or sdir == ME:
                continue
            path = os.path.join(sd, "session.v4.jsonl.zstd")
            if os.path.isfile(path):
                yield path


def end_of(evs):
    return max((e.get("time") for e in evs
                if isinstance(e.get("time"), int)), default=0)


def main():
    import datetime
    candidates = []  # (end_ms, path, n_calls)
    empty_vis, empty_cur = 0, 0
    empty_vis_blocktypes = {}
    for path in all_logs():
        try:
            evs = scan(path)
        except Exception as exc:
            print("skip", path, exc)
            continue
        biblio = [e for e in evs
                  if e.get("type") == "tool/call"
                  and (e.get("data") or {}).get("name") in BIB_NAMES]
        if biblio:
            candidates.append((end_of(evs), path, len(biblio)))
        for e in evs:
            if e.get("type") == "assistant/message":
                m = (e.get("data") or {}).get("message") or {}
                vis = visible_text(m)
                if not vis.strip():
                    empty_vis += 1
                    c = m.get("content")
                    if isinstance(c, list):
                        bt = tuple(sorted(b.get("type", "?") for b in c
                                          if isinstance(b, dict)))
                        empty_vis_blocktypes[bt] = empty_vis_blocktypes.get(bt, 0) + 1
                cur = text_of(m.get("content"))
                if not cur.strip():
                    empty_cur += 1
    candidates.sort(reverse=True)
    print("sessions with biblioteca tool calls: %d (newest first)" % len(candidates))
    for end, path, n in candidates[:5]:
        print("  %s  %s  calls=%d"
              % (datetime.datetime.fromtimestamp(end / 1000).isoformat(),
                 os.path.basename(os.path.dirname(path)), n))

    if not candidates:
        raise SystemExit("no candidate session found")

    end, path, n = candidates[0]
    sid = os.path.basename(os.path.dirname(path))
    evs = scan(path)
    import datetime
    print("== selected: %s end=%s calls=%d"
          % (sid, datetime.datetime.fromtimestamp(end / 1000).isoformat(), n))

    # map toolCallId -> tool/result text
    results = {}
    for e in evs:
        if e.get("type") == "tool/result":
            d = e.get("data") or {}
            m = d.get("message") or {}
            cid = m.get("toolCallId") or d.get("callId")
            if cid is not None:
                results[str(cid)] = text_of(m.get("content"))

    total_chars, total_toks = 0, 0
    for e in evs:
        if e.get("type") != "tool/call":
            continue
        d = e.get("data") or {}
        if d.get("name") not in BIB_NAMES:
            continue
        cid = d.get("callId")
        args = d.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                pass
        res = results.get(str(cid), "")
        toks = tokenize(res)
        trunc = "[truncated at" in res
        total_chars += len(res)
        total_toks += toks
        print("seq=%s name=%s args=%s result_chars=%d result_tokens=%d truncated=%s"
              % (e.get("seq"), d.get("name"), json.dumps(args, ensure_ascii=False),
                 len(res), toks, trunc))
    print("TOTALS: calls=%d result_chars=%d result_tokens=%d"
          % (n, total_chars, total_toks))

    # agent verdict: visible text mentioning imgproxy/pending (<=2 x 80 chars)
    hits = []
    for e in evs:
        if e.get("type") != "assistant/message":
            continue
        vis = visible_text((e.get("data") or {}).get("message") or {})
        if ("imgproxy" in vis.lower()) or ("end state" in vis.lower()) or ("pending" in vis.lower()):
            hits.append((e.get("seq"), vis))
    print("verdict candidate messages: %d" % len(hits))
    for seq, vis in hits[:2]:
        print("  seq=%s: %s" % (seq, vis.replace("\n", " ")[:80]))

    print("== step2 evidence (all sessions)")
    print("assistant messages with empty VISIBLE text (type 'text' blocks):", empty_vis)
    print("  block-type combos of those:", empty_vis_blocktypes)
    print("assistant messages where CURRENT text_of is empty:", empty_cur)


if __name__ == "__main__":
    main()
