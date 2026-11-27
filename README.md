# biblioteca

Persistent, pull-based memory of past work sessions for a local coding agent.

biblioteca reads the agent's session logs (zstd-compressed JSONL from the DeepSeek
Harness, dsh), writes one small card per session mechanically, builds a two-level
FTS5 index, and exposes two MCP tools — `library_search` and `library_open` — that the
agent calls when it needs to remember what it did before. The episode layer is
deliberately mechanical: no model writes to it, nothing is auto-injected into the
prompt, and the original logs are never modified
([`docs/design.md:32`](docs/design.md), [`docs/decisions.md:13`](docs/decisions.md)).

## What it is

- **Episode cards** — one per session, mechanical facts only: id, source log path,
  start/end, cwd, first user message (≤300 chars), files touched (≤40), bash commands
  (≤30 × 120 chars, ordered by salience), tool-call count, error count, commit hashes,
  plus per-field source tags (`observed`/`user`) and mechanical salience counters
  (`n_errors`, `n_writes`, `n_commits`, `n_new_files`).
  ([`src/extract.py:8-72`](src/extract.py), [`docs/plan.md:30,36-37`](docs/plan.md))
- **Two-level FTS5 index** — `cards_fts` (recollection: curated first message, files,
  commands) and `familiar_fts` (familiarity: lossless, uncapped list of every file path
  and bash command per session).
  ([`src/build_index.py:9-12`](src/build_index.py), [`docs/decisions.md:38-39`](docs/decisions.md))
- **Two-step retrieval** — `library_search` returns ≤5 one-line results with a size
  footer; `library_open` returns an outline first, then the requested window.
  ([`src/library_server.py:101-129`](src/library_server.py), [`docs/design.md:94-98`](docs/design.md))
- **Self-refresh** — the server re-extracts only the logs whose size/mtime changed, at
  startup and before every search, cutting each log at its last completed turn, so a
  session never finds itself.
  ([`src/refresh.py:4-12`](src/refresh.py), [`src/library_server.py:229-234,867-873`](src/library_server.py))
- **Pull-based** — a fixed cue line in the system prompt (~40 tokens) tells the agent
  the library exists; the agent searches when useful. No auto-injection, ever.
  ([`docs/design.md:32,100-103,119`](docs/design.md))

Five components, Python 3 standard library only, no pip installs: `src/extract.py`,
`src/build_index.py`, `src/search.py`, `src/refresh.py`, `src/library_server.py`
([`src/library_server.py:2`](src/library_server.py)).

## Why bio-inspired

Each design choice implements one documented memory mechanism; the full reference list
is in [`docs/sources.md`](docs/sources.md).

| Mechanism | What we take from it |
|---|---|
| Hippocampal indexing theory (Teyler & DiScenna 1986) | Cards are tiny and point to the session log on disk (the "cortex"); a partial cue activates the whole episode ([`docs/design.md:36-38`](docs/design.md)) |
| Episode barcodes (Chettih et al. 2024, food-caching bird) | One card per session with its own id; episodes are **never** merged or deduplicated ([`docs/design.md:40-42`](docs/design.md)) |
| Complementary learning systems (McClelland et al. 1995) | Two layers: fast mechanical episodes + a slow offline knowledge layer built by a "sleep job" that never runs during work ([`docs/design.md:44-48`](docs/design.md)) |
| Synaptic tagging / behavioural tagging (Frey & Morris 1997; Moncada et al. 2011) | Importance is mechanical (tool errors, commits, file writes, first-touched files), not self-rated by the model ([`docs/design.md:50-52`](docs/design.md)) |
| Persistence × transience (Richards & Frankland 2017) | Ranking decays with time since last use; logs are **never** deleted — only their rank drops ([`docs/design.md:54-56`](docs/design.md)) |
| Schemas (Tse et al. 2007) | Known projects (git repos) are schemas: `--project` search, orphan cards wait for the sleep job ([`docs/design.md:58-60`](docs/design.md)) |
| Source monitoring (Johnson et al. 1993) | Every stored field is tagged `observed`/`user`/`model`; a `model` item is a hint to verify, never evidence ([`docs/design.md:62-68`](docs/design.md)) |
| Reconsolidation / prediction error (Sinclair & Barense 2019) | A card or lesson is corrected only when it is used and contradicted ([`docs/design.md:70-72`](docs/design.md)) |

