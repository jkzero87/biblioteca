# Design

Date: 2026-11-06. Status: proposed, not yet measured.

## 1. Problem

The local agent has no memory across sessions. Inside a session, the context window (on the 27B: 65,536 tokens on the text stack started by `manifiestate`, 49,152 on the vision stack started by `manifiestate_v`) is already managed by an aggressive compaction plugin. We want the agent to be able to use what it did before — decisions, file locations, failures, fixes — **when that is useful**, without:

- loading a memory block into every prompt,
- growing the tool surface (measured earlier: Minimal preset ~1,130 base prompt tokens vs Standard ~7,771),
- breaking the KV prefix cache (re-prefilling a long conversation at ~900 tokens/s costs seconds every time),
- turning the model's own inventions into stored "facts".

## 2. What the field does today (four pieces)

1. **Notebook always open** — a short index injected into every prompt (Claude Code `MEMORY.md`, dsh-auto-memory).
2. **Library** — longer notes the agent opens with its normal tools when the index points there.
3. **Diary** — raw transcripts of past sessions, searchable (dsh-recall, chat-history search).
4. **Tidier** — a background process that merges, rewrites and evicts.

Heavier systems (Mem0, Zep/Graphiti, Letta, OpenViking) add embeddings, temporal graphs or a separate server. For one user on one machine with one GPU slot, that cost is not justified at the start.

### Existing dsh plugins reviewed

| Plugin | What it does | Why not adopt as-is |
|---|---|---|
| dsh-auto-memory | Markdown files + index injected each step, byte budget (~4 KB) | Push model; index re-evaluated every step (cache churn on writes); the model writes freely |
| dsh-memento | SQLite, approval-gated writes, snapshot frozen per session | Good ideas (freeze, approval); still a push snapshot of curated facts |
| dsh-memory (LZMW) | One `memory` tool, auto-summary at context pressure, curator subagent | Summarisation runs during the session (GPU contention) |
| dsh-recall | Search over raw session text, optional silent auto-recall | Already installed in the desktop profile — must measure what it injects (Phase 1) |

`biblioteca` is complementary: a **curated, mechanical, pull-based** layer over the same sessions dsh-recall searches raw.

## 3. Biological mechanisms and what we take from each

### 3.1 Hippocampal indexing theory
The hippocampus stores an index of where cortical content lives, not the content; a partial cue activates the index and reinstates the whole episode.
**Design:** cards are tiny and point to the session log on disk (the "cortex"). Searching `load.py` retrieves the episode. HippoRAG showed the idea works for LLM retrieval and is cheaper than iterative retrieval.

### 3.2 Episode barcodes (black-capped chickadee)
Each cache event gets a sparse, unique hippocampal activity pattern, reactivated at retrieval; nearby caches get unrelated codes; the code is separate from place coding. Interpreted as a way to store many memories quickly without interference.
**Design:** one card per session with its own id. **Never merge or deduplicate episodes**, even when they touch the same files. Deduplication belongs only to the knowledge layer.

### 3.3 Complementary learning systems + sleep replay
Fast hippocampal learning of separate episodes; slow neocortical learning of overlapping general structure; replay during sleep transfers one to the other.
**Design:** two layers.
- *Episodes*: mechanical cards, written immediately, no model involved.
- *Knowledge*: lessons distilled across many episodes ("in dsh, creating a provider does not select it"), built by a **sleep job** that runs offline when the GPU is free — never inside a working session.

### 3.4 Synaptic tag-and-capture / behavioural tagging
A weak trace sets a transient tag; a strong, novel event nearby in time stabilises it. Salience (novelty, surprise, emotion) decides what lasts.
**Design:** importance is **not** self-rated by the model (Generative Agents uses LLM-rated importance; with a model that confabulates this is unsafe). Use mechanical salience from the log: tool errors (`isError`), commits, file writes, files touched for the first time.

### 3.5 Adaptive forgetting (persistence × transience)
Forgetting reduces the weight of outdated information and prevents overfitting to specific past events; the goal of memory is better decisions, not maximal storage.
**Design:** ranking decays with time since last use; superseded knowledge is hidden. **Logs are never deleted** — only their rank in the index drops. Targets a known failure: stale notes with already-completed TODOs caused repeated work.

