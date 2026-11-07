# Phase 1 report — dsh-recall diagnostic

Date: 2026-11-07. Read-only diagnostic; nothing under the sessions folder, the profile, or plugins was modified.

## 1. Profile path and plugin list

Profile: `~/.local/share/deepseek-harness/profiles/desktop/`

Plugins (from `package.json` `dependencies`, confirmed present in `node_modules/` — `ls` of that dir shows exactly these plus `@deepseek-ai`, `@standard-schema`):

| Plugin | Source |
|---|---|
| dsh-compaction-select 0.1.0 | `.local-packages/dsh-compaction-select-0.1.0.tgz` |
| dsh-free-search 0.4.39 | `.local-packages/dsh-free-search-0.4.39.tgz` |
| dsh-output-cap | `link:/home/jkzero/dsh-output-cap` |
| dsh-preset-standard-light | `link:/home/jkzero/local-llm-lab/plugins/dsh-preset-standard-light` |
| dsh-recall 0.1.0 | `.local-packages/dsh-recall-0.1.0.tgz` (see §2 — installed files match the other tarball) |
| dsh-cite-sources 0.1.1 | `.local-packages/dsh-cite-sources-0.1.1.tgz` |
| dsh-vision-context | `link:/home/jkzero/local-llm-lab/plugins/dsh-vision-context` |
| dsh-image-pricing | `link:/home/jkzero/local-llm-lab/plugins/dsh-image-pricing` |

`dsh-auto-memory` is **not** in the profile (evidence: the `package.json` above; `node_modules` listing).

## 2. dsh-recall: installed version, active config, auto-recall

- **Installed version:** `0.1.0` (`node_modules/dsh-recall/package.json`). `.local-packages/` holds two tarballs, `dsh-recall-0.1.0.tgz` and `dsh-recall-0.1.0+ce2df4a.tgz`; the installed `lib/index.js` matches the **+ce2df4a** build by sha256, not the one `package.json` points at:

  ```
  sha256sum .../node_modules/dsh-recall/lib/index.js  → 0a6aad1b...9e704
  sha256sum .../dsh-recall-0.1.0.tgz (extracted)      → db3e0d82...e573b448
  sha256sum .../dsh-recall-0.1.0+ce2df4a.tgz (extracted) → 0a6aad1b...9e704
  ```
  The only difference between the two builds is the `capExemptTools` support (diff: 21 lines; ce2df4a adds the `exempt` parameter to `capSession` and `const capExempt = new Set(config?.capExemptTools ?? [])`).

- **Config schema (code):** the plugin reads exactly one config key: `capExemptTools` (array of tool names, default `[]`) — `lib/index.js:116`. No other config key exists in the code.

- **Active config (defaults + `cordis.patch.yml`):** the profile patch (`cordis.patch.yml` lines 132–134) sets
  `- id: recall / config: capExemptTools: [ web_fetch ]`.
  So active: **capExemptTools = [web_fetch]**; everything else at defaults. The bundle's own patch (`node_modules/dsh-recall/cordis.patch.yml`) only inserts the `recall` row.

- **Auto-recall injection: ON, with no config switch.** There is no "auto-recall" toggle in the installed code. The automatic behaviour is `capSession`, hooked unconditionally on `agent/pre-step` (`lib/index.js:303–309`): on every step, any tool result over 8192 chars is capped to 4096 head + 1024 tail chars, the marker
  `[... N characters removed. call recall with startSeq=S endSeq=S for the full original ...]`
  is inserted into the visible result, a `compaction/prune` event is appended, and the capped result is appended as a surface replacement. It runs unless a tool is in `capExemptTools` (here: web_fetch). It cannot be turned off via config; the only levers are exempting more tools or removing the plugin row.
- Evidence for attribution: `grep -rl "compaction/prune" node_modules/` → only `dsh-recall/lib/index.js`. (`compaction/summary` comes from `dsh-compaction-select`, not dsh-recall.)

## 3. Recall injection in the 3 most recent real sessions

Current session (`--home-jkzero-biblioteca--/session-41334c39-…`) excluded per task. The next-newest log, `--home-jkzero-dsh-scratch--/session-3f0a2d79-…`, contains only 4 setup events (`session`, `permission/preset`, `sandbox/mode`, `approval/policy` — no conversation) and is not a real session, so it is skipped (evidence: full 4-line dump, see §4).

| Session (workspace `--home-jkzero-dsh-scratch--`) | Recall events | Recall chars | Session chars | % |
|---|---:|---:|---:|---:|
| session-13c4f945-4fe2-4e5d-aa01-4edd35b80c36 | 0 | 0 | 129,927 | 0.000% |
| session-4cb73363-a675-4063-abc3-2a6b79e910e8 | 0 | 0 | 479,900 | 0.000% |
| session-907fd2cd-c6bc-4533-a35e-5d3f2151f7b6 | 2 | 5,901 | 2,412,831 | 0.245% |

Breakdown of session 907fd2cd (the only non-zero one), from the script output:

- Automatic (capSession): 1 capped `tool/result` (raw line 5,747 chars; marker invented 98 chars — original tool output was 22,268+ chars) and 1 `compaction/prune` event (raw line 154 chars). `5747 + 154 = 5901` (checked with `python3 -c "print(5747+154)"` → 5901).
- Explicit: 0 `recall` and 0 `recall_index` tool calls in **all three** sessions.

Marker sample (from the script):
`[... 22268 characters removed. call recall with startSeq=447 endSeq=447 for the full original ...]`

**Measurements, reproducible:**

