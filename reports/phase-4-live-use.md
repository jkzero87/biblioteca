# Phase 4 — live use of the library tools

Measured from session logs (read-only). 155 session logs scanned (all `~/.local/share/deepseek-harness/sessions/**/session.v4.jsonl.zstd`), this session excluded.

**Selected session** (the only other session with `mcp__biblioteca__*` tool calls):

- id: `session-0f8b8396-dfb6-423c-a078-b0a8ff7ab1c2`
- cwd: `--home-jkzero-biblioteca--`
- ended: 2026-11-07T17:51:37 (local, America/Bogota)
- context: the agent was asked to work with the library tools and used them to do a read-only reconnaissance of an earlier imgproxy session (`session-c5f86181-d002-4a54-b7a1-7d647da9c2e4`) instead of re-reading `~/bin/imgproxy.py` and its log.

## Run 1 (anchor mode; before at=end / from_seq)

| seq | tool | args | result chars | result tokens | truncated |
|----:|------|------|-------------:|--------------:|:---------:|
| 19 | `library_search` | query=`imgproxy Rust rewrite rust-lab` | 195 | 120 | no |
| 24 | `library_open` | id=`session-c5f86181-…`, query=`imgproxy Rust rewrite progress pending` | 3088 | 1095 | yes |
| 29 | `library_open` | id=`session-c5f86181-…`, query=`next steps pending TODO imgproxy.py` | 2151 | 854 | yes |
| 34 | `library_open` | id=`session-c5f86181-…`, query=`conclusion summary final` | 3108 | 959 | yes |

**Totals (Run 1):** 4 calls · 8542 result chars · 3028 result tokens (each result text POSTed to `127.0.0.1:8092/tokenize`).

## Run 2 (after at=end + from_seq)

Same script (`src/phase4_live_use.py`), this session excluded. 3 sessions now have `mcp__biblioteca__*` calls; the most recent selected:

- id: `session-4780d63d-a595-4a65-a683-743dec5d16fb`
- ended: 2026-11-07T18:44:19 (local, America/Bogota)
- context: the agent walked `session-c5f86181-…` end-to-end — one initial open (no query), a `from_seq` chain 105 → 137 → … → 398, two past-end attempts, a `from_seq=1` / `from_seq=24` re-walk, and three searches.

| seq | tool | args | result chars | result tokens | truncated |
|----:|------|------|-------------:|--------------:|:---------:|
| 19 | `library_search` | query=`imgproxy Rust rewrite` project=`rust-lab` | 300 | 143 | no |
| 24 | `library_open` | id=`session-c5f86181-…` (no query, no at) | 3279 | 1176 | no |
| 29 | `library_open` | id=…, from_seq=105 | 2847 | 891 | no |
| 34 | `library_open` | id=…, from_seq=137 | 2517 | 752 | no |
| 39 | `library_open` | id=…, from_seq=149 | 3035 | 1180 | no |
| 44 | `library_open` | id=…, from_seq=163 | 3096 | 1169 | no |
| 49 | `library_open` | id=…, from_seq=184 | 3279 | 1269 | no |
| 54 | `library_open` | id=…, from_seq=220 | 3320 | 1130 | no |
| 59 | `library_open` | id=…, from_seq=249 | 3173 | 1087 | no |
| 64 | `library_open` | id=…, from_seq=276 | 3261 | 1054 | no |
| 69 | `library_open` | id=…, from_seq=300 | 2259 | 763 | no |
| 74 | `library_open` | id=…, from_seq=322 | 2816 | 911 | no |
| 79 | `library_open` | id=…, from_seq=341 | 2800 | 1048 | no |
| 84 | `library_open` | id=…, from_seq=358 | 2856 | 1052 | no |
| 89 | `library_open` | id=…, from_seq=378 | 3041 | 1048 | no |
| 94 | `library_open` | id=…, from_seq=398 | 567 | 228 | no |
| 99 | `library_open` | id=…, from_seq=405 → error result (isError) | 79 | 46 | no |
| 104 | `library_open` | id=…, from_seq=399 → error result (isError) | 79 | 46 | no |
| 109 | `library_search` | query=`rust imgproxy workspace convert.rs axum` | 54 | 23 | no |
| 114 | `library_search` | query=`rust-lab` | 316 | 160 | no |
| 119 | `library_open` | id=…, from_seq=1 | 3082 | 970 | no |
| 124 | `library_open` | id=…, from_seq=24 | 3290 | 1201 | no |