The recollection/familiarity split also follows the state of the art in agent memory
(Zep episode subgraph, MemGPT/Letta recall storage) and the distinction between
hippocampal recollection and perirhinal familiarity
([`docs/decisions.md:38-39`](docs/decisions.md)).

## How it works

```
            during work (session)                  offline ("sleep", GPU free)
 user ──► agent ──► library_search(q) ─┐           ┌─ extract mechanical cards
                    library_open(id) ──┤           ├─ salience + project schema
                                       ▼           ├─ pre-cut episode excerpts
                     resident process (RAM)        └─ distil knowledge (model, tagged `model`)
                     · FTS index (in-memory SQLite)          │
                     · one-line card summaries               ▼
                     · LRU cache of opened excerpts    data/ on disk
                                       ▲               · library.db
                                       └── reload ◄────· excerpts/
                                                       · raw session logs (read-only)
```
([`docs/design.md:76-88`](docs/design.md); in the current tree the offline column only
exists for its first two steps — see Status below)

- **Search.** Each query word is quoted as an FTS5 phrase; AND first, OR fallback if
  AND returns nothing, and the footer reports which mode ran. Ranking:
  `score = bm25 × recency × local salience` — recency `0.98^days`, salience
  `1 + 0.2 × min(sum, 10)` — with every factor printed in the footer
  (`[N results, X bytes, Y ms, query: …, mode=and|or]`).
  ([`src/search.py:6-12,26-37,334`](src/search.py), [`docs/plan.md:39`](docs/plan.md))
- **Result labels.** `[card]` the session worked on it, `[familiar]` the session saw
  it, `[searched]` the session only searched for it — never evidence for it.
  ([`src/search.py:10-14`](src/search.py), [`reports/phase-2-cards.md:82`](reports/phase-2-cards.md),
  [`docs/decisions.md:44-45`](docs/decisions.md))
- **Open.** Outline first (cap 723 chars, worst case 322 tokens), then a window:
  `query=` (anchor event), `from_seq=` (forward paging) or `at="end"` (how it ended).
  Per-session read budget: 4000 estimated tokens in a 30-minute window; one excerpt is
  capped at 3350 chars (≤1500 tokens worst case); token estimates use a measured
  char/token ratio with a 2.5 fallback.
  ([`src/library_server.py:37-41,53,67,82-89`](src/library_server.py))
- **Access bookkeeping.** `--touch` is the only writer (sets `access_count`,
  `last_accessed`); plain search is read-only and records nothing.
  ([`src/search.py:42-44`](src/search.py))
- **Refresh.** At server startup and before every `library_search`: logs whose
  size/mtime changed are re-extracted, each cut at its last completed turn; sessions
  with no completed turn are skipped and retried later.
  ([`src/refresh.py:4-12`](src/refresh.py), [`src/library_server.py:229-234,867-873`](src/library_server.py))

## Measured results

Every number below comes from the per-phase reports in [`reports/`](reports/); each row
is what was actually measured, with its source line.

