// Pure, shared PDF-tool logic used by BOTH the hosted site and the desktop app so
// they never drift: input validation, live match counting, rules-file parsing, and
// friendly failure messages. No hosting, payment, or code-signing lives here (those
// are commercial and stay in the host). Uses only standard browser/Node/Worker APIs.

export function escapeHtml(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

export const ACTIONS = ["replace", "redact", "remove", "meta", "scrub", "anonymize", "stripmeta",
  "watermark", "number", "bates", "stamp", "pages", "rotate", "crop", "clean", "protect", "optimize"];
// edit actions that take no "find" (marks, page ops, protect, optimize)
export const NOFIND_ACTIONS = ["watermark", "number", "bates", "stamp", "pages", "rotate", "crop", "clean", "protect", "optimize"];
export const META_FIELDS = ["Title", "Author", "Subject", "Keywords", "Creator", "Producer"];
export const DETECTOR_TYPES = ["email", "iban", "credit_card", "ssn", "phone", "date", "amount"];

export function validateRules(rules, maxRules = 50) {
  if (!Array.isArray(rules) || rules.length === 0) return { ok: false, code: "no_rules", error: "add at least one rule" };
  if (rules.length > maxRules) return { ok: false, code: "too_many_rules", error: `max ${maxRules} rules` };
  for (const r of rules) {
    if (typeof r !== "object" || r === null) return { ok: false, code: "bad_rules", error: "a rule is malformed" };
    const action = r.action == null ? "replace" : r.action;
    if (!ACTIONS.includes(action)) return { ok: false, code: "bad_rules", error: "a rule has an unknown action" };
    if (action === "meta") {
      if (!META_FIELDS.includes(r.metaField)) return { ok: false, code: "bad_rules", error: "a metadata rule has an unknown field" };
      if (r.metaValue != null && (typeof r.metaValue !== "string" || r.metaValue.length > 200)) return { ok: false, code: "bad_rules", error: "a metadata value is too long" };
      continue;
    }
    if (action === "stripmeta") continue;
    if (NOFIND_ACTIONS.includes(action)) continue;
    if (action === "scrub" || action === "anonymize") {
      if (r.types != null && (!Array.isArray(r.types) || r.types.some((t) => !DETECTOR_TYPES.includes(t))))
        return { ok: false, code: "bad_rules", error: "an unknown data type was requested" };
      if (action === "anonymize" && r.names != null && (!Array.isArray(r.names) || r.names.some((n) => typeof n !== "string" || n.length > 200)))
        return { ok: false, code: "bad_rules", error: "a name to anonymize is invalid" };
      continue;
    }
    if (typeof r.find !== "string" || !r.find.trim()) return { ok: false, code: "bad_rules", error: "a rule is missing find text" };
    if (r.find.length > 500 || (r.replace != null && (typeof r.replace !== "string" || r.replace.length > 200)))
      return { ok: false, code: "bad_rules", error: "a rule value is too long" };
    const sc = r.scope;
    if (sc != null && sc !== "first" && sc !== "all" && !(Number.isInteger(sc) && sc >= 1 && sc <= 10000))
      return { ok: false, code: "bad_rules", error: "a rule has an invalid scope" };
  }
  return { ok: true };
}

// Count matches of `find` in `text` with the same semantics as the engine (literal by
// default, or regex; optional ignore-case and whole-word). Returns the count, or -1 for
// an invalid regular expression. Powers the live match-count hint on already-extracted
// text (no server round trip).
export function countMatches(text, find, opts = {}) {
  if (!find || !text) return 0;
  let pat = opts.regex ? find : find.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  if (opts.word) pat = "(?<![0-9A-Za-z])" + pat + "(?![0-9A-Za-z])";
  let rx;
  try { rx = new RegExp(pat, opts.ci ? "gi" : "g"); } catch (_) { return -1; }
  let n = 0;
  for (let m; (m = rx.exec(text)); ) { n++; if (m.index === rx.lastIndex) rx.lastIndex++; }
  return n;
}

// FLAGS field (space-separated, optional): all | first | <N> | ci | word | pN
export function parseFlags(s) {
  const out = { scope: "first", ci: false, word: false };
  for (const f of String(s).trim().toLowerCase().split(/\s+/)) {
    if (!f) continue;
    if (f === "all") out.scope = "all";
    else if (f === "first") out.scope = "first";
    else if (/^\d+$/.test(f)) out.scope = parseInt(f, 10);
    else if (f === "ci") out.ci = true;
    else if (f === "word") out.word = true;
    else if (/^p\d+$/.test(f)) out.page = parseInt(f.slice(1), 10);
  }
  return out;
}

// Rules file: "FIND | REPLACE | FLAGS" per line (REPLACE + FLAGS optional).
export function parseRulesFile(text, maxRules = 50) {
  const rules = [];
  for (const raw of String(text).split(/\r?\n/)) {
    const t = raw.trim();
    if (!t || t.startsWith("#")) continue;
    const parts = t.split("|");
    const find = (parts[0] || "").trim();
    const replace = (parts[1] || "").trim();
    if (!find) continue;
    if (find.length > 500 || replace.length > 200) return { ok: false, error: "a rule is too long" };
    rules.push({ find, replace, ...parseFlags(parts[2] || "") });
    if (rules.length > maxRules) return { ok: false, error: `too many rules (max ${maxRules})` };
  }
  if (!rules.length) return { ok: false, error: "no valid rules found in that file" };
  return { ok: true, rules };
}

// map an engine per-rule failure to plain language for the customer
export function friendlyRuleReason(rule) {
  if (rule.applied) return "";
  const err = String(rule.error || rule.reason || "");
  if (rule.refused || /standard-14|non-embedded|custom encoding|font/i.test(err))
    return "uses a font we can't reproduce, so we left it unchanged";
  if (/editable Tj|split|encoded run/i.test(err))
    return "stored in a way we can't safely rewrite";
  if (/not found/i.test(err))
    return "not found (check spelling, spacing, and capitalization)";
  return err || "couldn't be applied";
}

// "23h 41m" / "9m 12s" / "expired" from milliseconds remaining
export function formatCountdown(ms) {
  if (ms <= 0) return "expired";
  const s = Math.floor(ms / 1000);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${sec}s`;
  return `${sec}s`;
}
