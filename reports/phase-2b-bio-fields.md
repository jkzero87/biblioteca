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

## Fix pass (2026-11-07)

Three changes (see docs/decisions.md, 2026-11-07 entries): source
monitoring (retrieval commands — src/search.py, src/verify_match.py,
grep/egrep/rg, find -name/-iname/-path — stay in familiar_terms but are
excluded from bash_commands and cards_fts, counted per card as
n_retrieval), local salience (score = bm25 × recency × local_salience,
local_salience = 1 + 0.2·min(matched error/commit/write items, 10); old
session-wide salience still printed as session_sal, not used in the
score), and access only with --touch (db opened read-only by default).

Baseline: old = ba6f45a (phase 2b code), 149 cards, excluding
session-21d526c5 and the fix-pass session; new = 150 cards, only the
fix-pass session excluded. Note: 21d526c5 therefore exists only in the
new db (it was excluded from the phase-2b db). Both runs are fresh,
no --touch, access_count/last_accessed NULL before the test queries.

### standard-light

| # | old (ba6f45a) | label | new | label |
|---|---|---|---|---|
| 1 | 86987b85 (card) 7.913 | TRUE | b450021f (card) 7.413 | TRUE |
| 2 | ac96b069 (card) 7.503 | FALSE | ac96b069 (card) 6.015 | FALSE |
| 3 | 8b1b3fb3 (card) 6.989 | FALSE | 86987b85 (card) 4.324 | TRUE |
| 4 | b450021f (card) 5.064 | TRUE | 21d526c5 (familiar) 4.733 | FALSE |
| 5 | 6e168499 (card) 4.303 | FALSE | 6e168499 (familiar) 4.517 | FALSE |

TRUE hits at positions 1–4 (old) → 1–3 (new). 8b1b3fb3, whose only match
was a search command, drops out of the top 5. 21d526c5 (new db only)
appears via familiarity, matching only through retrieval acts — FALSE
by design, since familiarity is lossless. 6d7eea23 (user-labelled
FALSE) also drops out.

### compatibility.json

old: 6e168499 (card, commands) FALSE 17.111 · 8b1b3fb3 (card, commands)
FALSE 5.210 · 41334c39 (familiar) TRUE 4.249 · f4c1b1f8 (familiar)
FALSE 2.288.
new: 6e168499 (familiar) FALSE 5.013 · 21d526c5 (familiar) FALSE 4.982
· 41334c39 (familiar) TRUE 2.235 · 8b1b3fb3 (familiar) FALSE 2.034 ·
f4c1b1f8 (familiar) FALSE 1.356.

Every card-level match was a search command, so all hits now come from
familiar_terms. The only TRUE hit, 41334c39, matches on directly
reading the profile's compatibility.json (cat, not a retrieval act)
and stays in the top 5. 21d526c5 (new db only) is a test session whose
only matches are retrieval acts — FALSE.

### venexuela

old: 6e168499 (card, commands) FALSE 5.372 · 38b3c05c (card,
first_message) TRUE 4.639.
new: 38b3c05c (card, first_message) TRUE 3.142 · 6e168499 (familiar)
FALSE 5.991 · 21d526c5 (familiar) FALSE 5.768.

The only true hit ("¿quién es el presidente de venexuela?") rises from
position 2 to 1: card level precedes familiar level, and the retrieval-act
match of 6e168499 no longer sits on a card.

### Invariants

- sha256 data/library.db identical before and after running search.py
  five times without --touch (3 queries + 2 reruns of standard-light):
  7d59207ac2f57ca8f91d05bd871d1c749573afa52646b0dac9d3f5c0e4bb1575;
  no -wal/-journal file created.
- access_count/last_accessed reset to NULL for all 150 cards after the
  rebuild; build_index carry-over verified (the rebuild started from a
  db that still carried the old access values).
- n_retrieval total across 150 cards: 453 (35 cards with n_retrieval > 0).

## Fix pass 2 (2026-11-07)

A familiarity hit that matches only through search commands is now
labelled [searched] and ranks last. Order: [card] > [familiar] >
[searched]. Nothing is dropped (see docs/decisions.md, 2026-11-07).

