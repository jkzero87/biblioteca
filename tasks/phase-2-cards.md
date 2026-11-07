# Phase 2: mechanical episode cards (Index + Barcode)

**Runs on:** local agent (dsh desktop + 27B).
**Sources:** `docs/plan.md` Phase 2; `docs/design.md` §3.1, §3.2, §4.1.
**Order:** may run in parallel with Phase 1.

## Goal

Build one card per session containing only mechanical facts from the log, plus a SQLite FTS5 index and a command-line search over those cards.

## Hard rules

- Session logs are read-only. Decompress to stdout or memory only. Never write to, move, or delete anything under `~/.local/share/deepseek-harness/sessions`.
- No model, no GPU, no network. The code must not call any model server. Do not install packages; if the standard library and the `zstd` CLI are not enough, stop and ask.
- Cards hold facts read from logs. Do not summarise, and do not copy the model's prose into a card.
- One card per session, each with its own id. Never merge or deduplicate episodes, even when they touch the same files.
- Code goes in `src/`. Generated output goes in `data/`, which must never be committed.

## Steps

1. Inspect real session logs before writing a parser (`sessions/<workspace>/session-<uuid>/session.v4.jsonl.zstd`). Record the actual event types and field names, with the sessions you sampled. Do not assume a format.
2. Write `extract`, producing one card per session with these fields:
   - id
   - source path
   - start and end time
   - cwd
   - first user message (≤300 chars)
   - files touched
   - bash commands (≤30 commands, ≤120 chars each)
   - tool-call count
   - error count
   - commit hashes
3. Do not let one bad log stop the run. List every parse failure with its path and reason. A log still being written may fail or end early.
4. Write `build_index`: SQLite FTS5 over first message, files, and commands. Rebuilding must be idempotent: the same logs give the same cards and the same search results.
5. Write `search`: one line per result, and print the output size.
6. Run all three over every real session.
7. Choose three real topics from pending work or past sessions. Search for each, then open the source logs of the matches to check that they really are about that topic.

## Report exactly

1. Event types and field names found, and which ones each card field comes from.
2. Sessions processed, cards written, db size, and search time.
3. Every parse failure, or "none".
4. For each of the three topics: the query, the result lines, the output size, and what you found when you opened the source logs (true or false match).
5. Exact commands to re-run `extract`, `build_index`, and `search`.
6. `git status` output showing that nothing from `data/` is staged or tracked.