**Totals (Run 2):** 22 calls · 49346 result chars · 17347 result tokens.

**Reading (Run 2):** `from_seq` chaining now reaches the tail of the session — Run 1's failure mode (re-anchoring near the start, never reaching the end) is gone: 15 forward opens walked seq 8 → 398, and `from_seq=405` / `from_seq=399` correctly errored past the last event. No result carried a `[truncated at` marker: cut `from_seq` windows end with a `next: from_seq=<n>` trailer instead. But the walk stopped at seq 398 (2026-10-23 ~18:22 local), while the card says the session ended 2026-10-25T21:45:51 — see the end-state gap below.

**Agent's own verdict from that session** (stated by the user; the session's visible text contains the same assessment — it describes its own run as a read-only reconnaissance of the Python imgproxy work):

> library saved re-reading imgproxy.py and the log, but needed 3 opens and never reached the session's end state

**Reading:** the library paid off (one search + three opens ≈ 3028 tokens instead of re-reading `imgproxy.py` plus the session log), but the anchor-based open re-anchored near the start of the session on every call: three opens, all truncated by `OPEN_CHAR_CAP`, and the end of the session was never reached. That is what the step-3 additions fix: `at="end"` (window ending at the last event) and `from_seq` (continue forward from a cut, via the exact `next: from_seq=<n>` trailer).

## End-state gap: card end vs. library tail (session c5f86181)

Diagnosed with `src/phase4_endgap.py` (aggregates only; log read-only; no raw lines).

**Cause.** The card's `end` is the max of `time` over **all** events (extract.py: "start / end ← min / max of `time` (ms epoch) over all events"), and that maximum belongs to seq 402 `session/end-seed`, a bookkeeping event written 2026-10-25T16:45:51-05:00 (= 21:45:51 UTC — the card's end), two days after the last real activity. `library_open` renders only what `parse_events` keeps, and `parse_events` (`src/library_server.py:317-368`) handles exactly four event types in an if/elif chain: `user/message` (L333), `assistant/message` with non-empty visible text (L336-343; `if text.strip()` at L342), `tool/call` (L344), `tool/result` (L359). Any other type falls through silently (no `else` branch). The four events after seq 398 — seq 399 `step/end`, 400 `workspace/changes`, 401 `turn/end`, 402 `session/end-seed` — are all of that kind, so the last renderable event is seq 398 (`assistant/message`, 2026-10-23T18:22:40-05:00), and `at="end"` / `from_seq` stop there.

**Evidence (aggregates only).**
- Raw log: 404 events; seq 0..402, all unique, strictly increasing in file order (0 adjacent decreases), no time inversion → not a resumed/restarted log.
- Time span: 2026-10-23T15:39:23-05:00 .. 2026-10-25T16:45:51-05:00.
- Events with time after seq 398: exactly 4 (seq 399..402): `step/end`, `workspace/changes`, `turn/end`, `session/end-seed`.
- Of the 52 events after 2026-10-23T18:20-05:00, `parse_events` would keep 17 and skip 35: 7 of the skips are assistant messages without visible text (L342), the rest are bookkeeping types with no branch in L333-359.
- Whole file: `parse_events` keeps 135 of 404; kept seq span 8..398; kept time span 2026-10-23T15:39:51..18:22:40-05:00.
- Card row (`data/library.db`): source = this `.zstd` file; start=2026-10-23T20:39:23Z, end=2026-10-25T21:45:51Z (= 16:45:51-05:00) — exactly the `session/end-seed` time.

**Discrepancy note.** All 23 cargo-related tool calls in this log are dated 2026-10-23 (15:40–18:21 local); the only event dated 2026-10-25 is the end-seed. Rendered blocks carry no event times (`render_event`, `src/library_server.py:389-401`), so a "10-25" date seen in Run 1's output would have come from event *text* content, not from an event timestamp.

Not fixed (per task).
