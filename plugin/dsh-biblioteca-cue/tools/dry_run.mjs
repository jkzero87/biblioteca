#!/usr/bin/env node
/**
 * Dry run of the cue hook over real Desktop sessions: no model, no writes.
 *
 * For each session log under the sessions root, takes every user-authored message (`user/message` with source
 * kind "user") at or after --since, in order, and runs the same selectCues the hook runs, with habituation carried
 * across that session's messages and the per-turn cap. The index is read once, read-only, from library.db; a card
 * counts only if it started before the message and is not the message's own session.
 *
 * Usage: node tools/dry_run.mjs [--db PATH] [--sessions DIR] [--since 2026-11-08] [--until ISO] [--min-idf 4.0[,4.5,...]]
 *        [--max 2] [--examples N]
 */
import { execFileSync } from "node:child_process";
import { readdirSync, existsSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { buildIndex, cueLine, selectCues } from "../lib/cue.js";

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, a, i, all) => {
  if (a.startsWith("--")) acc.push([a.slice(2), all[i + 1]]);
  return acc;
}, []));
const home = process.env.HOME;
const dbPath = args.db ?? join(home, "biblioteca/data/library.db");
const root = args.sessions ?? join(home, ".local/share/deepseek-harness/sessions");
const since = Date.parse(`${args.since ?? "2026-11-08"}T00:00:00`);
const until = args.until ? Date.parse(args.until) : Infinity;
const thresholds = (args["min-idf"] ?? "4.0").split(",").map(Number);
const maxCues = Number(args.max ?? 2);
const nExamples = Number(args.examples ?? 10);

const db = new DatabaseSync(dbPath, { readOnly: true });
const rows = db.prepare("SELECT f.c0 AS id, f.c1 AS terms, c.start AS start FROM familiar_fts_content f JOIN cards c ON c.id = f.c0").all();
db.close();
const index = buildIndex(rows);

const sessions = [];
for (const dir of readdirSync(root)) {
  for (const sdir of readdirSync(join(root, dir))) {
    const log = join(root, dir, sdir, "session.v4.jsonl.zstd");
    if (!existsSync(log)) continue;
    const raw = execFileSync("zstd", ["-dqc", log], { maxBuffer: 1 << 30 }).toString("utf8");
    const msgs = [];
    let id = sdir;
    for (const line of raw.split("\n")) {
      let e;
      try { e = JSON.parse(line); } catch { continue; } // a live session may end in a partial line
      if (e.type === "session") id = e.id;
      if (e.type !== "user/message" || e.data?.source?.kind !== "user" || !(e.time >= since) || !(e.time < until)) continue;
      const text = (e.data.content ?? []).filter((b) => b?.type === "text").map((b) => b.text).join("\n");
      if (text.trim()) msgs.push({ time: e.time, text });
    }
    if (msgs.length) sessions.push({ id, msgs });
  }
}

for (const minIdf of thresholds) {
  let messages = 0, cued = 0, cues = 0;
  const examples = [];
  for (const s of sessions) {
    const habituated = new Set();
    for (const m of s.msgs) {
      messages += 1;
      const got = selectCues(index, m.text, { minIdf, maxCues, habituated, excludeId: s.id, nowMs: m.time });
      if (!got.length) continue;
      cued += 1;
      cues += got.length;
      for (const c of got) habituated.add(c.term);
      examples.push({ session: s.id, time: new Date(m.time).toISOString(), msg: m.text.slice(0, 140).replace(/\s+/g, " "), got });
    }
  }
  const dfMax = Math.floor(index.n / Math.exp(minIdf));
  console.log(`minIdf=${minIdf} (df<=${dfMax} of ${index.n} cards) messages=${messages} sessions=${sessions.length} `
    + `messages_with_cue=${cued} cues=${cues} cues_per_message=${(cues / messages).toFixed(3)}`);
  if (nExamples > 0 && minIdf === thresholds[thresholds.length - 1]) {
    for (const e of examples.slice(0, nExamples)) {
      console.log(`  ${e.session.slice(8, 16)} ${e.time} ${JSON.stringify(e.msg)}`);
      for (const c of e.got) console.log(`     ${c.score.toFixed(2)} ${cueLine(c)}`);
    }
  }
}
