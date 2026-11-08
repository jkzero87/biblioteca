# Phase 5 — incremental refresh (STEP 2–4)

What it does: `refresh()` in `src/refresh.py` stats every session log, compares against the `indexed_logs` table (path, session_id, size, mtime, indexed_at), re-extracts only the changed ones cut at the last completed turn, carries `last_accessed`/`access_count` over from the old rows, and writes everything in one transaction. The server calls it at startup and before every `library_search`; a refresh failure never fails the search.

Full rebuild (STEP 2, one-off, empty `indexed_logs`): 176 logs checked, 163 cards indexed, 1564.7 ms. 13 sessions skipped (no completed turn — they are not indexed and will be retried on later refreshes). `n_new_files` is exact on this rebuild (full batch, no reference set); on later partial refreshes it is approximate, because the reference set is the `familiar_terms` of the unchanged cards, not their full path sets.

Refresh cost measured with `time.perf_counter` around `refresh.refresh(DB)`, 5 runs per case on 2026-11-08 (DB: 163 indexed sessions, 176 logs on disk):

| case | what changed | log size (zstd) | median | min | max |
|------|--------------|-----------------|--------|-----|-----|
| a — no change | nothing | — | 33.9 ms | 33.2 | 36.0 |
| b — one new session (smallest) | 1 card re-extracted, `session-2207416e-…` | 9,617 B | 118.6 ms | 116.5 | 119.2 |
| c — one new session (largest) | 1 card re-extracted, `session-6d7eea23-…` | 1,241,413 B | 219.0 ms | 191.8 | 223.7 |

Each (b)/(c) run deleted the session's `indexed_logs` row first (simulates a session that ended after the last refresh), so exactly 1 card was re-extracted per run (`updated 1 of 176` in every run). Even the 1.2 MB log refreshes in ~0.2 s, well under a search.
