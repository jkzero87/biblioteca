# Phase 2b — bio fields, source map, projects, access tracking, scored ranking

Rebuilt: 149 cards (old db: 147; this time only the current session
`session-21d526c5` is excluded, so `session-6e168499`, the Phase 2 test
session, is back in the library). 0 extraction failures. 36 git repos
scanned under `~` (depth ≤ 4, hidden dirs and node_modules/.venv skipped).

## Changes

- `src/extract.py` — per-card `n_writes` (write/edit tool calls carrying a
  path, one per event), `n_commits` (= len(commit_hashes), regex unchanged),
  `n_new_files` (file paths first seen in this session, sessions processed
  in start-time order; a path seen in any earlier session is not new), and
  `source_map` tagging every stored field `observed` or `user`.
- `src/build_index.py` — new columns `n_writes, n_commits, n_new_files,
  project, last_accessed, access_count, source_map`. `last_accessed` /
  `access_count` are carried over from the previous db before the atomic
  replace, so a rebuild never resets them. Project rule (stated in the
  module docstring): (1) deepest git repo equal to or ancestor of the
  session cwd; (2) else the repo under which the most `files_touched` paths
  fall (>0; ties → lexicographically smallest repo path); (3) else `orphan`.
- `src/search.py` — inside each level, hits are ranked by
  `score = bm25 × recency × salience` with
  `bm25 = −FTS5 rank`, `recency = 0.98^days since last_accessed` (or session
  end if never accessed), `salience = 1 + 0.2 × min(n_errors + n_commits +
  n_writes, 10)`. All four numbers plus the source tag of each matched
  field are printed per result. `[card]` hits always precede `[familiar]`.
  `--project NAME` filters both levels (repo path or repo basename).
  Returned sessions get `access_count += 1` and `last_accessed = now`
  (db opened rw); logs untouched.

## Old vs new ranking (same 3 queries)

Old outputs captured in `data/old/queries_old.txt` **before** the rebuild;
new outputs in `data/old/queries_new.txt` (first run, pre-access).

### `standard-light` (TRUE labels from reports/phase-2-cards.md)

| # | old (Phase 2) | new (Phase 2b) | label |
|---|---|---|---|
| 1 | [card] b450021f | [card] 86987b85 | TRUE |
| 2 | [card] 86987b85 | [card] ac96b069 | FALSE |
| 3 | [card] ac96b069 | [card] 8b1b3fb3 | FALSE |
| 4 | [card] 8b1b3fb3 | [card] b450021f | TRUE |
| 5 | [familiar] 6d7eea23 | [card] 6e168499 | FALSE |

The two TRUE sessions move from positions 1+2 to 1+4. b450021f has the
highest bm25 (6.16) but low salience (1.2) and an October end
(recency 0.685) → score 5.065, below ac96b069 (7.505) and 8b1b3fb3 (6.991).
The old familiar-only hit 6d7eea23 drops out entirely (new 5th is a card).

### `compatibility.json`

| # | old | new |
|---|---|---|
| 1 | [card] 8b1b3fb3 | [card] 6e168499 |
| 2 | [familiar] 41334c39 | [card] 8b1b3fb3 |
| 3 | [familiar] f4c1b1f8 | [familiar] 41334c39 |
| 4 | — | [familiar] f4c1b1f8 |

### `venexuela`

| # | old | new |
|---|---|---|
| 1 | [card] 38b3c05c | [card] 6e168499 |
| 2 | — | [card] 38b3c05c |

Observation: `session-6e168499` is the Phase 2 test session whose
`bash_commands` literally contain `python3 src/search.py <query>`, so it
matches every one of these queries on `commands_text` and carries max
salience (3.0) with recency ≈ 1.0. Honest consequence of the formula —
recency + salience outrank pure bm25 for today's high-activity sessions.

## Item 3 — commit counts

`SELECT count(*) FROM cards WHERE n_commits > 0` → **9 of 149 sessions**
have at least one commit hash. Examples (most recent first):

- `session-6e168499…` n_commits=2 (2026-11-07)
- `session-8b1b3fb3…` n_commits=2 (2026-11-07)
- `session-f4c1b1f8…` n_commits=1 (2026-11-07)

No regex change from Phase 2 (`\[[^\s\]]+ ([0-9a-f]{7,40})\]`).

## Orphans

18 of 149 cards are `orphan` (no repo matches cwd or files). Many are
chat-only sessions with zero files. 5 file paths that landed in orphan
cards:

- `/home/jkzero/Documents/deepseek-harness/default-workspace/research/kienyke.txt`
- `/home/jkzero/Documents/deepseek-harness/default-workspace/research/neo2_rap.txt`
- `/home/jkzero/Documents/deepseek-harness/default-workspace/research/iamrap.txt`
- `/home/jkzero/Documents/deepseek-harness/default-workspace/research/remezcla.txt`
- `/tmp/dsh-desktop-day1/alpha-red-left-half.png`

(`…/Documents/deepseek-harness/default-workspace` is not a git repo.)

Project distribution: dsh-scratch 80, deepseek-harness 26, orphan 18,
worktree q-desktop-T4 8, biblioteca 5, es-reasoning-finetune 5,
q-desktop-T2 4, rust-lab 2, q-desktop-T1 1.

## Source map (example card, session-86987b85)

```json
{
  "bash_commands": "observed", "commit_hashes": "observed", "cwd": "observed",
  "end": "observed", "error_count": "observed", "familiar_terms": "observed",
  "files_touched": "observed", "first_message": "user", "id": "observed",
  "n_commits": "observed", "n_new_files": "observed", "n_writes": "observed",
  "source": "observed", "start": "observed", "tool_call_count": "observed"
}
```

No field is tagged `model`: `SELECT count(*) FROM cards WHERE
source_map LIKE '%model%'` → 0, and every query line prints only
`observed`/`user` tags (visible in `data/old/queries_new.txt`).

## `--project` example

`python3 src/search.py standard-light --project=dsh-scratch`:

```
[card] session-86987b85…  2026-10-20T23:46:23  matched=first_message:user,files_text:observed,commands_text:observed  bm25=5.67 recency=1.000 salience=2.0 score=11.344  …
[card] session-ac96b069…  2026-10-20T01:23:25  matched=files_text:observed,commands_text:observed  bm25=4.98 recency=1.000 salience=2.2 score=10.960  …
[card] session-b450021f…  2026-10-20T01:20:43  matched=first_message:user,commands_text:observed  bm25=6.16 recency=1.000 salience=1.2 score=7.397  …
[familiar] session-6d7eea23…  2026-10-20T21:48:22  matched=familiar_terms:observed  bm25=2.40 recency=0.698 salience=3.0 score=5.032  …
[4 results, 1032 bytes, 6.3 ms, query: "standard-light", project: dsh-scratch]
```

(The earlier `session-8b1b3fb3`/`6e168499` hits are correctly excluded —
their project is `biblioteca`.)

## Notes

- Carry-over of `last_accessed`/`access_count` was a no-op this round
  (old schema had no such columns); the mechanism is in place for future
  rebuilds.
- Access tracking is live: a second run of `standard-light` shows
  `recency=1.000` for the five sessions returned by the first run, i.e.
  accessed sessions climb in rank on subsequent queries.
- `n_new_files` is recorded per card but, per the task, is not part of the
  salience formula.
