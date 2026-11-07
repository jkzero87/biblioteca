# Phase 1: recall diagnostic

**Runs on:** local agent (dsh desktop + 27B).
**Sources:** `docs/plan.md` Phase 1; `docs/design.md` §2 and §6.
**Order:** may run in parallel with Phase 2.

## Goal

Measure what the installed dsh-recall plugin adds to real sessions, so the user can decide whether to keep it as is, tune it, or turn off its auto-recall before biblioteca sits on top.

## Hard rules

- Everything in this phase is read-only: session logs, the dsh profile, plugins, and config. Change nothing outside your report.
- Read session logs only by decompressing to stdout (`zstd -dc <file>`). Never write to, move, or delete anything under `~/.local/share/deepseek-harness/sessions`.
- No network. Do not install anything, including dsh-auto-memory.
- Your commands and scripts must not call the model or use the GPU.
- Every claim in your report needs a file path or a command with its output. If something cannot be established, say so instead of inferring it.

## Steps

1. Locate the desktop dsh profile and list its plugins. Starting point: `~/.local/share/deepseek-harness/profiles/desktop` (`package.json`, `cordis.patch.yml`, `compatibility.json`).
2. For dsh-recall, report the version actually installed in the profile's `node_modules`, not only a tarball name. `.local-packages/` holds more than one dsh-recall tarball.
3. Report dsh-recall's active config and whether auto-recall injection is ON. Read the plugin's code or schema for its defaults, then apply the overrides in `cordis.patch.yml`.
4. Pick the 3 most recent real sessions. Logs are at `sessions/<workspace>/session-<uuid>/session.v4.jsonl.zstd`. Exclude the session you are running in, because its log is still being written.
5. Inspect those logs and identify which events carry dsh-recall injections. Record the event types and fields you relied on. Separate automatic injections from explicit `recall` and `recall_index` tool calls.
6. For each session, count the recall events and recall characters, and the total session characters. Compute recall characters as a percentage of session characters. State how "session chars" is measured so the number can be reproduced.
7. Check, without installing it and without network, whether dsh-auto-memory supports the installed dsh version. Get the installed version from the app (`X-AppImage-Version` in `~/Applications/DeepSeek-Harness/deepseek-harness.desktop`). If no local copy of dsh-auto-memory exists, report the check as undetermined and name what would be needed to finish it.

## Report exactly

1. Profile path and plugin list, with evidence.
2. dsh-recall: installed version, active config, auto-recall ON or OFF, with evidence.
3. Table with one row per session: session id, recall events, recall chars, session chars, %.
4. The commands or script used for the table, so it can be re-run.
5. dsh-auto-memory compatibility: yes, no, or undetermined, with evidence.
6. Your recommendation on the decision (keep, tune, or turn off auto-recall), marked as a recommendation. Do not apply it.
