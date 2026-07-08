// The shared pdfblah App surface: one uploaded PDF, then Edit (stack changes into one
// downloaded PDF) or Tools (single-purpose utilities, each with its own inputs and output).
// mountApp(root, host) builds and wires it; the host adapter supplies the backend calls, so
// the hosted site and the local `pdfblah gui` run ONE implementation and never drift.
//
//   host.analyze(file, {onProgress, onUploaded}) -> Promise<analysis>
//   host.post(path, payload) -> Promise<result>     // JSON POST to /apply, /combine, ...
//   host.downloadUrl(fileId) -> string               // where a stashed result is fetched
//   host.maxBytes / maxPages / maxRules / sampleRulesUrl
import { escapeHtml as esc, countMatches, parseRulesFile, friendlyRuleReason, META_FIELDS } from "./gate.mjs";

const el = (t, c, h) => { const e = document.createElement(t); if (c) e.className = c; if (h != null) e.innerHTML = h; return e; };
const fileToB64 = (f) => new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(String(r.result).split(",", 2)[1] || ""); r.onerror = rej; r.readAsDataURL(f); });

const CTYPE = {
  replace: ["a-text", "Replace"], redact: ["a-text", "Redact"], remove: ["a-text", "Remove"],
  scrub: ["a-text", "Scrub PII"], anon: ["a-text", "Anonymize"],
  watermark: ["a-mark", "Watermark"], number: ["a-mark", "Page numbers"], bates: ["a-mark", "Bates"], stamp: ["a-mark", "Stamp"],
  metaset: ["a-meta", "Metadata"], stripmeta: ["a-meta", "Strip metadata"],
  pages: ["a-page", "Reorder pages"], rotate: ["a-page", "Rotate"], crop: ["a-page", "Crop"],
  protect: ["a-lock", "Password"], optimize: ["a-lock", "Shrink"],
};
const MENU = [
  ["Text", [["replace", "Replace", "Swap text for new text"], ["redact", "Redact", "Black out and delete"], ["remove", "Remove", "Delete text entirely"]]],
  ["Marks", [["watermark", "Watermark", "Text or image over the page"], ["number", "Page numbers", '"Page 1 of 4"'], ["bates", "Bates", "Legal sequential numbering"], ["stamp", "Stamp", "Overlay an image or PDF"]]],
  ["Metadata", [["metaset", "Edit a field", "Title, author, dates…"], ["stripmeta", "Strip all metadata", "Remove author, software, dates"]]],
  ["Pages", [["pages", "Keep or reorder", "Choose and order pages"], ["rotate", "Rotate", "Turn pages 90°"], ["crop", "Crop", "Trim the page area"]]],
  ["Protect & clean up", [["protect", "Password-protect", "Encrypt with a password"], ["optimize", "Shrink the file", "Compress, optional downsample"]]],
];
const ICON = {
  combine: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="10" height="13" rx="1.5"/><path d="M15 8h5a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H10a1 1 0 0 1-1-1v-2"/></svg>',
  split: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="16" rx="2"/><path d="M12 4v16" stroke-dasharray="2 2.5"/></svg>',
  render: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="8.5" cy="9.5" r="1.6"/><path d="M4 17l4.5-5 4 4L16 11l4 5"/></svg>',
  extract: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M13 3v5h5"/><path d="M9 14h6M12 11v6"/></svg>',
  compare: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="8" height="14" rx="1.5"/><rect x="13" y="5" width="8" height="14" rx="1.5"/><path d="M17 9v6"/></svg>',
  attach: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11l-8.5 8.5a4 4 0 0 1-5.7-5.7L15 5.6a2.6 2.6 0 0 1 3.7 3.7l-8.5 8.5a1.2 1.2 0 0 1-1.7-1.7l7.8-7.8"/></svg>',
  form: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8h5M8 12h8M8 16h6"/></svg>',
  sign: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 18c3-1 5-9 7-9s1 5 3 5 3-3 3-3"/><path d="M15 19l2 2 4-4"/></svg>',
};

