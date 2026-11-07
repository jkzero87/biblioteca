# Phase 5: getting the agent to use it

**Runs on:** local agent (dsh desktop + 27B), during the user's normal work.
**Sources:** `docs/plan.md` Phase 5; `docs/design.md` §4.3, §6, §7.
**Order:** starts when Phase 4 is done.

## Goal

Find out from real sessions whether the agent searches the library and whether those searches change what it does.

## Hard rules

- Session logs are read-only. Never write to, move, or delete anything under `~/.local/share/deepseek-harness/sessions`.
- Judge results on real work only. No synthetic test sessions.
- The cue line is fixed. Do not reword it during the period, because a changing prompt breaks the KV prefix cache and the measurement.
- No network, and no new model or GPU jobs. The only model use is the normal working session.
- The mechanical trigger gives a hint, never content, and is added only under step 5.
- Before editing the dsh profile, copy the file next to itself with a `.bak-pre-biblioteca-<date>` suffix. Ask the user to restart the app; do not restart it yourself.
- Logs of searches stay in `data/`, which must never be committed.

## Steps

1. Ask the user how long the fixed period is. `plan.md` does not set it, so do not choose it yourself.
2. Add this cue line to the system prompt, exactly (about 40 tokens):
   > You have a library of past work; search it when the task names a project, a file, or something that may have been done before.
3. Use the library during normal work for the whole period.
4. Log every search: session id, query, results, tokens added, whether it changed the plan (avoided re-investigation or reused a decision), with the reason, and whether a retrieved card was misleading.
5. At the end of the period, report the search rate. `plan.md` does not define "low", so ask the user whether the rate counts as low. Only on their yes, add the mechanical trigger: the harness runs a cheap search on the user message and, on a strong match, appends a hint such as "2 cards match load.py", not content. Then repeat steps 3 and 4 for a period the user sets.

## Report exactly

From real sessions, with session ids:

1. Period covered and sessions in it.
2. Search rate: searches per session.
3. Share of searches that changed the plan, each with its one-line evidence.
4. Tokens added per session by the library.
5. Wrong recalls: each case where a retrieved card was misleading, and how.
6. Search and open latency (median, p95).
7. Whether the mechanical trigger was added. If it was, the numbers above before and after it.
