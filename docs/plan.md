# Plan

Date: 2026-11-06. Each phase ends with a measurable done-criterion. A phase starts only when the previous one is done, unless marked parallel.

Who runs what:
- **Local agent** (dsh desktop + 27B): all CPU-only work — parsing, indexing, benchmarks without the model.
- **Claude Code**: anything that needs the GPU or occupies the model server for a long time (sleep job, embeddings, model benchmarks).

Testing rule: every phase is validated on **real sessions and real pending work**, not synthetic test sets.

---

## Phase 0 — Base (this commit)
- Design, plan, decisions and sources written.
- Repo created, `data/` git-ignored.
- **Done when:** repo exists with these docs and an empty `src/`.

## Phase 1 — Know what is already there (parallel with Phase 2)
Diagnostic, read-only.
- Locate the desktop dsh profile and list its plugins.
- dsh-recall: version, active config, whether auto-recall injection is ON.
- In the 3 most recent real sessions: count events and characters injected by dsh-recall, as % of session size.
- Check, without installing, whether dsh-auto-memory supports the installed dsh version.
- **Done when:** a table per session (recall events, recall chars, session chars, %) with file/command evidence for every claim.
- **Decision it unlocks:** keep dsh-recall as is, tune it, or turn off its auto-recall before biblioteca sits on top.

## Phase 2 — Mechanical episode cards (Index + Barcode)
No model, no GPU, no network. Session logs read-only.
- Inspect real session logs first; record the actual event types and field names.
- `extract`: one card per session with mechanical facts only — id, source path, start/end time, cwd, first user message (≤300 chars), files touched, bash commands (≤30, ≤120 chars each), tool-call count, error count, commit hashes.
- `build_index`: SQLite FTS5 over first message, files and commands; idempotent rebuild.
- `search`: one line per result, with output size.
- **Done when:** searches for three real topics return matches that were checked by opening the source logs; sessions processed, cards, db size and search time reported; parse failures listed.

## Phase 2b — Bio-inspired fields (Source + Forget + Schema)
- Per-field source map: `observed` / `user`. Nothing tagged `model` yet.
- Mechanical salience: n_errors, n_writes, n_commits, n_new_files.
- Project schema: assign each card to the git repo whose path best matches its cwd/files, else `orphan`.
- `last_accessed`, `access_count`; ranking = bm25 × recency (0.98^days) × salience (1 + 0.2 × min(sum, 10)), all factors printed.
- `--project` search.
- **Done when:** old vs new ranking shown side by side for the same queries; orphan count and 5 orphan paths reported.

## Phase 3 — Measure before going resident (RAM)
- Sizes: db, cards, all logs, largest log (compressed/decompressed); free RAM, swap.
- Latency of 20 real queries in four modes: new process per query; one process + disk db; one process + in-memory db; cold file cache (only if possible without sudo).
- Episode cost: full zstd decompress vs pre-cut plain excerpt, for the 5 largest sessions.
- **Prediction written in advance:** per-process start-up and decompression dominate; disk vs RAM is nearly equal.
- **Done when:** one table (mode, median, p95) and a bottleneck statement backed only by those numbers.

## Phase 4 — Resident library server (MCP)
Shape decided by Phase 3 numbers.
- Python MCP server: `library_search(query, project?)` (≤5 lines, ~300-token cap) and `library_open(id)` (one excerpt).
- Index held in RAM; LRU cache of opened excerpts; atomic reload when the db file changes.
- Started by the existing launch command; registered in the dsh desktop profile.
- **Done when:** the agent calls both tools in a real session, and the session log shows the tokens they added.

## Phase 5 — Getting the agent to use it
- Add the fixed cue line to the system prompt (~40 tokens).
- Use the library during normal work for a fixed period.
- Log every search: did it change the plan (avoided re-investigation / reused a decision)? Tokens added.
- Only if search rate is low: add the mechanical trigger (hint, not content).
- **Done when:** search rate, share that changed the plan, tokens added per session, and wrong recalls are reported from real sessions.

## Phase 6 — Sleep job (knowledge layer) — Claude Code
Runs when the GPU is free, never during work.
- Pre-cut excerpts for new sessions.
- Distil lessons across episodes with the 27B; every lesson tagged `model` and linked to its source episodes.
- Mark lessons contradicted by later evidence (reconsolidation).
- **Done when:** lessons are searchable, each shows its source episodes, and none is tagged `observed`.

## Later (only if Phase 5 shows the library is used)
- Mechanical contradiction checks at open time (path exists? commit reachable?).
- Embeddings / HippoRAG-style graph, if exact-word search misses things in practice.
- Write approval for anything the agent wants to add by itself.

---

## Metrics tracked across phases
| Metric | From phase |
|---|---|
| Search latency (median, p95) | 2 |
| Tokens added to context per session | 4 |
| Searches per session | 5 |
| Searches that changed the plan | 5 |
| Wrong / misleading recalls | 5 |
