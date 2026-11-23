/**
 * dsh-biblioteca-cue: on the first step of every turn, cross the user's message with the biblioteca familiarity
 * index and append up to `maxCuesPerTurn` one-line cues to that step's messages, as dsh-time-context appends its
 * clock reading. The cue is a logged `user/message` whose source kind is `biblioteca-cue`; its `cues` field records
 * term, IDF score and card id for measurement. The system prompt is never touched, and the model is never called.
 *
 * The index is read once at load and again whenever library.db changes (size, mtime or inode), from a read-only
 * SQLite connection. A term cued once in a session is not cued again in it (habituation, rebuilt from the log).
 */
import { statSync } from "node:fs";
import { isAbsolute } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { createUserMessage } from "@deepseek-ai/dsh-llm";
import { buildIndex, cueSource, cueText, habituatedTerms, selectCues, userText } from "./cue.js";

const name = "biblioteca-cue";

/**
 * Validate the plugin config; misconfiguration fails at load.
 * @param {object} config - the row config.
 * @returns {{dbPath: string, minIdf: number, maxCuesPerTurn: number}} the resolved config.
 */
export function resolveConfig(config) {
  const dbPath = config?.dbPath;
  if (typeof dbPath !== "string" || !isAbsolute(dbPath)) {
    throw new Error("dsh-biblioteca-cue: config.dbPath must be an absolute path to library.db");
  }
  const minIdf = config?.minIdf;
  if (typeof minIdf !== "number" || !Number.isFinite(minIdf) || minIdf <= 0) {
    throw new Error("dsh-biblioteca-cue: config.minIdf must be a positive number");
  }
  const maxCuesPerTurn = config?.maxCuesPerTurn ?? 2;
  if (!Number.isInteger(maxCuesPerTurn) || maxCuesPerTurn < 1) {
    throw new Error("dsh-biblioteca-cue: config.maxCuesPerTurn must be a positive integer");
  }
  return { dbPath, minIdf, maxCuesPerTurn };
}

/**
 * Read every card's familiarity terms and start from library.db over a read-only connection.
 * @param {string} dbPath - absolute path to library.db.
 * @returns {{id: string, terms: string, start: string | null}[]} one row per card.
 */
export function readRows(dbPath) {
  const db = new DatabaseSync(dbPath, { readOnly: true });
  try {
    return db.prepare(
      "SELECT f.c0 AS id, f.c1 AS terms, c.start AS start FROM familiar_fts_content f JOIN cards c ON c.id = f.c0",
    ).all();
  } finally {
    db.close();
  }
}

/** Identity of the database file for change detection. */
function fileKey(dbPath) {
  const s = statSync(dbPath);
  return `${s.size}:${s.mtimeMs}:${s.ino}`;
}

/**
 * The logged message for a set of cues.
 * @param {{term: string, score: number, cardId: string, cardStartMs: number}[]} cues - selected cues, at least one.
 * @returns {object} a user message with the cue lines and the `biblioteca-cue` notice source.
 */
function cueMessage(cues) {
  return createUserMessage({ content: [{ type: "text", text: cueText(cues) }], source: cueSource(cues) });
}

function apply(ctx, config) {
  const options = resolveConfig(config);
  let key = fileKey(options.dbPath);
  let index = buildIndex(readRows(options.dbPath));
  ctx.logger.info(`biblioteca-cue: index loaded, ${index.n} cards, ${index.df.size} terms, minIdf ${options.minIdf}`);

  /** Reload when library.db changed; a failed reload keeps the previous index. */
  function currentIndex() {
    try {
      const now = fileKey(options.dbPath);
      if (now !== key) {
        index = buildIndex(readRows(options.dbPath));
        key = now;
        ctx.logger.info(`biblioteca-cue: index reloaded, ${index.n} cards`);
      }
    } catch (error) {
      ctx.logger.warn(`biblioteca-cue: index reload failed (${error.message}); keeping ${index.n} cards`);
    }
    return index;
  }

  ctx.on("agent/pre-step", async ({ agent, step, signal }, next) => {
    const decision = await next();
    if (decision.kind === "reject" || signal.aborted || step !== 1) return decision;
    const text = userText(decision.messages);
    if (text === "") return decision;
    const cues = selectCues(currentIndex(), text, {
      minIdf: options.minIdf,
      maxCues: options.maxCuesPerTurn,
      habituated: habituatedTerms(agent.session.snapshotEvents()),
      excludeId: agent.session.id,
      nowMs: Date.now(),
    });
    if (cues.length === 0) return decision;
    ctx.logger.info(`biblioteca-cue: ${agent.session.id} ${cues.map((c) => `${c.term}=${c.score.toFixed(2)}@${c.cardId}`).join(" ")}`);
    return { ...decision, messages: [...decision.messages, cueMessage(cues)] };
  });
}

export { name, apply };
