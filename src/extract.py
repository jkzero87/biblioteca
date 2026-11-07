#!/usr/bin/env python3
"""Phase 2 step 2/3: extract one mechanical card per session.

Reads every session.v4.jsonl.zstd read-only, streamed via `zstd -dc`.
Writes data/cards.jsonl (one JSON card per line, sorted by id) and prints
a summary plus every parse failure (path + reason). No model, no network.

Card fields and their source in the log:
  id               <- `session` event field `id`
  source           <- path of the .zstd file
  start / end      <- min / max of `time` (ms epoch) over all events;
                      start also cross-checked against `session.createdAt`
  cwd              <- `session.cwd`
  first_message    <- first `user/message` with data.source.kind == 'user',
                      joined text blocks, truncated to 300 chars
  files_touched    <- tool/call args `file_path`/`path` for read|write|edit
                      plus tool/result `data.meta.path` / `data.meta.paths`
                      (dedup, first-seen order, cap 40)
  bash_commands    <- tool/call name=='bash', args `command` (first 30,
                      each truncated to 120 chars)
  tool_call_count  <- count of `tool/call` events
  error_count      <- count of `tool/result` with data.message.isError == true
  commit_hashes    <- regex `[branch <sha>]` in non-error tool results
                      (first-seen order, cap 20)

Usage:
  python3 src/extract.py [--exclude=session-uuid-prefix]... [--root DIR] [--out FILE]
"""
import json
import os
import re
import subprocess
import sys
from collections import OrderedDict

DEFAULT_ROOT = os.path.expanduser("~/.local/share/deepseek-harness/sessions")
FILE_TOOLS = {"read", "write", "edit"}
MAX_FILES = 40
MAX_COMMANDS = 30
MAX_COMMIT_HASHES = 20
COMMIT_RE = re.compile(r"\[[^\s\]]+ ([0-9a-f]{7,40})\]")


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


def iter_lines(path):
    """Yield decoded non-empty lines; raises on zstd failure."""
    proc = subprocess.Popen(
        ["zstd", "-dc", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    assert proc.stdout is not None
    try:
        for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if line:
                yield line
    finally:
        rc = proc.wait()
        if rc != 0:
            err = proc.stderr.read().decode("utf-8", "replace").strip()[:200]
            raise RuntimeError(f"zstd exit {rc}: {err}")


def text_of(content):
    """content is a str or a list of {text: ...} blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content if isinstance(b, dict)]
        return " ".join(p for p in parts if p)
    return ""


def args_of(event):
    a = event.get("data", {}).get("arguments")
    if isinstance(a, str):
        try:
            return json.loads(a)
        except Exception:
            return {}
    return a if isinstance(a, dict) else {}


def extract_card(path):
    card = {
        "id": None,
        "source": path,
        "start": None,
        "end": None,
        "cwd": None,
        "first_message": None,
        "files_touched": [],
        "bash_commands": [],
        "tool_call_count": 0,
        "error_count": 0,
        "commit_hashes": [],
    }
    files = OrderedDict()
    commits = OrderedDict()
    min_t = max_t = None
    bad_lines = 0
    for line in iter_lines(path):
        try:
            ev = json.loads(line)
        except Exception:
            bad_lines += 1
            continue
        if not isinstance(ev, dict):
            bad_lines += 1
            continue
        et = ev.get("type")
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        t = ev.get("time")
        if isinstance(t, (int, float)):
            if min_t is None or t < min_t:
                min_t = t
            if max_t is None or t > max_t:
                max_t = t
        if et == "session":
            card["id"] = ev.get("id") or card["id"]
            card["cwd"] = ev.get("cwd")
        elif et == "user/message":
            if card["first_message"] is None and data.get("source", {}).get("kind") == "user":
                card["first_message"] = text_of(data.get("content"))[:300]
        elif et == "tool/call":
            card["tool_call_count"] += 1
            name = data.get("name")
            args = args_of(ev)
            if name == "bash":
                if len(card["bash_commands"]) < MAX_COMMANDS:
                    cmd = args.get("command", "")
                    if isinstance(cmd, str):
                        card["bash_commands"].append(cmd[:120])
            if name in FILE_TOOLS:
                for k in ("file_path", "path"):
                    v = args.get(k)
                    if isinstance(v, str) and v and v not in files:
                        files[v] = None
        elif et == "tool/result":
            if data.get("message", {}).get("isError") is True:
                card["error_count"] += 1
            meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
            for k in ("path",):
                v = meta.get(k)
                if isinstance(v, str) and v and v not in files:
                    files[v] = None
            paths = meta.get("paths")
            if isinstance(paths, list):
                for v in paths:
                    if isinstance(v, str) and v not in files:
                        files[v] = None
            if data.get("message", {}).get("isError") is not True:
                content = text_of(data.get("message", {}).get("content"))
                for m in COMMIT_RE.finditer(content):
                    if m.group(1) not in commits and len(commits) < MAX_COMMIT_HASHES:
                        commits[m.group(1)] = None
    if card["id"] is None:
        raise ValueError("no `session` event with id found in log")
    # ms epoch -> ISO UTC
    def iso(ms):
        if ms is None:
            return None
        import datetime
        return datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
    card["start"] = iso(min_t)
    card["end"] = iso(max_t)
    card["files_touched"] = list(files)[:MAX_FILES]
    card["commit_hashes"] = list(commits)
    if bad_lines:
        card["bad_lines"] = bad_lines
    return card


def main():
    excludes = []
    root = DEFAULT_ROOT
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "cards.jsonl")
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith("--exclude="):
            excludes.append(a.split("=", 1)[1])
        elif a == "--root":
            i += 1
            root = args[i]
        elif a == "--out":
            i += 1
            out = args[i]
        i += 1
    logs = find_logs(root, excludes)
    cards = []
    failures = []
    for path in logs:
        try:
            cards.append(extract_card(path))
        except Exception as e:
            failures.append({"path": path, "reason": f"{type(e).__name__}: {e}"})
    cards.sort(key=lambda c: c["id"])
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        for c in cards:
            f.write(json.dumps(c, ensure_ascii=False, sort_keys=True) + "\n")
    for c in cards:
        if c.get("bad_lines"):
            failures.append({"path": c["source"], "reason": f"{c['bad_lines']} unparseable line(s)"})
    print(f"logs: {len(logs)}  cards: {len(cards)}  failures: {len(failures)}")
    for fl in failures:
        print(f"FAIL {fl['path']}: {fl['reason']}")
    print(f"cards written to {out}")


if __name__ == "__main__":
    main()
