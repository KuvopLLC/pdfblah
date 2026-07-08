// The shared pdfblah PDF tool: step 1 (upload + analyze a PDF) and step 2 (the
// action-rule editor with live match counts). Built as a self-contained controller so
// the hosted site and the desktop app share ONE implementation and never drift.
//
//   import { mountTool } from ".../tool.js";
//   const tool = mountTool(rootEl, host);
//
// The host adapter supplies everything that differs between hosted and local:
//   host.analyze(file, {onProgress, onUploaded}) -> Promise<analysisJson>
//   host.onChange({file, analysis, ready, rules})   // enable/disable the host's run button
//   host.maxBytes, host.maxPages, host.maxRules     // client-side limits (optional)
//   host.sampleRulesUrl                             // optional "download sample" link
// Nothing about payment, access codes, preview watermarking, or downloads lives here.
import {
  escapeHtml as esc, parseRulesFile, META_FIELDS, countMatches,
} from "./gate.mjs";

const SCOPES = [["first", "First"], ["all", "All"], ["2", "2nd"], ["3", "3rd"], ["4", "4th"], ["5", "5th"]];
const CHECK_LABELS = [["text", "Text layer"], ["encryption", "Encryption"], ["fonts", "Fonts"], ["metadata", "Metadata"]];
const ACTION_OPTS = [["replace", "Replace"], ["redact", "Redact"], ["remove", "Remove"], ["meta", "Metadata"]];
const ACTION_COLORS = { replace: "#ff6a3e", redact: "#1a1a1a", remove: "#ab4729", meta: "#4e4b66" };
const PRESET_LABELS = {
  scrub: ["Scrub PII", "removes emails, cards, IBANs, SSNs, and phone numbers"],
  anonymize: ["Anonymize", "replaces detected data with realistic fakes"],
  stripmeta: ["Strip all metadata", "removes author, dates, producer, and the rest"],
};

function fmtSize(n) { return n >= 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " KB"; }
function fmtDate(v) { const m = /^D:(\d{4})(\d{2})(\d{2})(\d{2})?(\d{2})?/.exec(v || ""); return m ? `${m[1]}-${m[2]}-${m[3]}${m[4] ? ` ${m[4]}:${m[5] || "00"}` : ""}` : (v || ""); }

