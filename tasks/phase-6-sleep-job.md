# Phase 6: sleep job (knowledge layer)

**Runs on:** Claude Code. This job occupies the GPU and the model server for a long time, so the local agent does not run it.
**Sources:** `docs/plan.md` Phase 6; `docs/design.md` §3.3, §3.7, §3.8, §4.1.
**Order:** starts when Phase 5 is done.

## Goal

Build the offline knowledge layer: pre-cut excerpts for new sessions, lessons distilled across episodes by the 27B, and marks on lessons contradicted by later evidence.

## Hard rules

- Run only when the GPU is free, never during work. Before each run, check for an active working session; if there is one, do not start. The job must not stop or restart a model server someone is using.
- This phase may use the model and the GPU, through the local llama-server only. No other network access.
- Session logs are read-only. Never write to, move, or delete anything under `~/.local/share/deepseek-harness/sessions`.
- Every lesson is tagged `model` and linked to its source episodes. A `model` item is never upgraded to `observed`.
- Episodes are never merged. Deduplication applies only to the knowledge layer.
- Mechanical episode cards stay as they are; the model does not rewrite them.
- Do not delete a contradicted lesson. Mark it and hide it from ranking if it has been superseded.
- Generated output stays in `data/`, which must never be committed.

## Steps

1. Confirm the GPU is free: no working session active, and check `nvidia-smi` and `pgrep -a llama-server`. If the model server is not up and the machine is idle, ask the user how to start it.
2. Pre-cut excerpts for sessions that do not have one yet.
3. Distil lessons across episodes with the 27B. Tag each one `model` and link it to its source episodes. Example of the expected kind of lesson: "in dsh, creating a provider does not select it".
4. Mark lessons contradicted by later evidence as `contradicted on <date> by <session>`. Only a use that reality contradicts counts, such as a path that no longer exists or a commit that was reverted.
5. Write a new database file, so the resident server detects it and swaps atomically. Do not modify the live file in place.
6. Search for lessons through `library_search` and confirm that each result shows its tag and source episodes.

## Report exactly

1. Run time, sessions covered, and GPU state before the run.
2. Excerpts created, and lessons created.
3. Example searches returning lessons, each showing its source episodes.
4. Query results proving that no lesson is tagged `observed` and that every lesson has at least one source episode.
5. Lessons marked contradicted, each with its date and session, or "none".
6. Confirmation that the resident server reloaded the new database, and that no working session ran during the job.
