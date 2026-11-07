# Decisions

Dated log. New entries go at the bottom; old entries are never rewritten — if one is reversed, add a new entry that says so.

## 2026-11-06 — Pull, not push
Memory is retrieved on demand by the agent, not injected into every prompt.
Why: context is small, every prompt token costs prefill, and injected context measurably changes the agent's tool behaviour.

## 2026-11-06 — Bio-inspired design
Architecture follows memory mechanisms from neuroscience: hippocampal indexing, episode barcodes, complementary learning systems with sleep replay, tag-and-capture salience, adaptive forgetting, schemas, source monitoring, reconsolidation. See design.md §3.

## 2026-11-06 — Mechanical first, model later
Episode cards contain only facts read from logs. The model is used only in the offline sleep job, and its output is always tagged `model`.
Why: the model confabulates and then cites its own confabulation as context.

## 2026-11-06 — No model-rated importance
Salience comes from log signals (errors, commits, writes, first-time files), never from asking the model how important something is.

## 2026-11-06 — Episodes are never merged
One card per session, unique id, no deduplication at the episode layer.

## 2026-11-06 — Never delete logs; forgetting = ranking
Unused cards sink in rank; source logs stay untouched and read-only.

## 2026-11-06 — Small index in RAM, content on disk
A resident process holds the index; excerpts and logs live on disk. Exact shape decided by Phase 3 measurements.

## 2026-11-06 — SQLite FTS5 before embeddings
Exact-word search first: no GPU, millisecond queries, single file. Embeddings only if real use shows misses.

## 2026-11-06 — MCP server in Python
Integration through MCP (dsh has an MCP client; also usable from Claude Code) rather than a Cordis plugin in TypeScript.

## 2026-11-06 — Personal data never committed
`data/` is git-ignored; the repo holds code and docs only.

## 2026-11-07 — Familiarity + recollection (two-level index)
Search has two levels: cards (recollection: curated, capped, salience-ordered fields) and a lossless per-session term list (familiarity: every file path and bash command, no cap). Results are labelled [card] or [familiar]. Card display fields are chosen by salience (errors, commits, writes first), not by order of arrival.
Why: Phase 2 search missed compatibility.json, present in the logs but past the 30-command cap. State of the art keeps a lossless layer under the curated one (Zep episode subgraph, MemGPT/Letta recall storage); biology separates familiarity (perirhinal, fast, "seen before") from recollection (hippocampus, detailed).

## 2026-11-07 — No scene segmentation for now
Sessions are not split into scenes: one card per session. Measurements (reports/phase-2-scene-measure.md, 148 sessions): median 1 user message, p90 3; 99 of 148 sessions are single-message and hold 48% of all chars; only 4 sessions have ≥7 user messages. Scene-level segmentation does not pay off yet.

## 2026-11-07 — Searching is not working (source monitoring)
Commands that search (src/search.py, verify_match.py, grep, rg, find -name) stay in the familiarity layer but are never evidence on a card. Why: Phase 2b ranked a test session first for any query it had searched for; remembering that you looked for X is not remembering X.

## 2026-11-07 — Salience is local to the match
Ranking uses salience of the matched items only (errors, commits, writes that contain the query), not of the whole session. Why: in tag-and-capture a strong event stabilises only nearby traces; session-wide salience let busy sessions outrank the right ones (standard-light: true hits fell from 1-2 to 1-4).

## 2026-11-07 — Only real retrieval counts as use
Search records access only with --touch (used by the Phase 4 server). Test runs never change ranking. Why: retrieval strengthens memory (testing effect), but test queries are not use; all accesses recorded before this date were tests and were reset.

## 2026-11-07 — Familiarity keeps its source: [familiar] vs [searched]
A familiarity hit that matches only through search commands is labelled [searched] and ranks last. Order: [card] > [familiar] > [searched]. Nothing is dropped. Why: after the previous fix, 4 of 5 compatibility.json hits were sessions that only searched for it, burying the one session that used the file.
