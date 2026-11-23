/**
 * Pure cue logic for dsh-biblioteca-cue: tokenization, the IDF index over the library's familiarity terms, cue
 * selection with habituation and a per-turn cap, and the in-turn cue text. No harness or SQLite imports, so the
 * tests and tools/dry_run.mjs exercise exactly what the hook runs.
 */

/** Tokens shorter than this never cue. */
export const MIN_TOKEN_LENGTH = 4;

/**
 * Words that never cue even when rare in the index. The index holds tool arguments (paths, commands), so prose
 * words that appear in one stored path would otherwise score as rare.
 */
export const STOP_WORDS = new Set(`
this that with from have what when then them they your there their will would should could about into also just
only some more most other than such very each make made does done here were been being which while where para
como pero este esta esto estos estas porque cuando donde desde hasta sobre entre todo todos toda todas solo tambien
tiene tienen hace hacer puede pueden debe deben cada otro otra otros otras mismo misma algo nada mucho poco antes
despues ahora aqui alli bien usar usa usando sesion archivo archivos favor gracias como cual home jkzero file files
line lines true false none null name test tests error errors code read write edit bash step steps turn turns task
tasks output input result results check run runs using used need next last first
`.split(/\s+/).filter(Boolean));

/**
 * Split text the way SQLite FTS5's default unicode61 tokenizer does: NFKD with combining marks removed, lowercase,
 * maximal runs of Unicode letters and digits.
 * @param {string} text - any text.
 * @returns {string[]} tokens in order, duplicates kept.
 */
export function tokenize(text) {
  const folded = String(text).normalize("NFKD").replace(/\p{M}+/gu, "").toLowerCase();
  return folded.match(/[\p{L}\p{N}]+/gu) ?? [];
}

/**
 * Whether a token may ever cue, before rarity is considered.
 * @param {string} token - one token from {@link tokenize}.
 * @returns {boolean} true for tokens of at least {@link MIN_TOKEN_LENGTH} chars that are not digits or stop words.
 */
export function eligible(token) {
  return token.length >= MIN_TOKEN_LENGTH && !/^\p{N}+$/u.test(token) && !STOP_WORDS.has(token);
}

/**
 * Build the in-memory cue index.
 * @param {{id: string, terms: string, start: string | null}[]} rows - one row per library card: the session id,
 *   its familiar_fts terms_text, and the card start (ISO, UTC without a zone, as the library stores it).
 * @returns {{n: number, df: Map<string, number>, cards: Map<string, {id: string, startMs: number}[]>}} the card count,
 *   per-token document frequency, and per-token cards newest first.
 */
export function buildIndex(rows) {
  const df = new Map();
  const cards = new Map();
  for (const row of rows) {
    const startMs = row.start ? Date.parse(/[zZ]|[+-]\d\d:?\d\d$/.test(row.start) ? row.start : `${row.start}Z`) : 0;
    for (const token of new Set(tokenize(row.terms ?? "").filter(eligible))) {
      df.set(token, (df.get(token) ?? 0) + 1);
      const list = cards.get(token);
      if (list) list.push({ id: row.id, startMs });
      else cards.set(token, [{ id: row.id, startMs }]);
    }
  }
  for (const list of cards.values()) list.sort((a, b) => b.startMs - a.startMs || (a.id < b.id ? -1 : 1));
  return { n: rows.length, df, cards };
}

/**
 * Inverse document frequency of a token over the index: ln(n / df); 0 for a token no card holds.
 * @param {ReturnType<typeof buildIndex>} index - the cue index.
 * @param {string} token - one token.
 * @returns {number} the token's IDF.
 */
export function idf(index, token) {
  const df = index.df.get(token);
  return df === undefined || index.n === 0 ? 0 : Math.log(index.n / df);
}

/**
 * Choose the cues for one user message.
 * Every distinct eligible token whose IDF reaches `minIdf` and that is not habituated is a candidate; its card is
 * the newest card holding it other than `excludeId` and started before `nowMs`. Candidates are ranked by IDF
 * (ties: newer card, then token), and at most `maxCues` are returned, one per card.
 * @param {ReturnType<typeof buildIndex>} index - the cue index.
 * @param {string} text - the user's message text.
 * @param {{minIdf: number, maxCues: number, habituated?: Set<string>, excludeId?: string, nowMs?: number}} options -
 *   threshold, per-turn cap, terms already cued in this session, the current session id, and the message time.
 * @returns {{term: string, score: number, cardId: string, cardStartMs: number}[]} the cues, best first.
 */
