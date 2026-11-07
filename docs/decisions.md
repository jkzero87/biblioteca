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