### 3.6 Schemas (Tse et al., 2007)
New associations consolidate unusually fast when they fit an existing schema.
**Design:** known projects (git repos) are schemas. A card that maps to a project is indexed under it immediately; search looks inside the project first (`--project`). Orphan cards wait for the sleep job.

### 3.7 Source monitoring (Johnson)
Memories carry no built-in label of their origin; the brain infers it, and false memories and confabulation come from that inference failing.
**Design (most important for this agent):** every stored field carries its source:
- `observed` — read from a tool call/result in the log,
- `user` — said by the user,
- `model` — said by the model.
Search results show the tag. A `model` item is a hint to verify, never evidence. This directly addresses the observed failure where the model invented a date and later cited it as coming from context.

### 3.8 Reconsolidation driven by prediction error
Retrieval makes a memory modifiable; a mismatch between expectation and reality drives the update.
**Design:** a card or lesson is corrected only when it is **used and contradicted** (path no longer exists, commit reverted). It is marked "contradicted on <date> by <session>". Cheap, targeted maintenance instead of global curation runs.

## 4. Architecture

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

### 4.1 RAM vs disk
- **RAM (resident process, tens of MB):** FTS index loaded into an in-memory SQLite database, one-line card summaries, LRU cache of recently opened excerpts.
- **Disk:** full database, pre-extracted excerpts, original compressed logs (never modified).
- **Reload:** the sleep job writes a new database file; the resident process detects it and swaps atomically.
- **Hypothesis to test (Phase 3):** the real costs are interpreter start-up per query and zstd decompression of large sessions, not disk vs RAM (Linux page cache already keeps a hot small file in RAM).

### 4.2 Two-step retrieval (protects the context)
1. `library_search(query, project?)` → 3–5 one-line cards, hard cap ~300 tokens.
2. `library_open(id)` → one excerpt, only if the agent decides it helps.

### 4.3 Getting the agent to use it
Measured earlier: the agent used web fetch on its own only when the prompt announced it was needed. Expect the same here.
- **Cue line** in the system prompt (~40 tokens, never changes → cache-safe): "You have a library of past work; search it when the task names a project, a file, or something that may have been done before."
- **Mechanical trigger** (only if the cue line is not enough): the harness runs a cheap search on the user message; on a strong match it appends a hint ("2 cards match load.py"), not content.

### 4.4 Ranking
`score = bm25 × recency × salience`
- recency = 0.98^(days since last access, or since session end if never accessed)
- salience = 1 + 0.2 × min(n_errors + n_commits + n_writes, 10)
All three factors are printed with each result so ranking can be audited. Weights are starting values, to be tuned on real use.

### 4.5 Integration
A Python **MCP server** (dsh ships an MCP client). Same server usable from Claude Code. Started by the existing launch command.

## 5. What we deliberately do not copy (yet)

- **Model-rated importance** (see 3.4).
- **Consolidation during work** — the brain does it asleep; this GPU has one slot.
- **Knowledge graph / embeddings (HippoRAG-style)** — needs model extraction and an embedding model on the GPU. Later phase, only if the mechanical layer proves it is used.
- **Always-injected notebook** — rejected in favour of pull.

## 6. Risks

| Risk | Mitigation |
|---|---|
| Agent never searches | Cue line, then mechanical trigger; measure search rate |
| Agent searches but nothing changes | Metric: searches that changed the plan; if near zero, the library is noise |
| Model claims stored as facts | Source tags; `model` never upgraded to `observed` |
| Personal data leaks into git | `data/` git-ignored; repo holds code and docs only |
| dsh upgrade breaks integration | MCP boundary instead of a Cordis plugin; pin versions |
| Overlap with dsh-recall auto-recall | Phase 1 measures its injection before building on top |

## 7. Success metrics (judged on real work, not synthetic tests)

1. Searches per session, and share that changed the agent's plan (avoided re-investigation, reused a decision).
2. Tokens added to the context by the library per session.
3. Search and open latency (median, p95).
4. Wrong recalls: cases where a retrieved card was misleading.
