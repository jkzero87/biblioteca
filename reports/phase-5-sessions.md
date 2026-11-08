# Phase 5 — unprompted library use
Rule fixed in advance: over 5 real sessions that resume earlier work, without asking the agent to use the library: searched in 4-5 → the instruction line is enough; 3 → extend to 10 sessions; 0-2 → add a mechanical trigger. Also: median library tokens per session ≤ 4000. Measured from logs only.

| # | date | task | line present | searched unprompted | library calls | library tokens | direct reads before first write | outcome |
|---|------|------|--------------|---------------------|---------------|----------------|--------------------------------|---------|
| 1 | 2026-11-08 | imgproxy Rust proxy on 8093 | yes (user/message seq 10, agent-instructions) | yes (search was first tool call) | 3 | 1503 | 8 calls (imgproxy.py×1, rust-lab×4, session logs×0) | done: build ok, 9 tests pass, 1 request ok, commit 0fe7e33 |

Notes (measured from logs only):
- Target: `session-f05fe96d-1436-4751-81ac-e9fa7515eb2c` (workspace ~/rust-lab). Its turn started 2026-11-08 11:04:54 −05:00, after the library server start 2026-11-08 11:02:55 −05:00; session UUID itself was created 2026-10-25 and sat idle until the resume.
- A newer rust-lab session exists after the cutoff: `session-36c3a5d9-61a0-4b4f-ada0-ff6a7edcfa38`, created 2026-11-08 11:15:22 −05:00, but it contains only 4 setup events (no user message, no turn) — empty, not a real resuming session.
- Calls: seq 20 library_search "imgproxy Rust rewrite" (321 chars / 153 tokens, result truncated by server, no budget msg); seq 37 library_open id=c5f86181 outline (731 / 293); seq 76 library_open id=c5f86181 query="cargo build CARGO_HOME build command" (3238 / 1057). Totals 4290 chars / 1503 tokens; no budget message on any call; at=/from_seq= never used.
- User task message (seq 9) does not mention the library → search was unprompted.
- Direct reads: 8 tool calls before first write (seq 59, write imgproxy/src/main.rs): seq 20 library_search, 22 bash ls+git log, 26 bash cat ~/bin/imgproxy.py, 33 bash cat Cargo.toml, 37 library_open, 42 bash cat imgproxy/Cargo.toml, 49 todo_write (text mention of rust-lab, not a file touch), 52 bash grep Cargo.lock.
