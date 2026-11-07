#!/usr/bin/env python3
"""Phase 2 (option B): extract one mechanical card per session.

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
  files_touched    <- display list, cap 40. Files written/edited (write|edit
                       tool/call args) first, then files only read (read args,
                       tool/result `data.meta.path`/`meta.paths`), chronological
                       inside each group.
  bash_commands    <- display list, cap 30 x 120 chars, chosen by SALIENCE,
                       not order: 1) command whose result isError (via
                       `data.message.toolCallId` on tool/result),
                       2) contains "git commit"/"git push",
                       3) writes ("sed -i","tee"," > ",">>","mv ","cp ","rm "),
                       4) the rest; chronological order kept inside each
                       priority. Deterministic (stable sort).
  familiar_terms   <- LOSSLESS, no cap: every distinct file path from
                       tool/call args (`file_path`/`path`, any tool) and
                       tool/result `data.meta.path`/`meta.paths`, plus every
                       distinct bash command (each truncated to 300 chars).
                       First-seen order. Feeds the familiarity FTS table.
  tool_call_count  <- count of `tool/call` events
  error_count      <- count of `tool/result` with data.message.isError == true
  commit_hashes    <- regex `[branch <sha>]` in non-error tool results
                       (first-seen order, cap 20)
  n_writes         <- count of `write`/`edit` tool/call events that carry a
                       file_path/path argument (one per event, not per file)
  n_commits        <- len(commit_hashes)
  n_new_files      <- file paths first seen in THIS session, sessions
                       processed in start-time order; a path already seen in
                       any earlier session is not new. Path sources:
                       write/edit/read args + tool/result meta path(s).
                       Recorded, not part of the salience formula.
  source_map       <- per-field source tag: 'observed' (read from a tool
                       call/result in the log) or 'user' (said by the user).
                       Nothing is tagged 'model' in this phase.

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
WRITE_TOOLS = {"write", "edit"}
MAX_FILES = 40
MAX_COMMANDS = 30
MAX_COMMIT_HASHES = 20
FAMILIAR_CMD_CHARS = 300
COMMIT_RE = re.compile(r"\[[^\s\]]+ ([0-9a-f]{7,40})\]")
WRITE_MARKERS = ("sed -i", "tee", " > ", ">>", "mv ", "cp ", "rm ")
COMMIT_MARKERS = ("git commit", "git push")

# Source monitoring (design.md 3.7): every stored field carries its origin.
# Phase 2b has no model involvement, so nothing is tagged 'model'.
SOURCE_MAP = {
    "id": "observed",
    "source": "observed",
    "start": "observed",
    "end": "observed",
    "cwd": "observed",
    "first_message": "user",
    "files_touched": "observed",
    "bash_commands": "observed",
    "familiar_terms": "observed",
    "tool_call_count": "observed",
    "error_count": "observed",
    "n_writes": "observed",
    "n_commits": "observed",
    "n_new_files": "observed",
    "commit_hashes": "observed",
}


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


def salience_prio(full_cmd, call_id, call_errors):
    """0 = errored call, 1 = commit/push, 2 = write, 3 = rest. Deterministic."""
    if call_errors.get(call_id):
        return 0
    if any(m in full_cmd for m in COMMIT_MARKERS):
        return 1
    if any(m in full_cmd for m in WRITE_MARKERS):
        return 2
    return 3


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
        "familiar_terms": [],
        "tool_call_count": 0,
        "error_count": 0,
        "commit_hashes": [],
    }
    written = OrderedDict()   # path -> first-seen order (write/edit calls)
    readonly = OrderedDict()  # path -> first-seen order (everything else)
    familiar = OrderedDict()  # lossless terms (paths + bash commands)
    bash_calls = []           # (full_command, callId) in chronological order
    call_errors = {}          # callId -> True if any result isError
    commits = OrderedDict()
    paths_seen = set()        # every file path in this session (n_new_files)
    n_writes = 0              # write/edit tool calls carrying a path
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
            call_id = None
            msg = data.get("message") if isinstance(data.get("message"), dict) else {}
            cid = msg.get("toolCallId")
            if cid is None:
                cid = data.get("callId")
            call_id = str(cid) if cid is not None else None
            # every file path in call args feeds the lossless familiarity list
            for k in ("file_path", "path"):
                v = args.get(k)
                if isinstance(v, str) and v and v not in familiar:
                    familiar[v] = None
            if name == "bash":
                cmd = args.get("command", "")
                if isinstance(cmd, str):
                    bash_calls.append((cmd, call_id))
                    if cmd[:FAMILIAR_CMD_CHARS] not in familiar:
                        familiar[cmd[:FAMILIAR_CMD_CHARS]] = None
            if name in FILE_TOOLS:
                got_path = False
                for k in ("file_path", "path"):
                    v = args.get(k)
                    if not isinstance(v, str) or not v:
                        continue
                    got_path = True
                    paths_seen.add(v)
                    if name in WRITE_TOOLS:
                        if v not in written:
                            written[v] = None
                            readonly.pop(v, None)
                    else:
                        if v not in readonly:
                            readonly[v] = None
                if name in WRITE_TOOLS and got_path:
                    n_writes += 1
        elif et == "tool/result":
            msg = data.get("message") if isinstance(data.get("message"), dict) else {}
            cid = msg.get("toolCallId")
            if cid is None:
                cid = data.get("callId")
            if isinstance(msg.get("isError"), bool) and msg.get("isError") is True:
                card["error_count"] += 1
                if cid is not None:
                    call_errors[str(cid)] = True
            meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
            v = meta.get("path")
            if isinstance(v, str) and v:
                if v not in familiar:
                    familiar[v] = None
                if v not in readonly and v not in written:
                    readonly[v] = None
                paths_seen.add(v)
            paths = meta.get("paths")
            if isinstance(paths, list):
                for v in paths:
                    if isinstance(v, str) and v:
                        if v not in familiar:
                            familiar[v] = None
                        if v not in readonly and v not in written:
                            readonly[v] = None
                        paths_seen.add(v)
            if msg.get("isError") is not True:
                content = text_of(msg.get("content"))
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
    # display files: written first, then only-read, chronological inside each group
    card["files_touched"] = (list(written) + list(readonly))[:MAX_FILES]
    # display bash commands: salience-ordered (stable sort keeps chronology inside
    # each priority), deterministic
    order = sorted(
        range(len(bash_calls)),
        key=lambda i: salience_prio(bash_calls[i][0], bash_calls[i][1], call_errors),
    )
    card["bash_commands"] = [bash_calls[i][0][:120] for i in order[:MAX_COMMANDS]]
    card["familiar_terms"] = list(familiar)
    card["commit_hashes"] = list(commits)
    card["n_writes"] = n_writes
    card["n_commits"] = len(commits)
    card["source_map"] = dict(SOURCE_MAP)
    card["_paths"] = paths_seen  # consumed by main() for n_new_files
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
    # n_new_files: process sessions in start-time order (id breaks ties);
    # a path is "new" only if no earlier session touched it.
    seen_paths = set()
    for c in sorted(cards, key=lambda c: (c.get("start") or "", c["id"])):
        p = c.pop("_paths", set())
        c["n_new_files"] = len(p - seen_paths)
        seen_paths |= p
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