export function selectCues(index, text, { minIdf, maxCues, habituated = new Set(), excludeId, nowMs = Infinity }) {
  const candidates = [];
  for (const token of new Set(tokenize(text).filter(eligible))) {
    if (habituated.has(token)) continue;
    const score = idf(index, token);
    if (score < minIdf) continue;
    const card = (index.cards.get(token) ?? []).find((c) => c.id !== excludeId && c.startMs < nowMs);
    if (card) candidates.push({ term: token, score, cardId: card.id, cardStartMs: card.startMs });
  }
  candidates.sort((a, b) => b.score - a.score || b.cardStartMs - a.cardStartMs || (a.term < b.term ? -1 : 1));
  const cues = [];
  const usedCards = new Set();
  for (const cue of candidates) {
    if (cues.length >= maxCues) break;
    if (usedCards.has(cue.cardId)) continue;
    usedCards.add(cue.cardId);
    cues.push(cue);
  }
  return cues;
}

/**
 * Local calendar date of a card start.
 * @param {number} ms - epoch milliseconds.
 * @returns {string} YYYY-MM-DD in the process time zone.
 */
export function localDate(ms) {
  const d = new Date(ms);
  const pad = (v) => String(v).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/**
 * The model-facing text of one cue, in the approved wording.
 * @param {{term: string, cardId: string, cardStartMs: number}} cue - one selected cue.
 * @returns {string} one line.
 */
export function cueLine(cue) {
  return `Biblioteca: tienes notas sobre "${cue.term}" (sesión ${cue.cardId}, ${localDate(cue.cardStartMs)}). `
    + `Ábrelas con library_open("${cue.cardId}") si aplican.`;
}

/** Source kind of the injected messages; also the key habituation reads back from the log. */
export const CUE_SOURCE_KIND = "biblioteca-cue";

/**
 * Text of the user-authored messages among a step's proposed messages.
 * @param {readonly object[]} messages - the pre-step decision's messages.
 * @returns {string} their text blocks joined, or "" when the step carries no user-authored message.
 */
export function userText(messages) {
  return messages
    .filter((m) => m?.role === "user" && m.source?.kind === "user")
    .flatMap((m) => (Array.isArray(m.content) ? m.content : []))
    .filter((block) => block?.type === "text" && typeof block.text === "string")
    .map((block) => block.text)
    .join("\n");
}

/**
 * Terms already cued in a session, read from its logged cue messages.
 * @param {Iterable<object>} events - the session's logged events.
 * @returns {Set<string>} cued terms.
 */
export function habituatedTerms(events) {
  const terms = new Set();
  for (const event of events) {
    if (event?.type !== "user/message" || event.data?.source?.kind !== CUE_SOURCE_KIND) continue;
    for (const cue of event.data.source.cues ?? []) if (typeof cue?.term === "string") terms.add(cue.term);
  }
  return terms;
}

/**
 * Model-facing text of one turn's cues: one {@link cueLine} per cue.
 * @param {{term: string, cardId: string, cardStartMs: number}[]} cues - selected cues.
 * @returns {string} the lines joined by newlines.
 */
export function cueText(cues) {
  return cues.map(cueLine).join("\n");
}

/**
 * Logged source of one turn's cue message: a `notice` with a one-line summary (at most 120 chars) and one
 * record per cue, which measurement scripts and habituation read back.
 * @param {{term: string, score: number, cardId: string, cardStartMs: number}[]} cues - selected cues.
 * @returns {{kind: string, form: "notice", summary: string, cues: {term: string, score: number, cardId: string, cardStart: string}[]}} the source.
 */
export function cueSource(cues) {
  const summary = `biblioteca: ${cues.map((c) => `"${c.term}"`).join(", ")}`;
  return {
    kind: CUE_SOURCE_KIND,
    form: "notice",
    summary: summary.length <= 120 ? summary : `${summary.slice(0, 119)}…`,
    cues: cues.map((c) => ({
      term: c.term,
      score: Math.round(c.score * 1000) / 1000,
      cardId: c.cardId,
      cardStart: new Date(c.cardStartMs).toISOString(),
    })),
  };
}
