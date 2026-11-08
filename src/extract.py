#!/usr/bin/env python3
"""Phase 2 (option B): extract one mechanical card per session.

Reads every session.v4.jsonl.zstd read-only, streamed via `zstd -dc`.
Writes data/cards.jsonl (one JSON card per line, sorted by id) and prints
a summary plus every parse failure (path + reason). No model, no network.

Card fields and their source in the log:
  id               <- `session` event field `id`
  source           <- path of the .zstd file
  start / end      <- min / max of `time` (ms epoch) over ACTIVITY events
                       only (user/message, assistant/message, tool/call,
                       tool/result) — never bookkeeping events (session
                       end-seed, step/end, turn/end, workspace/changes, ...)
                       written at session close (phase 4 fix, step 1)
  cwd              <- `session.cwd`
  first_message    <- first `user/message` with data.source.kind == 'user'
                       whose text does not start with a harness prefix
                       (HARNESS_PREFIXES — harness text is never the user),
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
                       priority. Deterministic (stable sort). RETRIEVAL
                       commands (is_retrieval below) never appear here.
  familiar_terms   <- LOSSLESS, no cap: every distinct file path from
                       tool/call args (`file_path`/`path`, any tool) and
                       tool/result `data.meta.path`/`meta.paths`, plus every
                       distinct bash command (each truncated to 300 chars),
                       INCLUDING retrieval commands. First-seen order.
                       Feeds the familiarity FTS table.
  familiar_classes <- one class per familiar_terms entry, same order
                       (fix pass 2): 'write'/'read' for file paths (written
                       vs everything else), 'retrieval' for retrieval bash
                       commands and for grep/glob tool-call path args
                       (searching is not evidence), 'error'/'commit'/'write'/
                       'read' for the other bash commands (the same salience
                       classes as command_classes). Feeds the [searched]
                       label in search.py.
  tool_call_count  <- count of `tool/call` events
  error_count      <- count of `tool/result` with data.message.isError == true
  commit_hashes    <- regex `[branch <sha>]` in non-error tool results
                       (first-seen order, cap 20)
  n_writes         <- count of `write`/`edit` tool/call events that carry a
                       file_path/path argument (one per event, not per file)
  n_commits        <- len(commit_hashes)
  n_retrieval      <- count of bash calls classified as RETRIEVAL
                       (is_retrieval): the command runs src/search.py or
                       src/verify_match.py, or grep/rg/egrep, or find with
                       -name/-iname/-path (any shell segment). Such commands
                       stay in familiar_terms (lossless) but are excluded
                       from bash_commands: searching is not evidence.
  command_classes  <- one class per bash_commands entry: 'error' /
                       'commit' / 'write' / 'read' (the same classes used
                       for the salience ordering). Feeds local salience in
                       search.py.
  file_classes     <- one class per files_touched entry: 'write'
                       (write/edit call) or 'read' (read-only). Feeds local
                       salience in search.py.
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
# Test runs (outline fix 2): bash commands of this class carry a pass/fail
# outcome line and are landmarks of class "test" (priority error > commit
# > test > write). "pytest" also covers "python -m pytest".
TEST_MARKERS = ("cargo test", "pytest", "python -m pytest",
                "python -m unittest", "npm test", "npm run test",
                "go test", "make test")
# Source monitoring: retrieval commands run a search over the logs or the
# filesystem. They are remembered (familiar_terms) but are never evidence.
RETRIEVAL_PROGS = ("grep", "egrep", "rg")
RETRIEVAL_SCRIPTS = ("src/search.py", "src/verify_match.py")
FIND_NAME_FLAGS = ("-name", "-iname", "-path")
# Tool calls that search the filesystem: their path args are retrieval
# acts, like a shell grep (remembered, never evidence).
SEARCH_TOOLS = ("grep", "glob")
# Events that define the card's start/end: real activity only. Bookkeeping
# events (session end-seed, step/end, turn/end, workspace/changes, ...) are
# written at session close and would stretch the card's end days past the
# last real activity (phase 4 fix, step 1).
ACTIVITY_EVENTS = ("user/message", "assistant/message", "tool/call", "tool/result")
# Harness text: user/message events generated by the harness, not typed by
# the user. Their text starts with one of these prefixes after stripping
# leading whitespace. They never become [user] lines (library_server
# outline / at="end"), never fill first_message (extract), and are not
# indexed as user text (build_index cards_fts). This is the single list;
# every module imports it (docs/decisions.md 2026-11-07).
HARNESS_PREFIXES = (
    "<system-reminder>",
    "Time sampled while preparing turn",
    "Current runtime context. This snapshot",
    "This is an automatically generated check",
)


def starts_with_harness_prefix(text):
    """True when text (after stripping leading whitespace) starts with one
    of HARNESS_PREFIXES."""
    return (text or "").lstrip().startswith(HARNESS_PREFIXES)

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
    "familiar_classes": "observed",
    "tool_call_count": "observed",
    "error_count": "observed",
    "n_writes": "observed",
    "n_commits": "observed",
    "n_new_files": "observed",
    "n_retrieval": "observed",
    "command_classes": "observed",
    "file_classes": "observed",
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


def visible_text(message):
    """The assistant's VISIBLE text: only content blocks of type 'text'.

    Excludes 'reasoning' blocks (chain of thought, not visible to the user)
    and 'tool-call' blocks. Returns '' when the message has no visible text.
    """
    c = message.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(b.get("text", "") for b in c
                        if isinstance(b, dict) and b.get("type") == "text")
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


def is_retrieval(cmd):
    """Source monitoring: True if any shell segment of `cmd` RUNS a
    retrieval command — src/search.py, src/verify_match.py, grep, rg,
    egrep, or find with -name/-iname/-path. Searching is not evidence, so
    such commands stay in familiar_terms (lossless) but are excluded from
    bash_commands. Segments are split on `;` `|` `||` `&&`; leading
    VAR=value assignments are skipped before the program is read."""
    for seg in re.split(r"&&|\|\||[;|]", cmd):
        words = seg.split()
        i = 0
        while i < len(words) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[i]):
            i += 1
        if i >= len(words):
            continue
        prog = os.path.basename(words[i])
        if prog in RETRIEVAL_PROGS:
            return True
        if prog == "find" and any(f in words[i + 1:] for f in FIND_NAME_FLAGS):
            return True
        if prog.startswith("python") and any(s in seg for s in RETRIEVAL_SCRIPTS):
            return True
    return False


CLASS_NAMES = ("error", "commit", "write", "read")


def extract_card(path, lines=None):
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
        "familiar_classes": [],
        "tool_call_count": 0,
        "error_count": 0,
        "commit_hashes": [],
    }
    written = OrderedDict()   # path -> first-seen order (write/edit calls)
    readonly = OrderedDict()  # path -> first-seen order (everything else)
    familiar = OrderedDict()  # lossless terms (paths + bash commands)
    arg_cls = OrderedDict()   # path-like term -> class of the tool/call
                               # that produced it (fix pass 2)
    bash_calls = []           # (full_command, callId) in chronological order
    call_errors = {}          # callId -> True if any result isError
    commits = OrderedDict()
    paths_seen = set()        # every file path in this session (n_new_files)
    n_writes = 0              # write/edit tool calls carrying a path
    n_retrieval = 0           # bash calls that only search (never evidence)
    min_t = max_t = None
    bad_lines = 0
    if lines is None:
        lines = iter_lines(path)
    for line in lines:
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
        # start/end come from activity events only; bookkeeping events
        # (session end-seed, step/end, turn/end, workspace/changes, ...)
        # are written at session close and must not stretch the card
        if et in ACTIVITY_EVENTS:
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
                text = text_of(data.get("content"))
                # harness text (HARNESS_PREFIXES) is never the user: it
                # never fills first_message; the next real user message does
                if not starts_with_harness_prefix(text):
                    card["first_message"] = text[:300]
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
                    # grep/glob search the filesystem: their path arg is a
                    # retrieval act (never evidence); other tools just read
                    arg_cls[v] = "retrieval" if name in SEARCH_TOOLS else "read"
            if name == "bash":
                cmd = args.get("command", "")
                if isinstance(cmd, str):
                    bash_calls.append((cmd, call_id))
                    if is_retrieval(cmd):
                        n_retrieval += 1
                    # familiar_terms is lossless: retrieval stays here
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
    files_display = (list(written) + list(readonly))[:MAX_FILES]
    card["files_touched"] = files_display
    card["file_classes"] = ["write" if f in written else "read" for f in files_display]
    # display bash commands: salience-ordered (stable sort keeps chronology inside
    # each priority), deterministic; RETRIEVAL commands never appear (searching
    # is not evidence — they stay in familiar_terms)
    order = sorted(
        (i for i in range(len(bash_calls)) if not is_retrieval(bash_calls[i][0])),
        key=lambda i: salience_prio(bash_calls[i][0], bash_calls[i][1], call_errors),
    )
    chosen = order[:MAX_COMMANDS]
    card["bash_commands"] = [bash_calls[i][0][:120] for i in chosen]
    card["command_classes"] = [
        CLASS_NAMES[salience_prio(bash_calls[i][0], bash_calls[i][1], call_errors)]
        for i in chosen
    ]
    # fix pass 2: one class per familiar term (same order as familiar_terms):
    # file paths take the file class (written vs everything else), bash
    # commands take the command class ('retrieval' or the salience class),
    # and path args of other tools take the class recorded at insertion.
    cmd_by_term = {}
    for full, cid in bash_calls:
        t = full[:FAMILIAR_CMD_CHARS]
        if t not in cmd_by_term:
            cmd_by_term[t] = (full, cid)
    familiar_classes = []
    for term in familiar:
        if term in written:
            familiar_classes.append("write")
        elif term in readonly:
            familiar_classes.append("read")
        elif term in cmd_by_term:
            full, cid = cmd_by_term[term]
            familiar_classes.append(
                "retrieval" if is_retrieval(full)
                else CLASS_NAMES[salience_prio(full, cid, call_errors)]
            )
        else:
            familiar_classes.append(arg_cls.get(term, "read"))
    card["familiar_terms"] = list(familiar)
    card["familiar_classes"] = familiar_classes
    card["commit_hashes"] = list(commits)
    card["n_writes"] = n_writes
    card["n_commits"] = len(commits)
    card["n_retrieval"] = n_retrieval
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