| Metric | Value | Source |
|---|---|---|
| dsh-recall auto-injection (baseline) | 0–0.245% of session chars; 0 explicit recall calls | [`reports/phase-1-recall-diagnostic.md:105`](reports/phase-1-recall-diagnostic.md) |
| Cards / index (Phase 2) | 146 cards; 454,656 B db; 0.3–0.4 ms per query | [`reports/phase-2-cards.md:24-27`](reports/phase-2-cards.md) |
| Index with bio fields (Phase 2b) | 454,656 → 1,273,856 B; 9/149 sessions with commits; 18 orphan cards | [`reports/phase-2b-bio-fields.md:3,76,87,101`](reports/phase-2b-bio-fields.md) |
| Scene structure (measure) | 148 sessions; median 1 user message, p90 3; 99 single-message sessions = 48.2% of all chars | [`reports/phase-2-scene-measure.md:10,17,53-54`](reports/phase-2-scene-measure.md) |
| Corpus → index size (Phase 3) | 153 zstd logs (17,005,039 B) → 1,433,600 B db | [`reports/phase-3-measure-ram.md:11-13`](reports/phase-3-measure-ram.md) |
| Search latency (Phase 3, n=20) | disk median 0.8 ms (p95 22.2); in-RAM 0.7 ms; one new process 38.4 ms (interpreter start-up ≈31.7 ms) | [`reports/phase-3-measure-ram.md:71-73`](reports/phase-3-measure-ram.md) |
| Loading the 1.4 MB db | 1.95 ms | [`reports/phase-3-measure-ram.md:73`](reports/phase-3-measure-ram.md) |
| Opening a session | zstd decompress 10.5–30.7 ms vs flat ~17–19 ms for reading the pre-cut 64 KiB excerpt | [`reports/phase-3-measure-ram.md:98-99,118-119`](reports/phase-3-measure-ram.md) |
| Live use (Phase 4) | Run 1: 4 calls / 3,028 tokens; Run 3: 16 / 15,658; Runs 5–6 (current code): 8 / 5,232 and 5 / 2,685; `tools/list` schema 307–320 tokens per request | [`reports/phase-4-live-use.md:21,56,111,211-212,216`](reports/phase-4-live-use.md) |
| Refresh cost (Phase 5) | full rebuild 176 logs → 163 cards in 1,564.7 ms (13 skipped); no-change 33.9 ms; small change 118.6 ms; 1.2 MB log 219.0 ms | [`reports/phase-5-refresh.md:5,11-13`](reports/phase-5-refresh.md) |
| Unprompted use (Phase 5) | 1 of 5 target sessions measured: searched unprompted (search was the first tool call), 3 calls, 1,503 tokens | [`reports/phase-5-sessions.md:2,6,11`](reports/phase-5-sessions.md) |

## How to run

Requirements: Python 3 (standard library only — no pip installs), SQLite with FTS5,
session logs from the dsh sessions directory (default
`~/.config/DeepSeek Harness/sessions`).

```bash
# 1. Extract cards from the session logs (read-only)
python3 src/extract.py [--exclude=<session-prefix>...] [--root DIR] [--out FILE]

# 2. Build the index (atomic; carries over access counters)
python3 src/build_index.py [--cards FILE] [--db FILE]

# 3. Search from the CLI (read-only by default)
python3 src/search.py <query words> [--project NAME] [--touch]

# 4. Run the MCP server (stdio) — this is what dsh / Claude Code talk to
python3 src/library_server.py
```
([`src/extract.py:37,77`](src/extract.py), [`src/build_index.py:211-220`](src/build_index.py),
[`src/search.py:4`](src/search.py), [`src/library_server.py:916`](src/library_server.py))

Tests:

```bash
python3 src/test_search_and_or.py     # hermetic: builds a throwaway fixture db in a temp dir
python3 src/test_server_stdio.py     # runs the whole server on a shadow copy
```

`test_search_and_or.py` builds a small fixture database in a temp directory and never
touches `data/` ([`src/test_search_and_or.py:2,11,19`](src/test_search_and_or.py)).
`test_server_stdio.py` copies `data/library.db`, `data/char_ratio.json` and the five
server modules into a temp tree and starts the server there; the real
`data/library.db` is only read (to make the copy) and is never opened for writing
([`src/test_server_stdio.py:4-10`](src/test_server_stdio.py)).

