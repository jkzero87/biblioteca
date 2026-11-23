import { test } from "node:test";
import assert from "node:assert/strict";
import {
  CUE_SOURCE_KIND, buildIndex, cueLine, cueSource, cueText, eligible, habituatedTerms, idf, selectCues, tokenize,
  userText,
} from "../lib/cue.js";

const rows = [
  { id: "session-aaaaaaaa-0000", terms: "cd ~/rust-lab && cargo build -p imgproxy\n/home/jkzero/rust-lab/imgproxy/src/convert.rs", start: "2026-10-23T20:39:51" },
  { id: "session-bbbbbbbb-0000", terms: "adb shell am start gamenative\nadb logcat", start: "2026-11-07T02:02:26" },
  { id: "session-cccccccc-0000", terms: "adb devices\npython3 bench/belebele.py", start: "2026-10-20T21:59:36" },
  { id: "session-dddddddd-0000", terms: "python3 bench/belebele.py --label base9b", start: "2026-10-21T00:00:25" },
];
const index = buildIndex(rows);

test("tokenize folds case and accents like FTS5 unicode61", () => {
  assert.deepEqual(tokenize("Sesión GameNative/convert.rs ÁRBOL_x"), ["sesion", "gamenative", "convert", "rs", "arbol", "x"]);
});

test("eligible drops short, numeric and stop tokens", () => {
  assert.equal(eligible("adb"), false);
  assert.equal(eligible("2026"), false);
  assert.equal(eligible("archivo"), false);
  assert.equal(eligible("imgproxy"), true);
});

test("idf is ln(cards/df) and 0 for unknown tokens", () => {
  assert.equal(idf(index, "imgproxy"), Math.log(4 / 1));
  assert.equal(idf(index, "belebele"), Math.log(4 / 2));
  assert.equal(idf(index, "nothingstored"), 0);
});

test("crossing: a rare stored term cues the newest card that holds it", () => {
  const cues = selectCues(index, "Sigue con belebele y GameNative", { minIdf: 0.5, maxCues: 5 });
  assert.deepEqual(cues.map((c) => [c.term, c.cardId]), [
    ["gamenative", "session-bbbbbbbb-0000"],
    ["belebele", "session-dddddddd-0000"],
  ]);
});

test("crossing: terms below the threshold never cue", () => {
  assert.deepEqual(selectCues(index, "belebele", { minIdf: 1.0, maxCues: 2 }), []);
  assert.equal(selectCues(index, "belebele", { minIdf: 0.6, maxCues: 2 }).length, 1);
});

test("crossing: the current session's own card and cards started later are skipped", () => {
  assert.deepEqual(selectCues(index, "gamenative", { minIdf: 0.5, maxCues: 2, excludeId: "session-bbbbbbbb-0000" }), []);
  const before = Date.parse("2026-10-21T00:00:00Z");
  assert.equal(selectCues(index, "belebele", { minIdf: 0.5, maxCues: 2, nowMs: before })[0].cardId, "session-cccccccc-0000");
});

test("habituation: a term cued earlier in the session is not cued again", () => {
  const logged = [
    { type: "user/message", data: { source: { kind: "user" }, content: [{ type: "text", text: "imgproxy" }] } },
    { type: "user/message", data: { source: cueSource([{ term: "imgproxy", score: 1.386, cardId: "session-aaaaaaaa-0000", cardStartMs: 0 }]) } },
  ];
  const habituated = habituatedTerms(logged);
  assert.deepEqual([...habituated], ["imgproxy"]);
  assert.deepEqual(selectCues(index, "imgproxy again", { minIdf: 0.5, maxCues: 2, habituated }), []);
});

test("cap: at most maxCues cues per turn, best first, one per card", () => {
  const text = "imgproxy convert gamenative logcat base9b belebele";
  const cues = selectCues(index, text, { minIdf: 0.5, maxCues: 2 });
  assert.equal(cues.length, 2);
  assert.ok(cues[0].score >= cues[1].score);
  assert.notEqual(cues[0].cardId, cues[1].cardId);
  // Four cards hold these terms, but belebele's newest card is base9b's, so one-per-card leaves three.
  assert.deepEqual(selectCues(index, text, { minIdf: 0.5, maxCues: 10 }).map((c) => c.cardId).sort(),
    ["session-aaaaaaaa-0000", "session-bbbbbbbb-0000", "session-dddddddd-0000"]);
});

test("userText reads only user-authored text blocks", () => {
  const messages = [
    { role: "user", source: { kind: "user" }, content: [{ type: "text", text: "hola" }, { type: "image" }] },
    { role: "user", source: { kind: "time-context" }, content: [{ type: "text", text: "Time sampled" }] },
  ];
  assert.equal(userText(messages), "hola");
});

test("cue text and logged source follow the approved format", () => {
  const cue = { term: "gamenative", score: 1.3862943, cardId: "session-bbbbbbbb-0000", cardStartMs: Date.parse("2026-11-07T02:02:26Z") };
  assert.match(cueLine(cue), /^Biblioteca: tienes notas sobre "gamenative" \(sesión session-bbbbbbbb-0000, 2026-11-0[67]\)\. Ábrelas con library_open\("session-bbbbbbbb-0000"\) si aplican\.$/);
  assert.equal(cueText([cue, cue]).split("\n").length, 2);
  const source = cueSource([cue]);
  assert.equal(source.kind, CUE_SOURCE_KIND);
  assert.equal(source.form, "notice");
  assert.deepEqual(source.cues, [{ term: "gamenative", score: 1.386, cardId: "session-bbbbbbbb-0000", cardStart: "2026-11-07T02:02:26.000Z" }]);
  assert.ok(source.summary.length <= 120);
});
