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

## 2026-11-07 — Resident process, disk db, no pre-cut excerpts (yet)
The server opens the db once from disk and reopens it when the file changes; no in-memory copy. Episodes are decompressed on open, with an LRU cache. Why: Phase 3 measured disk 0.8 ms vs RAM 0.7 ms median and 10-31 ms to decompress the largest sessions; process start-up (32 ms) was the only real cost.

## 2026-11-07 — Excerpt = anchor + forward context (temporal contiguity)
library_open returns the matched event with the highest salience as anchor, one event before it and the events after it, up to a measured token cap. Every block carries its source tag. Why: recalling an event cues events close in time, more strongly forward (temporal contiguity, Kahana 1996); the cost of opening is tokens, not milliseconds. Peak-end was considered and rejected: it describes hedonic evaluation, not retrieval, and does not hold for day-long experiences.

## 2026-11-07 — MCP over stdio, launched by dsh
One @deepseek-ai/dsh-mcp-client entry (serverName biblioteca, transport stdio) in the desktop profile; dsh starts and stops the process. The manifiestate launchers are not changed. Stdlib only. The server answers unknown methods with -32601 so clients that probe server/discover fall back to initialize at once; it reads no env vars because the client strips DSH_*.

## 2026-11-07 — The end of an episode is reachable directly
library_open accepts at="end" (window ending at the last event) and from_seq (continue forward from a cut). Why: in the first live use the agent asked what was left pending and needed 3 opens without reaching the session's end. The hippocampus responds most at event boundaries, which appear to wrap up the preceding event as a unit (Ben-Yakov & Henson 2018); recall stores page through history instead of re-anchoring (MemGPT/Letta conversation_search).

## 2026-11-07 — Coarse to fine: outline first
library_open with no arguments returns the episode's outline (landmark events with seq, ~300 tokens); detail is reached by jumping with from_seq or at="end". Cut windows state how many events and tokens remain. Card times come from activity events only.
Why: in Run 2 the agent paged a whole session linearly (22 calls, 17,347 tokens, 5.7x Run 1). The hippocampus holds memories at two scales, coarse gist (anterior) and fine detail (posterior) (Poppenk et al. 2013; Brunec et al. 2018); event structure carries an episode's essence; agent-tool guidance recommends concise defaults and stating the default policy in the description.

## 2026-11-07 — Budget per opened session; outline carries outcomes
Linear paging without a query stops after ~4000 tokens served from one session in 30 minutes; targeted (query) opens remain allowed. Outline lines show each landmark's outcome. at="end" respects the cap.
Why: Run 3 used outline and at=end, then still walked the session linearly (16 calls, 15,658 tokens); the tool description alone did not stop it. Agent-tool guidance recommends default limits and truncation messages that steer to narrower requests; an event's central details (here, a command's outcome) carry its storyline. The agent also misreported its own calls (said 6 opens, log shows 15): runs are measured from logs only.

## 2026-11-07 — Outline: user turns first, harness text is not the user, tests are landmarks
Outline priority: the last 3 user messages, then the first one, then tool landmarks by class (error > commit > test > write), in session order, until the cap. Messages starting with "<system-reminder>" are harness text, never shown as [user]. A test run (cargo test, pytest, npm test, go test, make test, unittest) is a landmark of class test, with its pass/fail line as outcome.
Why: the outline of c5f86181 dropped the last 4 user turns (the end state), showed a system-reminder as [user], and omitted "6 passed". The hippocampus is sensitive to prediction error; a test run is a direct check of expectation against reality.

## 2026-11-07 — Phase 4 closed; budget is per server process (known limitation)
Phase 4 done: the agent used both tools in real sessions and the logs show their cost (reports/phase-4-live-use.md). Runs 5-6 on current code: 8 calls / 5,232 tokens and 5 calls / 2,685 tokens, both reaching the end state, vs Run 3: 16 calls / 15,658 tokens.
Known limitation: dsh runs one server for all sessions and strips DSH_* env vars, so the per-session budget is keyed by the opened session id, not by the agent session. Run 6 inherited Run 5's spent budget. Accepted for now: in real use two sessions rarely open the same past session within 30 minutes. Single runs vary a lot (Run 3 vs Run 4 on the same code: -35%); compare designs over several runs.
