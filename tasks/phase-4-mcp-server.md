# Phase 4: resident library server (MCP)

**Runs on:** local agent (dsh desktop + 27B).
**Sources:** `docs/plan.md` Phase 4; `docs/design.md` §4.1, §4.2, §4.5.
**Order:** starts when Phase 3 is done. Its numbers decide the server's form.

## Goal

Give the agent two pull tools, `library_search` and `library_open`, through a resident Python MCP server.

## Hard rules

- Session logs are read-only. Never write to, move, or delete anything under `~/.local/share/deepseek-harness/sessions`.
- The server must not call a model or use the GPU. It makes no network calls beyond its MCP transport.
- Do not install packages without asking. If an MCP library is needed and missing, stop and ask.
- Integrate through MCP. Do not write a Cordis plugin.
- Do not add the cue line or any mechanical trigger; those belong to Phase 5. Nothing is injected into the prompt.
- Before editing a launch script or the dsh profile, copy the file next to itself with a `.bak-pre-biblioteca-<date>` suffix. Change only what this task needs.
- Do not restart the desktop app or the model server yourself, because you run inside them. Ask the user to do it.
- Generated output stays in `data/`, which must never be committed.

## Steps

1. Restate the Phase 3 table and the form it implies for the server. If Phase 3 does not settle how excerpts are cut for `library_open`, stop and ask.
2. Write the Python MCP server with two tools:
   - `library_search(query, project?)`: at most 5 one-line cards, hard cap of about 300 tokens.
   - `library_open(id)`: one excerpt.
3. Hold the index in RAM, with an LRU cache of opened excerpts.
4. Reload atomically when the db file changes: a query never sees a half-written db.
5. Start the server from the existing launch command. `plan.md` names one command, but this machine has two: `~/bin/manifiestate` (text stack) and `~/bin/manifiestate_v` (vision stack). Add it to both, so the library is available on either stack, and report both changes.
6. Register the server in the dsh desktop profile (`~/.local/share/deepseek-harness/profiles/desktop`). Use an explicit interpreter path, and confirm FTS5 works on that interpreter. `python3` on PATH is not the same interpreter as the one bundled with the app.
7. Ask the user to restart the stack and open a new session. In that real session, call both tools on real work.
8. Read that session's log (read-only) and work out the tokens the tools added.

## Report exactly

1. The Phase 3 numbers used, and the server form chosen from them.
2. Files changed outside the repo, each with its backup path.
3. Search and open latency on the running server (median, p95).
4. The session id where the agent called both tools, with the log events showing each call.
5. Tokens added by each tool call and by the tool definitions, and how you counted them.
6. The result of an atomic reload test: the db file was replaced while the server was running, and later queries saw the new data.