Implementation: extract.py stores familiar_classes, one class per
familiar_terms entry, same order — 'write'/'read' for file paths
(written vs everything else), 'retrieval' for retrieval bash commands
and for grep/glob tool-call path args (searching is not evidence),
'error'/'commit'/'write'/'read' for the other bash commands (the same
salience classes). build_index.py adds a familiar_classes TEXT column.
search.py attributes each hit's matched terms with an in-memory FTS5
table (one row per term, phrase query per query word, same tokenizer as
familiar_fts) and reads the classes from cards: all matched classes
'retrieval' → [searched], else [familiar].

Baseline: pre-change db sha 7d59207ac2f57ca8f91d05bd871d1c749573afa52646b0dac9d3f5c0e4bb1575
(150 cards). Rebuilt db: 151 cards — the fix-pass session (044c7b02) is
now included, only the current session is excluded — sha256
86d65e4647ea3931003f69c88cb1ea4bbc75b4e289cdc8c992386acba2c625c1.
Class aggregate over the 151 cards: read 959, write 244, retrieval 456,
commit 11, error 2; familiar_classes aligned with familiar_terms on
every card.

Old = 0b0bf0e search.py, new = current; both runs on the same rebuilt
db, no --touch.

### standard-light

| # | old (0b0bf0e) | label | new | label |
|---|---|---|---|---|
| 1 | b450021f (card) 6.921 | TRUE | b450021f (card) 6.920 | TRUE |
| 2 | ac96b069 (card) 5.623 | FALSE | ac96b069 (card) 5.623 | FALSE |
| 3 | 86987b85 (card) 4.039 | TRUE | 86987b85 (card) 4.039 | TRUE |
| 4 | 044c7b02 (card) 2.153 | test session | 044c7b02 (card) 2.153 | test session |
| 5 | 21d526c5 (familiar) 4.534 | FALSE | 6d7eea23 (familiar) 1.543 | FALSE |

Both TRUE hits stay at 1 and 3. Slot 5 changes: 21d526c5 matches
standard-light only through retrieval acts, so it is now [searched] and
ranks below 6d7eea23, whose match is a read act (inspecting the preset
files).

### compatibility.json

old: 044c7b02 (card) 2.843 · 6e168499 (familiar) 4.761 FALSE ·
21d526c5 (familiar) 4.731 FALSE · 41334c39 (familiar) 2.126 TRUE ·
8b1b3fb3 (familiar) 1.936 FALSE.
new: 044c7b02 (card) 2.843 · 41334c39 (familiar) 2.126 TRUE ·
6e168499 (searched) 4.760 FALSE · 21d526c5 (searched) 4.731 FALSE ·
8b1b3fb3 (searched) 1.936 FALSE.

The only TRUE hit — 41334c39, which directly read the profile's
compatibility.json (cat, not a retrieval act) — rises from 4 to 2. The
three search-only sessions are labelled [searched] and sink below it.

### venexuela

old: 38b3c05c (card) 2.800 TRUE · 044c7b02 (card) 2.524 · 6e168499
(familiar) 5.502 FALSE · 21d526c5 (familiar) 5.297 FALSE.
new: 38b3c05c (card) 2.800 TRUE · 044c7b02 (card) 2.524 · 6e168499
(searched) 5.502 FALSE · 21d526c5 (searched) 5.297 FALSE.

Order unchanged; the two FALSE hits are re-labelled [searched] (their
only matches were running searches for the word).

### f4c1b1f8 (compatibility.json) — re-adjudication

Verdict: FALSE, correct label [searched]. Its only familiar term
containing compatibility.json is a bash retrieval command (ls +
grep "dsh-recall" over the desktop profile dir, including
$P/compatibility.json) → class retrieval. verify_match.py (bounded
output) confirms the matching log lines are tool/result events,
assistant reasoning and that one bash call — no write or read of the
file. The option B report's "the file was born there" claim is not
supported by the log; the fix pass's FALSE stands.

### Invariants

- sha256 data/library.db identical before and after three runs of the
  three queries without --touch:
  86d65e4647ea3931003f69c88cb1ea4bbc75b4e289cdc8c992386acba2c625c1;
  no -wal/-journal file created.
- Pre-change baseline db:
  7d59207ac2f57ca8f91d05bd871d1c749573afa52646b0dac9d3f5c0e4bb1575.
- This session (session-73f9ddfc-9226-4a47-ad65-3b7bb1d19d31) excluded
  from the re-extraction; data/ remains gitignored.
