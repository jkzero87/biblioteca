# Phase 3 — Measuring RAM (search latency + episode cost)

## 1. Prediction (written before measuring, committed first)

> per-process start-up and decompression dominate; disk vs RAM is nearly equal

## 2. Sizes

| item | bytes |
|---|---:|
| `data/library.db` | 1,433,600 |
| `data/cards.jsonl` | 632,489 |
| all logs, compressed (153 `.zstd`) | 17,005,039 |
| all logs, decompressed | 57,991,648 |
| largest log, compressed — `session-6d7eea23…` | 1,241,413 |
| largest log, decompressed — same session | 4,433,398 |

RAM at measurement (`free -m`): total 62,637 MB, used 26,382 MB, free 5,523 MB,
buff/cache 31,801 MB, **available 36,254 MB**. The db (1.4 MB) and even the whole
decompressed log set (58 MB) fit easily; the design question is process count,
not capacity.

Swap: 8,191 MB total, **0 used during every measurement**. (User-reported:
8.0/8.0 GiB full this morning — no longer the case at measurement time.)

## 3. Queries (20)

Selection: cards ordered by `end` DESC, excluding this session
(`session-5eb4a240…`) and project `/home/jkzero/biblioteca`; per card take the
first file with class `write` → basename, else first `read`, else first token of
the first bash command; cards without a usable token are skipped; dedupe; stop
at 20. All queries are single tokens. Saved in `data/phase3_queries.json`
(+ `data/phase3_queries_detail.json`).

| # | query | source card | project | source |
|---|---|---|---|---|
| 1 | `cat` | session-13c4f945 | dsh-scratch | bash token |
| 2 | `pantalla_02.png` | session-4cb73363 | dsh-scratch | file read |
| 3 | `odin_01.png` | session-907fd2cd | dsh-scratch | file read |
| 4 | `title.keys` | session-860f5253 | dsh-scratch | file write |
| 5 | `adb` | session-df9b1e0a | dsh-scratch | bash token |
| 6 | `ESTADO_barra.md` | session-3e6521e4 | dsh-scratch | file write |
| 7 | `curl` | session-3e95b8a9 | dsh-scratch | bash token |
| 8 | `prueba-pillow13.py` | session-e3854678 | dsh-scratch | file write |
| 9 | `NEXT-linux-port.md` | session-1a7f1fda | dsh-scratch | file write |
| 10 | `NEXT.md` | session-110b800c | dsh-scratch | file write |
| 11 | `cd` | session-46e57f56 | dsh-scratch | bash token |
| 12 | `transducer.js` | session-dbc5673f | dsh-scratch | file write |
| 13 | `lockfile_guard.py` | session-907ab58b | dsh-scratch | file write |
| 14 | `compare.sh` | session-abf97961 | dsh-scratch | file write |
| 15 | `#` | session-1b5da0ca | dsh-scratch | bash token |
| 16 | `Cargo.toml` | session-c5f86181 | rust-lab | file write |
| 17 | `check_tokenizer_9b.py` | session-78f5be22 | es-reasoning-finetune | file write |
| 18 | `reglas_v2.py` | session-86987b85 | dsh-scratch | file write |
| 19 | `baseline_9b.py` | session-f390285e | es-reasoning-finetune | file write |
| 20 | `edit_launchers.py` | session-4f8439f2 | dsh-scratch | file write |

## 4. Search latency — 20 queries × 3 modes

Modes (per query, wall time):
- **A new process**: `python3 src/search.py <q>` in a fresh subprocess (no `--touch`).
- **B one process + disk db**: one process, sqlite opened `?mode=ro` on `data/library.db`.
- **C one process + in-RAM db**: one process, entire db loaded into `:memory:` via `sqlite3.deserialize`.
- **Cold file cache**: **not measured — needs root** (`echo 3 > /proc/sys/vm/drop_caches`); no sudo. All runs ran with a warm page cache.

In-process logic for B/C is byte-identical to the CLI: sanity check on query 1
gave identical output (5/5 result lines match `src/search.py`).

| mode | median (ms) | p95 (ms) | min (ms) | max (ms) | one-time cost |
|---|---:|---:|---:|---:|---|
| A new process | 38.4 | 57.6 | 32.4 | 57.6 | interpreter start + db open: median **31.7 ms**; query inside the process: median **5.5 ms** |
| B one process, disk db | 0.8 | 22.2 | 0.35 | 29.4 | db open: 0.12 ms |
| C one process, in-RAM db | 0.7 | 18.2 | 0.26 | 21.8 | loading 1,433,600 B into RAM: 1.95 ms |
| cold file cache | not measured | — | — | — | needs root |