export function mountApp(root, host = {}) {
  const maxBytes = host.maxBytes || 1024 * 1024 * 1024;
  const maxPages = host.maxPages || 5000;
  const maxRules = host.maxRules || 1000;
  let pdfFile = null, analysis = null, mode = "edit";
  const changes = [];
  const assets = {};  // id -> base64 for watermark/stamp images

  root.classList.add("pb-app");
  root.innerHTML = `
    <div class="card"><span class="lbl">Your PDF</span><div class="pb-file"></div>
      <input type="file" class="pb-fileinput" accept="application/pdf" hidden></div>
    <div class="modewrap gated locked"><div class="modeseg" role="tablist">
      <button class="modebtn on" data-mode="edit" role="tab">Edit<small>stack changes &rarr; one PDF</small></button>
      <button class="modebtn" data-mode="tools" role="tab">Tools<small>one-shot utilities</small></button>
    </div></div>
    <div class="gated locked pb-surface">
      <section class="pb-edit">
        <div class="card"><span class="lbl">What to change</span>
          <div class="changes pb-changes"></div>
          <div class="addwrap"><button class="addbtn pb-add"><span style="font-size:16px">+</span> Add a change</button><div class="menu pb-menu" hidden></div></div>
          <div class="presets"><span class="plabel">One-click:</span>
            <button class="preset" data-p="scrub">Scrub PII</button>
            <button class="preset" data-p="anon">Anonymize</button>
            <button class="preset" data-p="stripmeta">Strip all metadata</button>
            <button class="preset pb-rulesbtn">Upload a rules file</button>
            <input type="file" class="pb-rulesinput" accept=".txt,.csv,text/plain" hidden></div>
          <div class="pb-rulesErr bad" style="font-size:12.5px;margin-top:8px"></div>
        </div>
        <div class="runbar"><button class="act pb-preview" disabled>${host.editLabel || "Edit &amp; download"} &rarr;</button><span class="runnote pb-editnote"></span></div>
        <div class="result hidden pb-editresult"></div>
      </section>
      <section class="pb-tools" hidden><div class="card"><div class="pb-toolsgrid"></div></div></section>
    </div>`;

  const $ = (s) => root.querySelector(s);
  const filePanel = $(".pb-file"), changesEl = $(".pb-changes"), menuEl = $(".pb-menu");

  // ---------- Your PDF ----------
  const fmtSize = (n) => n >= 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " KB";
  const fmtDate = (v) => { const m = /^D:(\d{4})(\d{2})(\d{2})(\d{2})?(\d{2})?/.exec(v || ""); return m ? `${m[1]}-${m[2]}-${m[3]}${m[4] ? ` ${m[4]}:${m[5] || "00"}` : ""}` : (v || ""); };
  function setGate(ready) { root.querySelectorAll(".gated").forEach((e) => e.classList.toggle("locked", !ready)); }
  function renderIdle(err) {
    filePanel.innerHTML = `<div class="drop pb-drop">Drop a PDF here, or click to choose <span style="color:var(--muted);font-size:13px">(max ${Math.round(maxBytes / 1048576)} MB)</span></div>` + (err ? `<div class="bad" style="font-size:13px;margin-top:8px">${esc(err)}</div>` : "");
    const d = $(".pb-drop");
    d.onclick = () => $(".pb-fileinput").click();
    d.ondragover = (e) => { e.preventDefault(); d.classList.add("have"); };
    d.ondragleave = () => d.classList.remove("have");
    d.ondrop = (e) => { e.preventDefault(); setFile(e.dataTransfer.files[0]); };
  }
  function renderUploading(f) { filePanel.innerHTML = `<div class="panel"><div class="prow"><b style="color:var(--head)">Reading ${esc(f.name)}</b></div><div class="bar"><i style="width:100%"></i></div></div>`; }
  function renderAnalyzing(f) { filePanel.innerHTML = `<div class="panel"><div class="prow"><b style="color:var(--head)">Checking ${esc(f.name)}</b></div><div class="bar"><i style="width:100%"></i></div><div class="checks pb-checks"></div></div>`; }
  function ck(state, label, detail) { const i = state === "pass" ? "✓" : state === "fail" ? "✕" : ""; return `<div class="ck ${state}"><span class="dot">${i}</span><span class="clab">${esc(label)}</span>${detail ? `<span class="cdet">${esc(detail)}</span>` : ""}</div>`; }
  function renderReady(f, d) {
    const di = (d.metadata && d.metadata.docinfo) || {};
    const order = [["Title", "Title"], ["Author", "Author"], ["Subject", "Subject"], ["Creator", "Creator"], ["Producer", "Producer"], ["CreationDate", "Created"], ["ModDate", "Modified"]];
    const rows = order.filter(([k]) => di[k] != null && String(di[k]).trim()).map(([k, lab]) => { let v = di[k]; if (k === "CreationDate" || k === "ModDate") v = fmtDate(v); return `<div class="drow"><span class="dk">${esc(lab)}</span><span class="dv" title="${esc(v)}">${esc(v)}</span></div>`; }).join("") || `<div class="cnote">No document metadata.</div>`;
    const probs = (d.fonts && d.fonts.problems) || [];
    filePanel.innerHTML = `<div class="filechip"><div class="ic">PDF</div><div class="meta"><div class="fn">${esc(f.name)}</div><div class="sub">${d.pages} ${d.pages === 1 ? "page" : "pages"} · ${fmtSize(d.size)} · all checks passed ✓</div></div><button class="swap pb-swap">swap file</button></div>${probs.length ? `<div class="bad" style="font-size:12.5px;margin-top:8px">${probs.length} font(s) can't be reproduced; text in those is left unchanged.</div>` : ""}<div style="margin-top:14px"><span class="lbl" style="margin-bottom:4px">Document info</span><div class="docgrid">${rows}</div></div>`;
    $(".pb-swap").onclick = resetFile;
  }
  function renderBlocked(f, d) {
    filePanel.innerHTML = `<div class="panel"><div class="prow"><b style="color:var(--head)">${esc(f.name)}</b><button class="swap pb-swap">swap file</button></div><div class="checks">${(d.checks || []).map((c) => ck(c.ok === true ? "pass" : c.ok === false ? "fail" : "pend", c.label, c.detail)).join("")}</div><div class="blocker">${esc((d.blocker && d.blocker.error) || "This PDF can't be edited.")}</div></div>`;
    $(".pb-swap").onclick = resetFile;
  }
  function resetFile() { pdfFile = null; analysis = null; $(".pb-fileinput").value = ""; renderIdle(); setGate(false); refreshCounts(); $(".pb-editresult").classList.add("hidden"); }
  function setFile(f) {
    if (!f) return;
    if (f.type !== "application/pdf" && !/\.pdf$/i.test(f.name)) { renderIdle("That is not a PDF."); return; }
    if (f.size > maxBytes) { renderIdle(`That file is over ${Math.round(maxBytes / 1048576)} MB.`); return; }
    pdfFile = f; analysis = null; setGate(false);
    renderUploading(f);
    Promise.resolve(host.analyze(f, { onProgress() {}, onUploaded() { renderAnalyzing(f); } }))
      .then((d) => onAnalyzed(f, d)).catch(() => onAnalyzed(f, { ok: false, error: "Couldn't read that PDF." }));
  }
  function onAnalyzed(f, d) {
    analysis = d;
    if (!d || !d.ok) { renderIdle((d && d.error) || "Couldn't read that PDF."); setGate(false); return; }
    renderAnalyzing(f);
    const checks = d.checks || []; const wrap = $(".pb-checks"); let i = 0;
    (function step() {
      if (i >= checks.length) { setTimeout(() => { if (d.ready) { renderReady(f, d); setGate(true); refreshCounts(); } else { renderBlocked(f, d); setGate(false); } }, 200); return; }
      const c = checks[i]; wrap.insertAdjacentHTML("beforeend", ck(c.ok === true ? "pass" : c.ok === false ? "fail" : "pend", c.label, c.detail)); i++; setTimeout(step, 260);
    })();
  }
  $(".pb-fileinput").onchange = (e) => setFile(e.target.files[0]);

  // ---------- Edit: change rows ----------
  const bindText = (c, field, ph, cls) => { const i = el("input"); i.type = "text"; i.className = cls || "grow"; i.placeholder = ph || ""; i.value = c[field] || ""; i.oninput = () => { c[field] = i.value; if (field === "find") updateCount(i.closest(".change"), c); }; return i; };
  const bindSelect = (c, field, opts, cls) => { const s = el("select", cls || "grow"); opts.forEach(([v, l]) => { const o = el("option"); o.value = v; o.textContent = l; s.appendChild(o); }); s.value = c[field] != null ? String(c[field]) : opts[0][0]; s.onchange = () => { c[field] = s.value; }; return s; };
  const bindToggle = (c, field, label) => { const b = el("button", "tog" + (label === ".*" ? " re" : "") + (c[field] ? " on" : ""), label); b.type = "button"; b.onclick = () => { c[field] = !c[field]; b.classList.toggle("on", c[field]); if (field === "regex" || field === "ci" || field === "word") updateCount(b.closest(".change"), c); }; return b; };
  const bindNum = (c, field, ph, w) => { const i = el("input"); i.type = "number"; i.placeholder = ph || ""; i.value = c[field] != null ? c[field] : ""; i.style.width = (w || 70) + "px"; i.oninput = () => { c[field] = i.value === "" ? null : Number(i.value); }; return i; };
  function imgPicker(c, kind) {
    const wrap = el("span", "cnote"); const inp = el("input"); inp.type = "file"; inp.accept = kind === "pdf" ? "application/pdf" : "image/*"; inp.style.display = "none";
    const btn = el("button", "preset", c._assetName ? esc(c._assetName) : (kind === "pdf" ? "choose a PDF" : "choose an image")); btn.type = "button";
    btn.onclick = () => inp.click();
    inp.onchange = async () => { const f = inp.files[0]; if (!f) return; const id = "a" + Math.round(performance.now()); assets[id] = await fileToB64(f); c[kind === "pdf" ? "assetPdf" : "assetImage"] = id; c._assetName = f.name; btn.textContent = f.name; };
    wrap.append(btn, inp); return wrap;
  }

  function renderRow(c) {
    const [cat, label] = CTYPE[c.type];
    const row = el("div", "change");
    row.appendChild(el("span", "achip " + cat, `<span class="dot"></span>${label}`));
    const add = (...n) => n.forEach((x) => x && row.appendChild(typeof x === "string" ? el("span", null, x) : x));
    const scope = () => bindSelect(c, "scope", [["first", "first"], ["all", "all"], ["2", "2nd"], ["3", "3rd"]], "grow");
    scope().style && (scope.flex = 0);
    switch (c.type) {
      case "replace": add(bindText(c, "find", "text in the PDF"), el("span", "arrow", "→"), bindText(c, "repl", "new text"), sfix(scope()), bindToggle(c, "ci", "Aa"), bindToggle(c, "word", "W"), bindToggle(c, "regex", ".*")); break;
      case "redact": add(bindText(c, "find", "text to black out"), el("div", "rchip black", "████ blacked out"), sfix(scope())); break;
      case "remove": add(bindText(c, "find", "text to delete"), el("div", "rchip del", "text deleted"), sfix(scope())); break;
      case "scrub": add(el("span", "cnote", "Finds &amp; removes emails, cards, IBANs, SSNs, phone numbers.")); break;
      case "anon": add(el("span", "cnote", "Replaces detected data with realistic fakes.")); break;
      case "watermark": add(bindText(c, "text", "watermark text (or pick an image)"), bindSelect(c, "position", [["center", "center"], ["top-right", "top-right"], ["bottom-center", "footer"]]), bindToggle(c, "tile", "tile"), imgPicker(c, "img")); break;
      case "number": add(bindText(c, "format", "Page {n} of {total}"), bindSelect(c, "position", [["bottom-center", "bottom-center"], ["bottom-right", "bottom-right"]])); break;
      case "bates": add(bindText(c, "prefix", "prefix, e.g. ACME"), el("span", "cnote", "000001, 000002 …")); break;
      case "stamp": add(imgPicker(c, "img"), el("span", "cnote", "overlaid over each page")); break;
      case "metaset": add(bindSelect(c, "metaField", META_FIELDS.map((f) => [f, f]), "grow"), bindText(c, "metaValue", "new value (blank clears it)")); break;
      case "stripmeta": add(el("span", "cnote", "Removes author, dates, producer, and everything else.")); break;
      case "pages": add(bindText(c, "keep", "keep &amp; reorder, e.g. 1-3,5 or 3,1,2")); break;
      case "rotate": add(bindSelect(c, "degrees", [["90", "90° right"], ["180", "180°"], ["270", "90° left"]], "grow"), bindText(c, "pages", "pages (all)")); break;
      case "crop": add(bindText(c, "margins", "trim margins: 20,20,20,20")); break;
      case "protect": add(bindText(c, "password", "password to open")); break;
      case "optimize": add(el("span", "cnote", "Shrink — downsample images to"), bindSelect(c, "downsampleDpi", [["150", "150 dpi"], ["96", "96 dpi"], ["", "keep size"]])); break;
    }
    const x = el("button", "rx", "×"); x.title = "remove"; x.onclick = () => { changes.splice(changes.indexOf(c), 1); renderChanges(); }; row.appendChild(x);
    if (c.type === "watermark") row.appendChild(el("div", "cnote sub", "light grey · behind the text"));
    if (c.type === "protect") row.appendChild(el("div", "cnote sub", "AES-256 · applied last, after every other change"));
    if (c.type === "find" || ["replace", "redact", "remove"].includes(c.type)) { const h = el("span", "cnote sub"); row.appendChild(h); }
    return row;
  }
  function sfix(s) { s.style.flex = "0 1 90px"; return s; }
  function renderChanges() {
    changesEl.innerHTML = "";
    if (!changes.length) { changesEl.appendChild(el("div", "empty", "No changes yet. Add one below, or use a one-click preset.")); refreshCounts(); return; }
    changes.forEach((c) => { const row = renderRow(c); changesEl.appendChild(row); updateCount(row, c); });
    refreshCounts();
  }
  function updateCount(row, c) {
    const h = row && [...row.querySelectorAll(".cnote.sub")].pop();
    if (!h || !["replace", "redact", "remove"].includes(c.type)) return;
    const find = (c.find || "").trim();
    if (!find || !analysis || !analysis.text) { h.textContent = ""; h.className = "cnote sub"; return; }
    const n = countMatches(analysis.text, find, { ci: c.ci, word: c.word, regex: c.regex });
    if (n === -1) { h.textContent = "invalid pattern"; h.className = "cnote sub bad"; }
    else if (n === 0) { h.textContent = "not found in this PDF"; h.className = "cnote sub bad"; }
    else { h.textContent = n + " match" + (n === 1 ? "" : "es"); h.className = "cnote sub ok"; }
  }
  function refreshCounts() {
    const n = changes.length;
    $(".pb-editnote").textContent = n ? `${n} change${n > 1 ? "s" : ""} · one download` : "add a change to start";
    $(".pb-preview").disabled = !(pdfFile && analysis && analysis.ready && n);
  }
  const DEFAULTS = { replace: { scope: "first" }, redact: { scope: "all" }, remove: { scope: "all" }, watermark: { text: "DRAFT", position: "center", tile: true, opacity: 0.18 }, number: { format: "Page {n} of {total}", position: "bottom-center" }, bates: { prefix: "" }, metaset: { metaField: "Title" }, rotate: { degrees: "90" }, optimize: { downsampleDpi: "150" } };
  function addChange(type) { changes.push(Object.assign({ type }, DEFAULTS[type] || {})); renderChanges(); }

  // menu
  MENU.forEach(([grp, items]) => {
    const g = el("div"); g.appendChild(el("div", "mgh", grp));
    items.forEach(([type, name, desc]) => { const b = el("button", "mitem", `<b>${name}</b><span>${desc}</span>`); b.onclick = () => { addChange(type); closeMenu(); }; g.appendChild(b); });
    menuEl.appendChild(g);
  });
  const closeMenu = () => { menuEl.hidden = true; };
  $(".pb-add").onclick = (e) => { e.stopPropagation(); menuEl.hidden = !menuEl.hidden; };
  document.addEventListener("click", (e) => { if (!e.target.closest(".pb-menu") && !e.target.closest(".pb-add")) closeMenu(); });
  root.querySelectorAll(".preset[data-p]").forEach((b) => b.onclick = () => addChange(b.dataset.p));
  $(".pb-rulesbtn").onclick = () => $(".pb-rulesinput").click();
  $(".pb-rulesinput").onchange = async (e) => {
    const f = e.target.files[0]; if (!f) return; const err = $(".pb-rulesErr"); err.textContent = "";
    const res = parseRulesFile(await f.text(), maxRules);
    if (!res.ok) { err.textContent = res.error[0].toUpperCase() + res.error.slice(1) + "."; return; }
    changes.length = 0; res.rules.forEach((r) => changes.push(Object.assign({ type: "replace" }, r, { repl: r.replace })));
    renderChanges();
  };

  // apply (Edit pipeline)
  function toRules() {
    return changes.map((c) => {
      const r = { action: c.type === "metaset" ? "meta" : c.type };
      if (c.type === "replace") return { action: "replace", find: (c.find || "").trim(), replace: c.repl || "", scope: c.scope, ci: !!c.ci, word: !!c.word, regex: !!c.regex };
      if (c.type === "redact" || c.type === "remove") return { action: c.type, find: (c.find || "").trim(), scope: c.scope };
      if (c.type === "metaset") return { action: "meta", metaField: c.metaField, metaValue: c.metaValue || "" };
      if (c.type === "watermark") return { action: "watermark", text: c.text || undefined, assetImage: c.assetImage, position: c.position, tile: !!c.tile, opacity: c.opacity ?? 0.18 };
      if (c.type === "stamp") return { action: "stamp", assetImage: c.assetImage };
      if (c.type === "number") return { action: "number", format: c.format, position: c.position };
      if (c.type === "bates") return { action: "bates", prefix: c.prefix || "" };
      if (c.type === "pages") return { action: "pages", keep: c.keep || undefined };
      if (c.type === "rotate") return { action: "rotate", degrees: Number(c.degrees || 90), pages: c.pages || undefined };
      if (c.type === "crop") return { action: "crop", margins: (c.margins || "").split(",").map(Number).filter((x) => !isNaN(x)).length === 4 ? c.margins.split(",").map(Number) : undefined };
      if (c.type === "protect") return { action: "protect", password: c.password || "" };
      if (c.type === "optimize") return { action: "optimize", downsampleDpi: c.downsampleDpi ? Number(c.downsampleDpi) : undefined };
      return { action: c.type };
    }).filter((r) => (["replace", "redact", "remove"].includes(r.action) ? !!r.find : true));
  }
  $(".pb-preview").onclick = async () => {
    const btn = $(".pb-preview"), out = $(".pb-editresult");
    btn.disabled = true; $(".pb-editnote").innerHTML = `<span class="spin"></span> working`;
    const payload = { pdf: await fileToB64(pdfFile), name: pdfFile.name, rules: toRules(), assets };
    let d; try { d = await host.post("/apply", payload); } catch (e) { d = { ok: false, error: "Something went wrong." }; }
    $(".pb-editnote").textContent = ""; btn.disabled = false; renderEditResult(d);
  };
  function ruleLine(x) {
    if (x.action === "stripmeta") return `<li class="ok">✓ stripped all metadata</li>`;
    if (x.action === "meta") return `<li class="ok">✓ set “${esc(x.metaField)}”${x.replace ? ` to “${esc(x.replace)}”` : " (cleared)"}</li>`;
    if (!x.applied) return `<li class="bad">✗ “${esc(x.find || x.action)}” ${esc(friendlyRuleReason(x))}</li>`;
    const n = x.count ? ` <span class="pill">×${x.count}</span>` : "";
    const nm = { replace: "replaced", redact: "redacted", remove: "removed", watermark: "watermarked", number: "numbered", bates: "bates-numbered", stamp: "stamped", pages: "kept pages", rotate: "rotated", crop: "cropped", protect: "password-protected", optimize: "shrank", scrub: "scrubbed PII", anonymize: "anonymized" }[x.action] || x.action;
    const what = x.find ? ` “${esc(x.find)}”${x.action === "replace" ? ` → “${esc(x.replace) || "(deleted)"}”` : ""}` : "";
    return `<li class="ok">✓ ${nm}${what}${n}</li>`;
  }
  function renderEditResult(d) {
    const r = $(".pb-editresult"); r.classList.remove("hidden");
    if (!d || !d.ok) { r.innerHTML = `<div class="rh bad">${esc((d && d.error) || "Something went wrong.")}</div>`; return; }
    const rr = d.report;
    r.innerHTML = `<div class="rh"><span class="tick">✓</span> ${rr.applied} of ${rr.total} changes landed</div><ul>${rr.rules.map(ruleLine).join("")}</ul>`;
    // the report is shared; how the finished PDF is delivered differs: the host supplies
    // it (hosted = watermarked preview + pay; local = a direct download link).
    const deliver = el("div", "pb-deliver"); deliver.style.marginTop = "12px"; r.appendChild(deliver);
    if (typeof host.deliverEdit === "function") host.deliverEdit(d, deliver);
    else if (rr.applied > 0 && d.fileId) deliver.innerHTML = `<a class="act sm" href="${host.downloadUrl(d.fileId)}" download>Download edited PDF &rarr;</a>`;
    r.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  // ---------- Tools ----------
  const TOOLS = [
    ["combine", "Combine", "Join several PDFs into one", "several PDFs → one"],
    ["split", "Split", "Break one PDF into many", "one PDF → many"],
    ["render", "Render to images", "Pages to PNG or JPG", "PDF → images"],
    ["extract", "Extract", "Pull out text or images", "PDF → text / images"],
    ["compare", "Compare", "Diff two PDFs", "two PDFs → report"],
    ["attach", "Attachments", "Embed or pull out files", "list / add / extract"],
    ["form", "Form fields", "List, fill, or flatten", "fields → filled PDF"],
    ["sign", "Signatures", "Check who signed, and if valid", "PDF → report"],
  ];
  const gridEl = $(".pb-toolsgrid");
  function renderTools() {
    gridEl.className = "pb-toolsgrid"; gridEl.innerHTML = "";
    gridEl.appendChild(el("div", "toolshint", "Changing text, marks, pages, metadata, or a password? Those stack together in <b>Edit</b>. Tools here each do one job with their own inputs."));
    const grid = el("div", "tools"); gridEl.appendChild(grid);
    TOOLS.forEach(([id, name, desc, io]) => { const c = el("button", "tool", `<div class="ti">${ICON[id === "attach" ? "attach" : id]}</div><b>${name}</b><span>${desc}</span><span class="io">${io}</span>`); c.onclick = () => openTool(id, name, desc); grid.appendChild(c); });
  }
  function openTool(id, name, desc) {
    gridEl.innerHTML = "";
    const back = el("button", "back", "‹ All tools"); back.onclick = renderTools; gridEl.appendChild(back);
    gridEl.appendChild(el("div", "panelhead", `<div class="ti">${ICON[id === "attach" ? "attach" : id]}</div><div><h2>${name}</h2><p>${desc}</p></div>`));
    gridEl.appendChild(TOOLPANEL[id]());
  }
  const b64OfInput = async (inp) => inp.files[0] ? await fileToB64(inp.files[0]) : null;
  function resultBox() { const r = el("div", "result"); return r; }
  function runBtn(label) { return el("button", "act pb-run", label); }
  async function pdfB64() { return await fileToB64(pdfFile); }
  function dl(fileId, label) { return `<a class="act sm" href="${host.downloadUrl(fileId)}" download>${label}</a>`; }

  const TOOLPANEL = {
    combine() {
      const b = el("div"); const extra = [];
      const list = el("div", "filelist");
      const rebuild = () => { list.innerHTML = `<div class="fli"><span class="fnum">1</span><span class="fn">${esc(pdfFile ? pdfFile.name : "your PDF")}</span><span class="cnote">your PDF</span></div>` + extra.map((f, i) => `<div class="fli"><span class="fnum">${i + 2}</span><span class="fn">${esc(f.name)}</span><button class="fx" data-i="${i}">×</button></div>`).join(""); list.querySelectorAll(".fx").forEach((x) => x.onclick = () => { extra.splice(+x.dataset.i, 1); rebuild(); }); };
      rebuild();
      const drop = el("div", "adddrop", "+ Add more PDFs"); const inp = el("input"); inp.type = "file"; inp.accept = "application/pdf"; inp.multiple = true; inp.style.display = "none";
      drop.onclick = () => inp.click(); inp.onchange = () => { [...inp.files].forEach((f) => extra.push(f)); rebuild(); };
      const run = runBtn("Combine →"); const res = resultBox();
      run.onclick = async () => { run.disabled = true; run.textContent = "Combining…"; const pdfs = [await pdfB64(), ...await Promise.all(extra.map(fileToB64))]; const d = await host.post("/combine", { pdfs }); run.disabled = false; run.textContent = "Combine →"; res.innerHTML = d.ok ? `<div class="rh"><span class="tick">✓</span> Combined ${d.files} files · ${d.pages} pages</div>${dl(d.fileId, "Download combined.pdf →")}` : `<div class="rh bad">${esc(d.error)}</div>`; };
      const field = el("div", "field", "<label>PDFs to join, in order</label>"); field.append(list, drop, inp);
      b.append(field, run, res); return b;
    },
    split() {
      const b = el("div"); let mode = "each";
      const rs = el("div", "radioset");
      rs.innerHTML = `<label class="radio on" data-m="each"><span class="dot"></span>One file per page</label><label class="radio" data-m="every"><span class="dot"></span>Every <input type="text" value="2" style="width:52px;height:32px;text-align:center;margin:0 4px"> pages</label><label class="radio" data-m="ranges"><span class="dot"></span>By ranges <input type="text" placeholder="1-2, 3, 4" style="flex:1;height:32px;margin-left:6px"></label>`;
      rs.querySelectorAll(".radio").forEach((r) => r.onclick = (e) => { if (e.target.tagName === "INPUT") return; rs.querySelectorAll(".radio").forEach((x) => x.classList.remove("on")); r.classList.add("on"); mode = r.dataset.m; });
      const run = runBtn("Split →"); const res = resultBox();
      run.onclick = async () => { run.disabled = true; const payload = { pdf: await pdfB64() }; if (mode === "every") payload.every = Number(rs.querySelector('[data-m="every"] input').value || 1); else if (mode === "ranges") payload.ranges = (rs.querySelector('[data-m="ranges"] input').value || "").split(",").map((s) => s.trim()).filter(Boolean); else payload.every = 1; const d = await host.post("/split", payload); run.disabled = false; res.innerHTML = d.ok ? `<div class="rh"><span class="tick">✓</span> Split into ${d.parts} files</div>${dl(d.fileId, "Download all (.zip) →")}` : `<div class="rh bad">${esc(d.error)}</div>`; };
      b.append(el("div", "field", "<label>How to split</label>"), rs, el("div", "field", ""), run, res); return b;
    },
    render() {
      const b = el("div");
      b.innerHTML = `<div class="row2"><div class="field"><label>Format</label><select class="pb-fmt"><option>png</option><option>jpg</option></select></div><div class="field"><label>Resolution</label><select class="pb-dpi"><option value="150">150 dpi</option><option value="300">300 dpi</option><option value="72">72 dpi</option></select></div><div class="field"><label>Pages</label><input type="text" class="pb-pg" placeholder="all"></div></div>`;
      const run = runBtn("Render to images →"); const res = resultBox(); b.append(run, res);
      run.onclick = async () => { run.disabled = true; run.textContent = "Rendering…"; const d = await host.post("/render", { pdf: await pdfB64(), format: b.querySelector(".pb-fmt").value, dpi: Number(b.querySelector(".pb-dpi").value), pages: b.querySelector(".pb-pg").value || null }); run.disabled = false; run.textContent = "Render to images →"; res.innerHTML = d.ok ? `<div class="rh"><span class="tick">✓</span> Rendered ${d.count} pages</div><div class="grid2">${d.images.map((u) => `<img class="thumb" src="${u}">`).join("")}</div><div style="margin-top:10px">${dl(d.fileId, "Download images (.zip) →")}</div>` : `<div class="rh bad">${esc(d.error)}</div>`; };
      return b;
    },
    extract() {
      const b = el("div");
      b.innerHTML = `<div class="field"><label>Pull out</label><select class="pb-what" style="flex:0 1 200px"><option value="text">The text</option><option value="images">Embedded images</option></select></div>`;
      const run = runBtn("Extract →"); const res = resultBox(); b.append(run, res);
      run.onclick = async () => { run.disabled = true; const what = b.querySelector(".pb-what").value; const d = await host.post("/extract", { pdf: await pdfB64(), what }); run.disabled = false; if (!d.ok) { res.innerHTML = `<div class="rh bad">${esc(d.error)}</div>`; return; } res.innerHTML = what === "text" ? `<div class="rh"><span class="tick">✓</span> Extracted the text (${d.chars} chars)</div><div class="txtbox">${esc(d.text.slice(0, 4000))}</div>` : (d.count ? `<div class="rh"><span class="tick">✓</span> ${d.count} image(s)</div><div class="grid2">${d.images.map((u) => `<img class="thumb" src="${u}">`).join("")}</div><div style="margin-top:10px">${dl(d.fileId, "Download (.zip) →")}</div>` : `<div class="rh">No embedded images found.</div>`); };
      return b;
    },
    compare() {
      const b = el("div"); let fileB = null;
      const slotA = `<div class="slot filled"><b>${esc(pdfFile ? pdfFile.name : "your PDF")}</b>version A</div>`;
      const slotB = el("div", "slot"); slotB.innerHTML = `<b>Drop version B</b>the PDF to compare against`;
      const inp = el("input"); inp.type = "file"; inp.accept = "application/pdf"; inp.style.display = "none";
      slotB.onclick = () => inp.click(); inp.onchange = () => { fileB = inp.files[0]; if (fileB) { slotB.className = "slot filled"; slotB.innerHTML = `<b>${esc(fileB.name)}</b>version B`; } };
      const slots = el("div", "slots"); slots.innerHTML = slotA; slots.appendChild(slotB);
      const run = runBtn("Compare →"); const res = resultBox();
      run.onclick = async () => { if (!fileB) { res.innerHTML = `<div class="rh bad">Choose a second PDF (version B).</div>`; return; } run.disabled = true; const d = await host.post("/compare", { pdfA: await pdfB64(), pdfB: await fileToB64(fileB) }); run.disabled = false; if (!d.ok) { res.innerHTML = `<div class="rh bad">${esc(d.error)}</div>`; return; } const diffs = d.detail.filter((p) => !p.identical); res.innerHTML = `<div class="rh"><span class="tick">✓</span> ${d.changed} of ${d.pages} pages differ</div><div class="difflist">${diffs.slice(0, 20).map((p) => `<div><b>Page ${p.page}</b> — <span class="ok">+${p.added.length}</span> / <span class="bad">−${p.removed.length}</span> lines</div>`).join("") || "<div>The two files match.</div>"}</div>`; };
      b.append(el("div", "field", "<label>Compare two versions</label>"), slots, el("div", "field", ""), run, res); return b;
    },
    sign() {
      const b = el("div"); const res = resultBox();
      const listBtn = runBtn("List signatures"); listBtn.className = "act ghost sm"; const valBtn = runBtn("Validate →");
      const go = async (validate) => { const d = await host.post("/signatures", { pdf: await pdfB64(), validate }); if (!d.ok) { res.innerHTML = `<div class="rh bad">${esc(d.error)}</div>`; return; } if (!d.signatures.length) { res.innerHTML = `<div class="rh">No signatures found.</div>`; return; } res.innerHTML = validate ? d.signatures.map((s) => `<div class="rh">${esc(s.signer || s.field || "signature")}</div><div class="statrow"><span class="stat ${s.intact ? "good" : "warn"}">intact ${s.intact ? "✓" : "✕"}</span><span class="stat ${s.valid ? "good" : "warn"}">valid ${s.valid ? "✓" : "✕"}</span><span class="stat ${s.trusted ? "good" : "mut"}">${s.trusted ? "trusted" : "untrusted issuer"}</span></div>`).join("") : `<ul>${d.signatures.map((s) => `<li>Signed by <b>${esc(s.name || "unknown")}</b>${s.time ? " · " + esc(s.time) : ""}</li>`).join("")}</ul>`; };
      listBtn.onclick = () => go(false); valBtn.onclick = () => go(true);
      b.append(el("div", "field", "<label>Read who signed, or validate the signature</label>"), el("div", "row2", ""), res);
      const r2 = b.querySelector(".row2"); r2.style.flex = "0"; r2.append(listBtn, valBtn); r2.style.maxWidth = "360px";
      return b;
    },
    form() {
      const b = el("div"); const res = resultBox(); let fields = [];
      const listBtn = runBtn("List fields"); listBtn.className = "act ghost sm";
      const fillWrap = el("div"); const run = runBtn("Fill & save →"); run.style.display = "none"; let flatten = false;
      const flat = el("label", "radio", `<span class="dot"></span>Flatten (bake values in)`); flat.style.cssText = "display:inline-flex;margin:0 0 12px"; flat.onclick = () => { flatten = !flatten; flat.classList.toggle("on", flatten); };
      listBtn.onclick = async () => { const d = await host.post("/form", { pdf: await pdfB64(), action: "list" }); if (!d.ok || !d.fields.length) { res.innerHTML = `<div class="rh">No form fields found.</div>`; run.style.display = "none"; fillWrap.innerHTML = ""; return; } fields = d.fields; fillWrap.innerHTML = `<div class="field"><label>Fill fields</label><div class="row2">${fields.map((f, i) => `<div class="field" style="margin:0"><label style="font-weight:600;color:var(--muted)">${esc(f.name)}</label><input type="text" class="pb-ff" data-n="${esc(f.name)}" value="${esc(f.value)}" placeholder="value"></div>`).join("")}</div></div>`; fillWrap.appendChild(flat); run.style.display = "inline-block"; };
      run.onclick = async () => { const data = {}; fillWrap.querySelectorAll(".pb-ff").forEach((i) => data[i.dataset.n] = i.value); const d = await host.post("/form", { pdf: await pdfB64(), action: "fill", data, flatten }); res.innerHTML = d.ok ? `<div class="rh"><span class="tick">✓</span> Filled ${d.count} field(s)${d.flattened ? ", flattened" : ""}</div>${dl(d.fileId, "Download filled.pdf →")}` : `<div class="rh bad">${esc(d.error)}</div>`; };
      b.append(el("div", "field", "<label>PDF form</label>"), listBtn, fillWrap, run, res); return b;
    },
    attach() {
      const b = el("div"); const res = resultBox(); let add = [];
      const list = el("div", "filelist"); const drop = el("div", "adddrop", "+ Attach a file"); const inp = el("input"); inp.type = "file"; inp.style.display = "none"; inp.multiple = true;
      const rebuild = () => { list.innerHTML = add.length ? add.map((f, i) => `<div class="fli"><span class="fn">${esc(f.name)}</span><button class="fx" data-i="${i}">×</button></div>`).join("") : `<div class="fli"><span class="cnote">No files to attach yet.</span></div>`; list.querySelectorAll(".fx").forEach((x) => x.onclick = () => { add.splice(+x.dataset.i, 1); rebuild(); }); };
      rebuild(); drop.onclick = () => inp.click(); inp.onchange = () => { [...inp.files].forEach((f) => add.push(f)); rebuild(); };
      const listBtn = runBtn("List attachments"); listBtn.className = "act ghost sm"; const run = runBtn("Save with attachments →");
      listBtn.onclick = async () => { const d = await host.post("/attachments", { pdf: await pdfB64() }); res.innerHTML = `<div class="rh">${d.attachments.length ? "Attached: " + d.attachments.map(esc).join(", ") : "No attachments in this PDF."}</div>`; };
      run.onclick = async () => { if (!add.length) { res.innerHTML = `<div class="rh bad">Choose a file to attach.</div>`; return; } const payload = { pdf: await pdfB64(), add: await Promise.all(add.map(async (f) => ({ name: f.name, data: await fileToB64(f) }))) }; const d = await host.post("/attachments", payload); res.innerHTML = d.ok ? `<div class="rh"><span class="tick">✓</span> ${d.added.length} attached</div>${dl(d.fileId, "Download PDF →")}` : `<div class="rh bad">${esc(d.error)}</div>`; };
      b.append(el("div", "field", "<label>Files inside this PDF</label>"), list, drop, el("div", "row2", ""), res); const r2 = b.querySelector(".row2"); r2.append(listBtn, run); return b;
    },
  };

  // ---------- mode switch ----------
  root.querySelectorAll(".modebtn").forEach((btn) => btn.onclick = () => {
    root.querySelectorAll(".modebtn").forEach((x) => x.classList.toggle("on", x === btn));
    mode = btn.dataset.mode; const tools = mode === "tools";
    root.querySelector(".pb-tools").hidden = !tools; root.querySelector(".pb-edit").hidden = tools;
    if (tools) renderTools();
  });

  renderIdle(); setGate(false); renderChanges();
  return { getFile: () => pdfFile, getChanges: () => changes, reset: resetFile };
}
