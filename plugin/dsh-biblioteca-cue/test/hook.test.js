// @deepseek-ai/dsh-llm resolves to test/stub-dsh-llm (devDependency); run `npm install` once before `npm test`.
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";

import { apply, resolveConfig } from "../lib/index.js";

function writeDb(path, cards) {
  const db = new DatabaseSync(path);
  db.exec("CREATE TABLE IF NOT EXISTS cards (id TEXT PRIMARY KEY, start TEXT)");
  db.exec("CREATE TABLE IF NOT EXISTS familiar_fts_content (id INTEGER PRIMARY KEY, c0, c1)");
  db.exec("DELETE FROM cards; DELETE FROM familiar_fts_content;");
  for (const [id, terms, start] of cards) {
    db.prepare("INSERT INTO cards VALUES (?, ?)").run(id, start);
    db.prepare("INSERT INTO familiar_fts_content (c0, c1) VALUES (?, ?)").run(id, terms);
  }
  db.close();
}

function harness(config) {
  const listeners = {};
  const logs = [];
  const ctx = {
    on: (event, fn) => { listeners[event] = fn; },
    logger: { info: (m) => logs.push(m), warn: (m) => logs.push(`WARN ${m}`) },
  };
  apply(ctx, config);
  return { preStep: listeners["agent/pre-step"], logs };
}

const userMessage = (text) => ({ role: "user", source: { kind: "user" }, content: [{ type: "text", text }] });
const enter = (messages) => async () => ({ kind: "enter", messages });

test("config fails loud on a relative path, a bad threshold or a bad cap", () => {
  assert.throws(() => resolveConfig({ dbPath: "library.db", minIdf: 1 }), /absolute/);
  assert.throws(() => resolveConfig({ dbPath: "/x.db", minIdf: 0 }), /minIdf/);
  assert.throws(() => resolveConfig({ dbPath: "/x.db", minIdf: 1, maxCuesPerTurn: 0 }), /maxCuesPerTurn/);
  assert.equal(resolveConfig({ dbPath: "/x.db", minIdf: 1 }).maxCuesPerTurn, 2);
});

test("hook: appends one logged cue message on step 1, nothing on later steps, then reloads a changed db", async () => {
  const dir = mkdtempSync(join(tmpdir(), "cue-"));
  try {
    const db = join(dir, "library.db");
    writeDb(db, [
      ["session-a", "cargo build -p imgproxy", "2026-10-23T20:39:51"],
      ["session-b", "adb shell gamenative", "2026-11-07T02:02:26"],
      ["session-c", "ls", "2026-11-07T02:02:26"],
    ]);
    const { preStep, logs } = harness({ dbPath: db, minIdf: 1.0 });
    const agent = { session: { id: "session-now", snapshotEvents: () => [] } };
    const signal = new AbortController().signal;

    const out = await preStep({ agent, step: 1, signal }, enter([userMessage("arregla imgproxy y gamenative")]));
    assert.equal(out.messages.length, 2);
    const cue = out.messages[1];
    assert.equal(cue.source.kind, "biblioteca-cue");
    assert.deepEqual(cue.source.cues.map((c) => c.term).sort(), ["gamenative", "imgproxy"]);
    assert.match(cue.content[0].text, /library_open\("session-b"\)/);

    const later = await preStep({ agent, step: 2, signal }, enter([userMessage("imgproxy")]));
    assert.equal(later.messages.length, 1);

    const habituated = { session: { id: "session-now", snapshotEvents: () => [{ type: "user/message", data: cue }] } };
    const again = await preStep({ agent: habituated, step: 1, signal }, enter([userMessage("imgproxy gamenative")]));
    assert.equal(again.messages.length, 1);

    writeDb(db, [["session-d", "pytest jira", "2026-11-18T19:00:00"], ["session-e", "ls", "2026-11-18T19:00:00"], ["session-f", "ls", "2026-11-18T19:00:00"]]);
    const reloaded = await preStep({ agent, step: 1, signal }, enter([userMessage("pytest")]));
    assert.equal(reloaded.messages[1].source.cues[0].cardId, "session-d");
    assert.ok(logs.some((l) => l.includes("index reloaded")));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("hook: rejected decisions and steps without user text pass through unchanged", async () => {
  const dir = mkdtempSync(join(tmpdir(), "cue-"));
  try {
    const db = join(dir, "library.db");
    writeDb(db, [["session-a", "imgproxy", "2026-10-23T20:39:51"], ["session-b", "ls", "2026-10-23T20:39:51"]]);
    const { preStep } = harness({ dbPath: db, minIdf: 0.5 });
    const agent = { session: { id: "x", snapshotEvents: () => [] } };
    const signal = new AbortController().signal;
    assert.deepEqual(await preStep({ agent, step: 1, signal }, async () => ({ kind: "reject" })), { kind: "reject" });
    const injected = { role: "user", source: { kind: "time-context" }, content: [{ type: "text", text: "imgproxy" }] };
    assert.equal((await preStep({ agent, step: 1, signal }, enter([injected]))).messages.length, 1);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