Environment per timing run (`/proc/loadavg` 1-min + swap, start and end of each
mode, identical values): loadavg ≈ **7.96** (it was 18.92 before the run began),
swap **0/8,191 MB** throughout. The model server (llama-server, 127.0.0.1:8092)
serves this session and was busy the whole time; `pgrep -a llama-server` is
invisible from the sandbox's PID namespace, so its per-run state is recorded as
"not visible, but known to be serving this session". The p95 outliers (B: 22.2,
C: 18.2 vs median 0.8/0.7) are load spikes, not mode differences.

## 5. Episode cost — 5 largest sessions (by decompressed size)

Per session, fresh processes, read-only:
- decompression: `zstd -dc <log> > /dev/null` (timed), bytes counted in the same pass;
- excerpt: **first 64 KiB (65,536 B) of the decompressed log**, pre-cut once at
  "sleep-job" time to `data/episodes/<sid>.excerpt.txt` (plain text), then read
  in full by a fresh `python3` process (timed).

| session | compressed (B) | decompressed (B) | decompress (ms) | excerpt read (ms) |
|---|---:|---:|---:|---:|
| session-6d7eea23 | 1,241,413 | 4,433,398 | 30.7 | 18.5 |
| session-54548ded | 1,077,055 | 4,280,000 | 21.4 | 16.9 |
| session-4e8e8083 | 862,417 | 3,577,893 | 17.6 | 17.6 |
| session-dbc5673f | 902,076 | 3,122,815 | 25.0 | 17.3 |
| session-2122a024 | 725,202 | 2,911,655 | 10.5 | 18.9 |
| **total (5)** | **4,808,163** | **18,325,761** | median 21.4 | median 17.6 |

Per-episode environment: loadavg ≈ 7.96 and swap 0/8,191 MB before and after
each of the five.

Caveat: both times include their process start (python ≈ 17 ms, zstd a few ms);
the 64 KiB file read itself is sub-millisecond, and the excerpt time is flat
across sizes while decompression time scales with log size (10.5 ms at 2.9 MB →
30.7 ms at 4.4 MB).

## 6. Bottleneck (from these numbers)

1. **Per query, process start-up dominates.** Mode A median 38.4 ms, of which
   31.7 ms is interpreter start + db open and only 5.5 ms is the query itself.
   Keeping one live process cuts the median from 38.4 ms to 0.8 ms (~47×).
2. **Disk vs RAM is nearly equal.** Per-query median 0.8 ms (disk) vs 0.7 ms
   (in-RAM) — a 0.1 ms difference; p95 22.2 vs 18.2, both driven by load
   spikes. Loading the 1.4 MB db into RAM costs 1.95 ms once.
3. **Per episode, decompression is the size-dependent cost** (10.5–30.7 ms,
   linear in log size) versus a flat ~17–19 ms for reading the pre-cut 64 KiB
   excerpt. Pre-cutting at sleep time removes the dependency on log size.

**Prediction held: yes.** "per-process start-up and decompression dominate;
disk vs RAM is nearly equal" — exactly what the measurements show.

## 7. Reproduce

- Script: `data/bench_phase3.py` (stdlib + zstd CLI; read-only on logs and db;
  `search.py` never run with `--touch`; no network, no model, no sudo).
- Rerun: `python3 data/bench_phase3.py` — writes `data/phase3_results.json`,
  pre-cuts excerpts into `data/episodes/`.
- Inputs: `data/phase3_queries.json`, `data/phase3_queries_detail.json`,
  `data/phase3_top5.json`.

## 8. Notes

- llama-server on port 8092 serves this session (never touched); `/proc/loadavg`
  reflects host load: 18.92 (1-min) before the run, ≈7.96 during it.
- `pgrep -a llama-server` returns nothing inside the sandbox (separate PID
  namespace); `ss -ltnp` shows the 127.0.0.1:8092 listener.
- Swap was reported full (8.0/8.0 GiB) this morning; measured 0/8,191 MB during
  the run — no swap activity affected these numbers.
- Cold page-cache latency is the one unmeasured cell of the table: dropping the
  cache requires root, which this sandbox does not have.