export function mountTool(root, host = {}) {
  const maxBytes = host.maxBytes || 20 * 1024 * 1024;
  const maxPages = host.maxPages || 30;
  const maxRules = host.maxRules || 50;
  let pdfFile = null, analysis = null;

  root.classList.add("pb-tool");
  root.innerHTML = `
    <div class="card">
      <label>1. Your PDF</label>
      <div class="pb-pdfPanel"></div>
      <input type="file" class="pb-file" accept="application/pdf" hidden>
    </div>
    <div class="card gated locked pb-step2">
      <label>2. What to change</label>
      <div class="pb-rules"></div>
      <div style="margin-top:12px; display:flex; gap:8px; flex-wrap:wrap; align-items:center">
        <button class="ghost pb-addrow">+ add rule</button>
        <span class="note" style="margin:0 2px">or one-click:</span>
        <button class="ghost small pb-preset" data-preset="scrub">Scrub PII</button>
        <button class="ghost small pb-preset" data-preset="anonymize">Anonymize</button>
        <button class="ghost small pb-preset" data-preset="stripmeta">Strip metadata</button>
      </div>
      <div class="hints">
        <span class="hint"><b class="chip">Scope</b> first / all / Nth match</span>
        <span class="hint"><b class="chip">Aa</b> ignore case</span>
        <span class="hint"><b class="chip">W</b> whole word</span>
        <span class="hint"><b class="chip">.*</b> regular expression</span>
        <span class="hint">fonts, spacing, and alignment are matched automatically</span>
      </div>
      <div class="row" style="margin-top:16px; gap:10px">
        <button class="ghost small pb-uploadRules">Upload a rules file</button>
        ${host.sampleRulesUrl ? `<a class="note" href="${esc(host.sampleRulesUrl)}" download>Download sample</a>` : ""}
        <input type="file" class="pb-rulesFile" accept=".txt,.csv,text/plain" hidden>
      </div>
      <div class="pb-rulesErr note bad" style="margin-top:8px"></div>
    </div>`;

  const $ = (s) => root.querySelector(s);
  const panel = $(".pb-pdfPanel");
  const rulesEl = $(".pb-rules");

  // ---------- readiness ----------
  function ruleObjs() {
    return [...rulesEl.querySelectorAll(".rule")].map((row) => {
      const r = readRow(row);
      if (r.action === "stripmeta") return { action: "stripmeta" };
      if (r.action === "scrub") return { action: "scrub" };
      if (r.action === "anonymize") return { action: "anonymize" };
      if (r.action === "meta") return { action: "meta", metaField: r.metaField, metaValue: r.metaValue };
      return {
        action: r.action, find: (r.find || "").trim(), replace: r.replace || "",
        scope: r.scope, ci: r.ci, word: r.word, regex: r.regex,
      };
    }).filter((r) => (PRESET_LABELS[r.action] ? true : (r.action === "meta" ? !!r.metaField : !!r.find)));
  }
  function isReady() { return !!(pdfFile && analysis && analysis.ready && ruleObjs().length); }
  function notify() {
    if (typeof host.onChange === "function") {
      host.onChange({ file: pdfFile, analysis, ready: isReady(), rules: ruleObjs });
    }
  }
  function setGate(ready) { $(".pb-step2").classList.toggle("locked", !ready); }

  // ---------- upload / analyze panel ----------
  function renderIdle(err) {
    panel.innerHTML = `<div class="drop pb-drop">Click or drop a PDF <span class="note">(max ${Math.round(maxBytes / 1048576)} MB, ${maxPages} pages)</span></div>`
      + (err ? `<div class="note bad" style="margin-top:8px">${esc(err)}</div>` : "");
    const d = $(".pb-drop");
    d.onclick = () => $(".pb-file").click();
    d.ondragover = (e) => { e.preventDefault(); d.classList.add("have"); };
    d.ondragleave = () => d.classList.remove("have");
    d.ondrop = (e) => { e.preventDefault(); setFile(e.dataTransfer.files[0]); };
  }
  function renderUploading(f, pct) {
    panel.innerHTML = `<div class="panel">
      <div class="prow"><span class="pname">Uploading ${esc(f.name)}</span><span class="ppct">${pct}%</span></div>
      <div class="bar"><i style="width:${pct}%"></i></div>
      <div class="note" style="margin-top:10px">${fmtSize(f.size)} · ready in a moment</div>
    </div>`;
  }
  function ckHtml(state, label, detail) {
    const inner = state === "pass" ? "✓" : state === "fail" ? "✕" : "";
    return `<div class="ck ${state}"><span class="dot">${inner}</span><span class="clab">${esc(label)}</span>`
      + (detail ? `<span class="cdet">${esc(detail)}</span>` : "") + "</div>";
  }
  function renderAnalyzing(f) {
    const rows = CHECK_LABELS.map(([, l]) => ckHtml("run", l, "")).join("");
    panel.innerHTML = `<div class="panel">
      <div class="prow"><span class="pname">Checking ${esc(f.name)}</span><span class="note pb-ckcount">0 / 4 checks</span></div>
      <div class="bar"><i style="width:100%"></i></div>
      <div class="checks pb-checks">${rows}</div>
    </div>`;
  }
  function revealChecks(checks, done) {
    const wrap = $(".pb-checks"); if (!wrap) { done(); return; }
    const rows = [...wrap.children]; let i = 0;
    const step = () => {
      if (i >= checks.length || i >= rows.length) { done(); return; }
      const c = checks[i];
      const state = c.ok === true ? "pass" : c.ok === false ? "fail" : "pend";
      rows[i].outerHTML = ckHtml(state, c.label, c.detail);
      i++; const cc = $(".pb-ckcount"); if (cc) cc.textContent = `${i} / ${checks.length} checks`;
      setTimeout(step, 380);
    };
    step();
  }
  function renderReady(f, d) {
    const di = (d.metadata && d.metadata.docinfo) || {};
    const order = [["Title", "Title"], ["Author", "Author"], ["Subject", "Subject"], ["Creator", "Creator"], ["Producer", "Producer"], ["CreationDate", "Created"], ["ModDate", "Modified"]];
    const drows = order.filter(([k]) => di[k] != null && String(di[k]).trim()).map(([k, lab]) => {
      let v = di[k]; if (k === "CreationDate" || k === "ModDate") v = fmtDate(v);
      return `<div class="drow"><span class="dk">${esc(lab)}</span><span class="dv" title="${esc(v)}">${esc(v)}</span></div>`;
    }).join("") || `<div class="note">No document metadata.</div>`;
    const probs = (d.fonts && d.fonts.problems) || [];
    const warn = probs.length ? `<div class="note bad" style="margin-top:10px">${probs.length} font(s) can't be reproduced; text in those will be left unchanged.</div>` : "";
    panel.innerHTML = `
      <div class="chip-file">
        <div class="ic">PDF</div>
        <div class="meta"><div class="fn">${esc(f.name)}</div>
          <div class="note" style="margin-top:2px">${d.pages} ${d.pages === 1 ? "page" : "pages"} · ${fmtSize(d.size)} · all checks passed ✓</div></div>
        <button class="swap pb-swap">swap file</button>
      </div>${warn}
      <div class="docinfo">
        <div class="dh"><label style="margin:0">Document info</label><span class="note">as it is in your file right now</span></div>
        <div class="docgrid">${drows}</div>
      </div>`;
    $(".pb-swap").onclick = resetFile;
  }
  function renderBlockedChecks(f, d) {
    const rows = d.checks.map((c) => ckHtml(c.ok === true ? "pass" : c.ok === false ? "fail" : "pend", c.label, c.detail)).join("");
    panel.innerHTML = `<div class="panel">
      <div class="prow"><span class="pname">${esc(f.name)}</span><button class="swap pb-swap">swap file</button></div>
      <div class="checks">${rows}</div>
      <div class="blocker">${esc((d.blocker && d.blocker.error) || "This PDF can't be edited.")}</div>
    </div>`;
    $(".pb-swap").onclick = resetFile;
  }
  function renderErr(f, msg, retry) {
    panel.innerHTML = `<div class="panel">
      <div class="prow"><span class="pname">${esc((f && f.name) || "That file")}</span><button class="swap pb-swap">${retry ? "Try again" : "try another"}</button></div>
      <div class="blocker">${esc(msg)}</div>
    </div>`;
    $(".pb-swap").onclick = retry ? () => uploadAndAnalyze(f) : resetFile;
  }

  function resetFile() {
    pdfFile = null; analysis = null; $(".pb-file").value = "";
    renderIdle(); setGate(false); notify();
  }
  function setFile(f) {
    if (!f) return;
    if (f.type !== "application/pdf" && !/\.pdf$/i.test(f.name)) { renderIdle("That is not a PDF."); return; }
    if (f.size > maxBytes) { renderIdle(`That file is over ${Math.round(maxBytes / 1048576)} MB. If you can, split it into smaller PDFs and do each part.`); return; }
    pdfFile = f; analysis = null; setGate(false); notify();
    uploadAndAnalyze(f);
  }
  function uploadAndAnalyze(f) {
    renderUploading(f, 0);
    Promise.resolve(
      host.analyze(f, {
        onProgress: (pct) => renderUploading(f, Math.min(99, pct)),
        onUploaded: () => renderAnalyzing(f),
      })
    ).then((d) => onAnalyzed(f, d))
      .catch(() => onAnalyzed(f, { ok: false, error: "Network error. Try again.", code: "engine_error" }));
  }
  function onAnalyzed(f, d) {
    analysis = d;
    if (!d || !d.ok) { renderErr(f, (d && d.error) || "We couldn't read that PDF.", d && d.code === "engine_error"); setGate(false); notify(); return; }
    renderAnalyzing(f);
    revealChecks(d.checks || [], () => {
      if (d.ready) { setTimeout(() => { renderReady(f, d); setGate(true); notify(); refreshAllCounts(); }, 280); }
      else { renderBlockedChecks(f, d); setGate(false); notify(); }
    });
  }

  // ---------- rules (action column) ----------
  function mkTog(label, cls, title, on) {
    const b = document.createElement("button"); b.type = "button";
    b.className = "tog " + cls; b.textContent = label; b.title = title;
    if (on) b.classList.add("on");
    return b;
  }
  function mkSelect(cls, opts, val) {
    const s = document.createElement("select"); s.className = cls;
    for (const [v, l] of opts) { const o = document.createElement("option"); o.value = v; o.textContent = l; s.appendChild(o); }
    const sv = String(val); if ([...s.options].some((o) => o.value === sv)) s.value = sv;
    return s;
  }
  function mkInput(cls, ph, val) {
    const i = document.createElement("input"); i.type = "text"; i.className = cls; i.placeholder = ph; i.value = val || "";
    return i;
  }
  function readRow(row) {
    const r = row._rule, q = (s) => row.querySelector(s);
    if (q(".action")) r.action = q(".action").value;
    if (r.action === "meta") {
      if (q(".mf")) r.metaField = q(".mf").value;
      if (q(".mval")) r.metaValue = q(".mval").value;
    } else {
      if (q(".f")) r.find = q(".f").value;
      if (q(".r")) r.replace = q(".r").value;
      const sc = q(".scope"); if (sc) r.scope = /^\d+$/.test(sc.value) ? parseInt(sc.value, 10) : sc.value;
      r.ci = !!(q(".t-aa") && q(".t-aa").classList.contains("on"));
      r.word = !!(q(".t-w") && q(".t-w").classList.contains("on"));
      r.regex = !!(q(".t-re") && q(".t-re").classList.contains("on"));
    }
    return r;
  }
  function renderRow(row) {
    const r = row._rule; row.innerHTML = "";
    const changed = () => { notify(); updateCount(row); };
    if (PRESET_LABELS[r.action]) {
      const [lab, desc] = PRESET_LABELS[r.action];
      const chip = document.createElement("div"); chip.className = "presetrow";
      chip.innerHTML = `<b>${lab}</b> <span class="note">${desc}</span>`;
      const del = document.createElement("button"); del.className = "x"; del.title = "remove"; del.textContent = "×";
      del.onclick = () => { row.remove(); notify(); };
      row.append(chip, del);
      return;
    }
    const action = mkSelect("action", ACTION_OPTS, r.action || "replace");
    action.style.color = ACTION_COLORS[r.action] || "var(--head)";
    action.onchange = () => { readRow(row); r.action = action.value; renderRow(row); notify(); };
    row.append(action);
    if (r.action === "meta") {
      const mf = mkSelect("mf", META_FIELDS.map((f) => [f, f]), r.metaField || "Title"); mf.onchange = notify;
      const mv = mkInput("mval", "new value (blank clears it)", r.metaValue); mv.oninput = notify;
      row.append(mf, mv);
    } else {
      const f = mkInput("f", "text in the PDF", r.find); f.oninput = changed; row.append(f);
      if (r.action === "redact") { const c = document.createElement("div"); c.className = "rchip redact"; c.textContent = "████ blacked out"; row.append(c); }
      else if (r.action === "remove") { const c = document.createElement("div"); c.className = "rchip remove"; c.textContent = "text deleted"; row.append(c); }
      else { const rr = mkInput("r", "new text", r.replace); rr.oninput = changed; row.append(rr); }
      const scope = mkSelect("scope", SCOPES, r.scope == null ? "first" : r.scope); scope.title = "which matches"; scope.onchange = notify;
      const aa = mkTog("Aa", "t-aa", "ignore case", r.ci), wd = mkTog("W", "t-w", "whole word", r.word), re = mkTog(".*", "t-re", "regular expression", r.regex);
      [aa, wd, re].forEach((b) => { b.onclick = () => { b.classList.toggle("on"); changed(); }; });
      row.append(scope, aa, wd, re);
    }
    const del = document.createElement("button"); del.className = "x"; del.title = "remove"; del.textContent = "×";
    del.onclick = () => { row.remove(); notify(); };
    row.append(del);
    if (r.action !== "meta") { const h = document.createElement("span"); h.className = "mhint"; row.append(h); updateCount(row); }
  }
  function updateCount(row) {
    const hint = row.querySelector(".mhint"); if (!hint) return;
    const r = readRow(row);
    const find = (r.find || "").trim();
    if (!find || !analysis || !analysis.text) { hint.textContent = ""; hint.className = "mhint"; return; }
    const n = countMatches(analysis.text, find, { ci: r.ci, word: r.word, regex: r.regex });
    if (n === -1) { hint.textContent = "invalid pattern"; hint.className = "mhint bad"; }
    else if (n === 0) { hint.textContent = "not found in this PDF"; hint.className = "mhint bad"; }
    else { hint.textContent = n + " match" + (n === 1 ? "" : "es"); hint.className = "mhint ok"; }
  }
  function refreshAllCounts() { rulesEl.querySelectorAll(".rule").forEach(updateCount); }
  function addRow(rule) {
    const row = document.createElement("div"); row.className = "rule";
    row._rule = Object.assign({ action: "replace", find: "", replace: "", scope: "first", ci: false, word: false, regex: false, metaField: "Title", metaValue: "" }, rule || {});
    renderRow(row); rulesEl.appendChild(row);
  }

  // ---------- rules file ----------
  async function loadRulesFile(f) {
    const err = $(".pb-rulesErr"); err.textContent = "";
    if (!f) return;
    if (f.size > 64 * 1024) { err.textContent = "Rules file is too big (max 64 KB)."; return; }
    const res = parseRulesFile(await f.text(), maxRules);
    if (!res.ok) { err.textContent = res.error[0].toUpperCase() + res.error.slice(1) + "."; return; }
    rulesEl.innerHTML = "";
    res.rules.forEach((r) => addRow(r));
    notify();
  }

  // ---------- wiring ----------
  $(".pb-file").onchange = (e) => setFile(e.target.files[0]);
  $(".pb-addrow").onclick = () => { addRow(); notify(); };
  root.querySelectorAll(".pb-preset").forEach((b) => b.onclick = () => { addRow({ action: b.dataset.preset }); notify(); });
  $(".pb-uploadRules").onclick = () => $(".pb-rulesFile").click();
  $(".pb-rulesFile").onchange = (e) => loadRulesFile(e.target.files[0]);

  renderIdle(); setGate(false); addRow(); notify();

  return {
    getFile: () => pdfFile,
    getAnalysis: () => analysis,
    getRules: ruleObjs,
    isReady,
    reset: resetFile,
  };
}
