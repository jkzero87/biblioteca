#!/usr/bin/env python3
"""Phase 5, session 1: instruction-phrase check + direct reads before first write.

For the given rust-lab session log (read-only via zstd -dc):
  A) Find the exact phrase "You have a library of your past work sessions"
     in any event; report yes/no and the event type(s) + seq where it
     appears. The phrase itself is never printed.
  B) List every tool/call event before the first write/edit of a .rs or
     Cargo.toml file: seq, tool, and only the file path or the command's
     first 60 chars. Then count how many of those touched
     ~/bin/imgproxy.py, ~/rust-lab files, or session logs.
No raw log lines are printed.
"""
import json
import os
import subprocess

SID = "session-f05fe96d-1436-4751-81ac-e9fa7515eb2c"
PATH = os.path.expanduser(
    "~/.local/share/deepseek-harness/sessions/--home-jkzero-rust-lab--/"
    + SID + "/session.v4.jsonl.zstd")
PHRASE = "You have a library of your past work sessions"
IMPPROXY = "/home/jkzero/bin/imgproxy.py"
RUSTLAB = "/home/jkzero/rust-lab"
SESSLOGS = "/home/jkzero/.local/share/deepseek-harness/sessions"


def load(path):
    out = subprocess.run(["zstd", "-dc", path], stdout=subprocess.PIPE).stdout
    evs = []
    for line in out.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except Exception:
            continue
        if isinstance(e, dict):
            evs.append(e)
    return evs


def args_of(e):
    d = e.get("data") or {}
    a = d.get("arguments")
    if isinstance(a, str):
        try:
            a = json.loads(a)
        except Exception:
            a = {"_raw": a[:60]}
    return a if isinstance(a, dict) else {"_raw": str(a)[:60]}


def file_of(args):
    for k in ("file_path", "path", "filePath"):
        v = args.get(k)
        if isinstance(v, str):
            return os.path.expanduser(v)
    return None


def main():
    evs = load(PATH)
    tools = [e for e in evs if e.get("type") == "tool/call"]

    # A) phrase check (whole-event string search)
    where = []
    for e in evs:
        s = json.dumps(e, ensure_ascii=False)
        if PHRASE in s:
            where.append((e.get("seq"), e.get("type")))
    print("A) phrase present:", bool(where))
    for seq, t in where:
        print("   in event type %r at seq=%s" % (t, seq))

    # B) first write/edit of .rs / Cargo.toml
    boundary = None
    for e in tools:
        d = e.get("data") or {}
        if d.get("name") in ("write", "edit"):
            f = file_of(args_of(e))
            if f and (f.endswith(".rs") or os.path.basename(f) == "Cargo.toml"):
                boundary = e
                break
    if boundary is None:
        print("B) no write/edit of .rs/Cargo.toml found; listing all %d tool calls" % len(tools))
        boundary = None
    else:
        bseq = boundary.get("seq")
        bf = file_of(args_of(boundary))
        print("B) first write/edit of .rs/Cargo.toml: seq=%s tool=%s file=%s"
              % (bseq, (boundary.get("data") or {}).get("name"), bf))
        before = [e for e in tools if isinstance(e.get("seq"), int) and e.get("seq") < bseq]
    print("tool calls before boundary: %d" % len(before))
    n_imgproxy = n_rustlab = n_logs = 0
    for e in before:
        d = e.get("data") or {}
        name = d.get("name")
        a = args_of(e)
        f = file_of(a)
        if f:
            target = f
        else:
            cmd = a.get("command") or a.get("query") or a.get("_raw") or ""
            target = str(cmd)[:60]
        hay = (os.path.expanduser(f) if f else "") + " " + json.dumps(a, ensure_ascii=False)
        touched = []
        if "imgproxy.py" in hay:
            n_imgproxy += 1
            touched.append("imgproxy.py")
        if "rust-lab" in hay:
            n_rustlab += 1
            touched.append("rust-lab")
        if "deepseek-harness/sessions" in hay:
            n_logs += 1
            touched.append("session-log")
        print("  seq=%s %-22s %s%s"
              % (e.get("seq"), str(name), target,
                 ("  [" + ",".join(touched) + "]") if touched else ""))
    print("counts touching: imgproxy.py=%d  ~/rust-lab=%d  session logs=%d"
          % (n_imgproxy, n_rustlab, n_logs))


if __name__ == "__main__":
    main()
