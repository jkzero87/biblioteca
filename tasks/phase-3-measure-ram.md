# Phase 3: measure before going resident (RAM)

**Runs on:** local agent (dsh desktop + 27B).
**Sources:** `docs/plan.md` Phase 3; `docs/design.md` §4.1.
**Order:** starts when Phases 1, 2, and 2b are done.

## Goal

Measure where retrieval time goes, so the resident server in Phase 4 is shaped by numbers.

## Hard rules

- Session logs are read-only. Never write to, move, or delete anything under `~/.local/share/deepseek-harness/sessions`.
- No model, no GPU, no network. The benchmarks must not call any model server.
- No sudo. Skip the cold-file-cache mode if it is not possible without sudo, and say so. Do not stop or restart other processes to get cleaner numbers.
- Benchmark output and excerpts stay in `data/`, which must never be committed.
- Report measured numbers only. Do not adjust the prediction after measuring.

## Steps

1. Write down this prediction, unchanged, before measuring: per-process start-up and decompression dominate; disk vs RAM is nearly equal.
2. Measure sizes: db, cards, all logs, largest log (compressed and decompressed), free RAM, and swap.
3. Pick 20 real queries, taken from real topics rather than made up to suit the index. List them.
4. Measure the latency of those 20 queries in each mode:
   - new process per query
   - one process + disk db
   - one process + in-memory db
   - cold file cache (only if possible without sudo)
5. Measure episode cost for the 5 largest sessions: full zstd decompress vs a pre-cut plain excerpt. State how each excerpt was cut.
6. Record whether llama-server or a session was busy during the runs (`pgrep -a llama-server`), since this affects the numbers.

## Report exactly

1. The prediction, as written before measuring.
2. Sizes from step 2.
3. One table: mode, median, p95. Add a row for the mode if it was skipped, marked "skipped" with the reason.
4. Episode cost for the 5 largest sessions: session, compressed and decompressed size, decompress time, excerpt time, excerpt size.
5. A bottleneck statement backed only by those numbers, saying whether the prediction held.
6. The benchmark script path and the command to re-run it.