`data/` (`cards.jsonl`, `library.db`) is git-ignored and never committed
([`reports/phase-2-cards.md:75`](reports/phase-2-cards.md)).

## Status and limitations

- **Phases 0–5 done and measured** ([`docs/plan.md:13-62`](docs/plan.md)): mechanical
  episode layer, self-refresh, resident MCP server, and the fixed cue line. The
  episode-layer tree is stable and is being validated in real work (Phase 5 — rule
  fixed in advance: 5 real resuming sessions, no prompting, measured from logs only;
  1 of 5 measured so far: searched unprompted, 3 calls, 1,503 tokens)
  ([`reports/phase-5-sessions.md:2,6`](reports/phase-5-sessions.md)).
- **Open pull request:** #1 `feat/cue-hook` — "dsh-biblioteca-cue: in-turn cues from
  the familiarity index" — the mechanical trigger of
  [`docs/design.md:103`](docs/design.md); not yet merged.
- **Known limitations** ([`docs/decisions.md`](docs/decisions.md)):
  - the per-session read budget is keyed by the opened session id, not by the agent
    session (one server process for all sessions) — [`docs/decisions.md:82`](docs/decisions.md);
  - single-run token cost varies a lot; compare designs over several runs —
    [`docs/decisions.md:82`](docs/decisions.md);
  - no scene segmentation yet: one card per session (the measure found 99/148 sessions
    are single-message) — [`docs/decisions.md:41-42`](docs/decisions.md);
  - searching is not evidence: terms only seen in searches are labelled `[searched]`
    and never boost a card — [`docs/decisions.md:44-45`](docs/decisions.md);
  - end-gap: some sessions end in bookkeeping events the renderer skips
    ([`reports/phase-4-live-use.md:80,113`](reports/phase-4-live-use.md)).
- **Not built yet** — Phase 6 and Later, and only if Phase 5 shows the library is used
  ([`docs/plan.md:64-74`](docs/plan.md), [`docs/design.md:118`](docs/design.md)): the
  sleep job (knowledge layer, lessons tagged `model`, contradiction marking),
  mechanical contradiction checks at open, embeddings / HippoRAG-style graph, write
  approval.

## References

Full list in [`docs/sources.md`](docs/sources.md). Key works:

- Teyler & Rudy (2007), *The hippocampal indexing theory and episodic memory*, Hippocampus — https://doi.org/10.1002/hipo.20350
- Chettih et al. (2024), *Barcoding of episodic memories in the hippocampus of a food-caching bird*, Cell — https://doi.org/10.1016/j.cell.2024.02.032
- McClelland, McNaughton & O'Reilly (1995), *Why there are complementary learning systems in the hippocampus and neocortex*, Psychological Review — https://doi.org/10.1037/0033-295X.102.3.419
- Moncada et al. (2011), *Behavioral tagging*, PNAS — https://pmc.ncbi.nlm.nih.gov/articles/PMC3150922
- Richards & Frankland (2017), *The persistence and transience of memory*, Neuron — https://doi.org/10.1016/j.neuron.2017.04.037
- Tse et al. (2007), *Schemas and memory consolidation*, Science — https://doi.org/10.1126/science.1135935
- Johnson, Hashtroudi & Lindsay (1993), *Source monitoring*, Psychological Bulletin 114
- Sinclair & Barense (2019), *Prediction error and memory reactivation: how incomplete reminders drive reconsolidation*, Trends Neurosci.
- Gutiérrez et al. (2024), *HippoRAG: neurobiologically inspired long-term memory for LLMs* — arXiv:2405.14831
- Park et al. (2023), *Generative Agents* — arXiv:2304.03442
- Zep: A Temporal Knowledge Graph Architecture for Agent Memory — https://arxiv.org/abs/2501.13956
- MCP specification, stdio transport — https://modelcontextprotocol.io/specification/latest/basic/transports/stdio