- *session chars* = total Unicode code points of the decompressed `session.v4.jsonl` (UTF-8 decoded, newlines included; per line `len(line)+1`).
- *recall event* = one JSONL line that is any of: (a) `compaction/prune`; (b) a `tool/result` whose top-level `surfaceOp` is `{"op":"replace",…}` and whose text contains the marker above; (c) a `tool/call` with name `recall`/`recall_index`; (d) the `tool/result` of such a call (matched via `sourceEventSeqs` containing the call's seq).
- *recall chars* = sum of code-point lengths of those raw lines (whole JSON lines, so (b) includes the kept head+tail, not only the marker; marker-only chars are reported separately).

## 4. How the table was produced

Script: `src/phase1_recall_stats.py` (streams each file with `zstd -dc` via subprocess; reads nothing else; prints only aggregates + ≤3 marker samples). Run:

```
python3 src/phase1_recall_stats.py \
  ~/.local/share/deepseek-harness/sessions/--home-jkzero-dsh-scratch--/session-13c4f945-4fe2-4e5d-aa01-4edd35b80c36/session.v4.jsonl.zstd \
  ~/.local/share/deepseek-harness/sessions/--home-jkzero-dsh-scratch--/session-4cb73363-a675-4063-abc3-2a6b79e910e8/session.v4.jsonl.zstd \
  ~/.local/share/deepseek-harness/sessions/--home-jkzero-dsh-scratch--/session-907fd2cd-c6bc-4533-a35e-5d3f2151f7b6/session.v4.jsonl.zstd
```

Session selection evidence:

```
find ~/.local/share/deepseek-harness/sessions -name 'session.v4.jsonl.zstd' -printf '%T@ %s %p\n' | sort -rn | head -12
# newest → current session (excluded), then 3f0a2d79 (4 lines, no conversation), then the 3 in the table
```

## 5. dsh-auto-memory compatibility: **compatible with dsh 0.2.2-rc.1 (declared ranges verified; not installed)**

- Installed app version: `0.2.2-rc.1` (`grep X-AppImage-Version ~/Applications/DeepSeek-Harness/deepseek-harness.desktop`).
- No local copy of dsh-auto-memory found: `.local-packages/` contains only the five tarballs listed above; `node_modules/` and `local-llm-lab/plugins/` don't contain it; `find ~ -maxdepth 7 -iname "*auto-memory*"` (excluding `.git`, `node_modules`) returned nothing.
- The profile `compatibility.json` lists supported dsh versions only for `dsh-free-search`, `dsh-recall` (supports 0.2.1-alpha.2, 0.2.1-rc.2, 0.2.1-rc.5, 0.2.2-rc.1 — i.e. compatible with the installed app) and `dsh-cite-sources`; dsh-auto-memory is absent.
- **Checked (network, read-only; nothing installed in the profile):** the npm package `dsh-auto-memory` (unscoped), latest **1.3.0**, MIT, source repo `github.com/AskTheWay/dsh-auto-memory` (npm packument `repository` field; npmjs page and GitHub repo seen via search).
- Its `package.json` declares 8 peerDependencies: `@deepseek-ai/cordis >=4.0.2 <5.0.0` and `@deepseek-ai/{dsh-llm,dsh-agent,dsh-tools,dsh-session,dsh-home-paths,dsh-atomic-write,dsh-system-prompt} >=0.1.5-rc.2 <0.3.0`; engines: `node ^22.19.0 || >=24.0.0`.
- The installed app (`@deepseek-ai/dsh-desktop 0.2.2-rc.1`, read from its `resources/app.asar` header, read-only) bundles all seven `@deepseek-ai/dsh-*` packages above at **0.2.2-rc.1** and cordis **4.1.0** — every peer range is satisfied (0.2.2-rc.1 falls inside `>=0.1.5-rc.2 <0.3.0`; 4.1.0 inside `>=4.0.2 <5.0.0`), and the bundled Node **24.21.0** (`resources/runtime/versions.json`) satisfies the engines field.
- The 1.3.0 tarball (inspected in `/tmp`, not downloaded into the profile) contains **no separate compatibility manifest** — only `package.json`, `cordis.patch.yml`, `lib/` and READMEs — so the `package.json` peerDependencies are the compatibility declaration, the same mechanism that populates the profile `compatibility.json`.
- **Verdict: dsh-auto-memory 1.3.0 declares compatibility with dsh 0.2.2-rc.1 and needs no version exemption** (contrast dsh-recall 0.1.0, whose `^0.1.x` ranges excluded 0.2.x and required the exemption recorded in `.plugin-manager.log`). The plugin remains absent from this profile; this verdict only says installing it from npm would pass the peer-dependency check.
- Note: scoped look-alikes exist — `@a9i5k4/dsh-auto-memory` (v3.3.17) and `@y0shua1ee/dsh-auto-memory` (v0.3.3) — separate packages; this verdict covers the unscoped `dsh-auto-memory` only.
- Sources: npmjs package page and registry packument (`registry.npmjs.org/dsh-auto-memory`), GitHub repo `AskTheWay/dsh-auto-memory`, app.asar and `versions.json` of the installed app.

## 6. Recommendation (a recommendation, not applied)

**Keep dsh-recall as is; do not turn off the automatic capping before biblioteca.**

Measured cost is minimal (0–0.245% of session chars in the 3 most recent real sessions; zero explicit `recall` calls in them) — and the automatic part is not a token *addition* in the ordinary sense: it truncates tool results over 8192 chars to ~5.2 KB, which saves context, with the 98-char marker as the only invented text. The recall/recall_index tools remain available on demand, which does not conflict with biblioteca's pull-based design (biblioteca searches episode cards, dsh-recall restores the same session's own compacted content). If the marker format ever becomes noise for biblioteca, the only in-config lever is `capExemptTools`; disabling the plugin entirely would also remove the `compaction/prune` bookkeeping that `recall_index` relies on.
