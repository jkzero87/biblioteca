# biblioteca

A bio-inspired, pull-based memory for a local coding agent.

The agent (Qwen3.8-27B served by llama.cpp, driven by the DeepSeek Harness desktop app on Linux) forgets everything between sessions. `biblioteca` gives it an **indexed library of its own past work** that it can search **only when it needs to**, without loading memory into every prompt and without overloading a small context window.

> Status: **design + plan** (Phase 0). No code yet. See [`docs/plan.md`](docs/plan.md).

## The idea in one line

Prepare a lot offline, keep a tiny index hot in RAM, store everything else on disk, and let the agent pull a short card only when a cue says it is useful.

## Five words to remember the design

**Index, Barcode, Sleep, Source, Forget.**

| Word | Brain mechanism | What it becomes here |
|---|---|---|
| Index | Hippocampal indexing theory | Small cards that point to session logs on disk; a partial cue retrieves the whole episode |
| Barcode | Episode "barcodes" in food-caching birds | Every session is its own card; episodes are never merged, so they don't interfere |
| Sleep | Complementary learning systems + sleep replay | Fast mechanical episode layer now; slow "knowledge" layer distilled offline when the GPU is free |
| Source | Source / reality monitoring | Every field carries its origin: `observed`, `user`, or `model` — model claims are hints, never facts |
| Forget | Adaptive forgetting; tag-and-capture | Unused cards sink in ranking (never deleted); salient episodes (errors, commits, writes) rank higher |

Plus: **schemas** (cards attach to known projects first) and **reconsolidation** (a card is corrected only when it is used and contradicted by reality).

Full reasoning: [`docs/design.md`](docs/design.md).

## Constraints that shape everything

- One 16 GB GPU, one model slot. Anything that needs the model (summaries, "sleep") runs outside working sessions.
- 64 GB system RAM — room for a resident index.
- The model confabulates and then cites its own confabulation as if it came from context. Memory must never turn a model claim into a fact.
- Every token in the prompt costs prefill and competes with the task. Memory is **pulled**, not pushed.

## Repository layout

```
README.md            this file
docs/design.md       problem, research, architecture
docs/plan.md         phases, tasks, done-criteria, metrics
docs/decisions.md    dated decision log
docs/sources.md      references (neuroscience + agent-memory systems)
tasks/               task prompts for the local agent, one per step
src/                 code (empty until Phase 2)
data/                generated cards, index, excerpts — git-ignored, never committed
```

`data/` is built from personal session logs and **must never be committed**.

## How work is done

Each step in `docs/plan.md` has a matching prompt in `tasks/`. Steps are run by the local agent, except jobs that occupy the GPU or the model server for a long time, which are run by Claude Code.

## License

MIT
