# Phase 4 — live use of the library tools

Measured from session logs (read-only). 155 session logs scanned (all `~/.local/share/deepseek-harness/sessions/**/session.v4.jsonl.zstd`), this session excluded.

**Selected session** (the only other session with `mcp__biblioteca__*` tool calls):

- id: `session-0f8b8396-dfb6-423c-a078-b0a8ff7ab1c2`
- cwd: `--home-jkzero-biblioteca--`
- ended: 2026-11-07T17:51:37 (local, America/Bogota)
- context: the agent was asked to work with the library tools and used them to do a read-only reconnaissance of an earlier imgproxy session (`session-c5f86181-d002-4a54-b7a1-7d647da9c2e4`) instead of re-reading `~/bin/imgproxy.py` and its log.

| seq | tool | args | result chars | result tokens | truncated |
|----:|------|------|-------------:|--------------:|:---------:|
| 19 | `library_search` | query=`imgproxy Rust rewrite rust-lab` | 195 | 120 | no |
| 24 | `library_open` | id=`session-c5f86181-…`, query=`imgproxy Rust rewrite progress pending` | 3088 | 1095 | yes |
| 29 | `library_open` | id=`session-c5f86181-…`, query=`next steps pending TODO imgproxy.py` | 2151 | 854 | yes |
| 34 | `library_open` | id=`session-c5f86181-…`, query=`conclusion summary final` | 3108 | 959 | yes |

**Totals:** 4 calls · 8542 result chars · 3028 result tokens (each result text POSTed to `127.0.0.1:8092/tokenize`).

**Agent's own verdict from that session** (stated by the user; the session's visible text contains the same assessment — it describes its own run as a read-only reconnaissance of the Python imgproxy work):

> library saved re-reading imgproxy.py and the log, but needed 3 opens and never reached the session's end state

**Reading:** the library paid off (one search + three opens ≈ 3028 tokens instead of re-reading `imgproxy.py` plus the session log), but the anchor-based open re-anchored near the start of the session on every call: three opens, all truncated by `OPEN_CHAR_CAP`, and the end of the session was never reached. That is what the step-3 additions fix: `at="end"` (window ending at the last event) and `from_seq` (continue forward from a cut, via the exact `next: from_seq=<n>` trailer).
