# Phase 2b: bio-inspired fields (Source + Forget + Schema)

**Runs on:** local agent (dsh desktop + 27B).
**Sources:** `docs/plan.md` Phase 2b; `docs/design.md` §3.4, §3.5, §3.6, §3.7, §4.4.
**Order:** starts when Phase 2 is done.

## Goal

Add source tags, mechanical salience, project assignment, and the audited ranking to the Phase 2 cards.

## Hard rules

- Session logs are read-only. Never write to, move, or delete anything under `~/.local/share/deepseek-harness/sessions`.
- No model, no GPU, no network. The code must not call any model server.
- Nothing is tagged `model` in this phase. Sources are `observed` (read from a tool call or result) or `user` (said by the user).
- Salience comes from log signals only. Never ask a model how important something is.
- Forgetting means lower rank. Do not delete logs or cards, and do not merge episodes.
- Generated output stays in `data/`, which must never be committed.

## Steps

1. Add a per-field source map to each card, tagging every field `observed` or `user`.
2. Add mechanical salience counts from the log: `n_errors`, `n_writes`, `n_commits`, `n_new_files`. A new file is one touched for the first time, across sessions in time order.
3. Assign each card to a project: the git repo whose path best matches its cwd and files, otherwise `orphan`. State the matching rule you used.
4. Add `last_accessed` and `access_count`. A rebuild must not reset them.
5. Implement the ranking `score = bm25 × recency × salience`:
   - recency = 0.98^(days since last access, or since session end if never accessed)
   - salience = 1 + 0.2 × min(n_errors + n_commits + n_writes, 10)
6. Print bm25, recency, salience, and score with every search result, plus the source tags. `n_new_files` is recorded but is not part of the salience formula above; do not add it.
7. Add `--project` to search.
8. Run the same real queries with the Phase 2 ranking and the new ranking.

## Report exactly

1. Old and new ranking side by side for the same queries, with all factors printed.
2. Project assignment rule, orphan count, and 5 orphan paths.
3. One example card showing its source map.
4. Confirmation, from a query, that no field is tagged `model`.
5. An example `--project` search with its output.
