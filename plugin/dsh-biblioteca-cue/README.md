# dsh-biblioteca-cue

A DeepSeek Harness plugin that reminds the model that the biblioteca holds notes on what the user just wrote.

On the first step of every turn, an `agent/pre-step` hook (the mechanism dsh-recall uses for its arrival cap) crosses the user's message with the biblioteca familiarity index and appends up to `maxCuesPerTurn` lines to that step's messages, in this wording:

> Biblioteca: tienes notas sobre "<term>" (sesión <id>, <date>). Ábrelas con library_open("<id>") si aplican.

- **Index.** `familiar_fts_content` joined with `cards` from `library.db`, read once at load over a read-only SQLite connection (`node:sqlite`) and again when the file's size, mtime or inode changes. A failed reload keeps the previous index.
- **Score.** IDF = ln(cards / df) over the index, with tokens split like FTS5 `unicode61`. Tokens shorter than 4 characters, digits and a short stop list never cue. A token cues only at `minIdf` or above. Its card is the newest card holding it, excluding the current session, and a turn names each card at most once.
- **Habituation.** A term cued once in a session is not cued again in it. The cued terms are read back from the session log, so a restart keeps them.
- **In the turn, logged.** The cue is a `user/message` with `source.kind: "biblioteca-cue"`, `form: "notice"` and `cues: [{ term, score, cardId, cardStart }]`. The system prompt is never touched, so the prompt cache stays valid. The model is never called.

## Config

```yaml
- id: biblioteca-cue
  name: dsh-biblioteca-cue
  config:
    dbPath: /home/jkzero/biblioteca/data/library.db   # absolute
    minIdf: 5.0                                       # positive
    maxCuesPerTurn: 2                                 # positive integer, default 2
```

A relative `dbPath`, a non-positive `minIdf`, a bad `maxCuesPerTurn` or an unreadable database fails at load.

## Dry run

`node tools/dry_run.mjs --min-idf 4.0,5.0 --until 2026-11-23T13:31:00` runs the same selection over the Desktop session logs since 2026-11-08, read-only, and prints cue counts and examples.

## Tests

`npm install` once (it links the `@deepseek-ai/dsh-llm` test stub in `test/stub-dsh-llm`), then `npm test`.

## Install

`./install_desktop.sh` with the app closed. Adapted from `~/dsh-cite-sources/install_desktop.sh`: it packs the tgz into `$DSH_HOME/.local-packages`, adds the dependency and the bundle, installs with the app's pnpm, and restores in-place-patched plugin copies if pnpm rewrote them. No `compatibility.json` entry is needed: `engines.dsh` (`>=0.2.2-rc.5 <0.3.0`) decides alone.
