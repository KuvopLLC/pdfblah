// pdfblah workbench — a multi-file document workbench. P1: the session shell + viewer
// (left rail of files + page thumbnails, center preview). P2: the Stack — an ordered,
// per-step on/off list of edits with a live After preview. Output formats, Inspect, and
// Recipes hang off this shell in later phases.
//
// mountWorkbench(root, host) builds and wires it. The host adapter supplies the backend so
// the local `pdfblah gui` and the hosted site run ONE implementation:
//   host.post(path, payload) -> Promise<result>   // "wbsession" / "wbadd" / "wbpreview" / ...
//   host.maxBytes
//
// Files upload once into a server-side session; everything else references them by id, so a
// live per-page re-render never re-uploads the PDF. Stack edits re-render only the active
// page (debounced + coalesced); the server caches the applied PDF by stack-hash.

const el = (t, c, h) => { const e = document.createElement(t); if (c) e.className = c; if (h != null) e.innerHTML = h; return e; };
const fileToB64 = (f) => new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(String(r.result).split(",", 2)[1] || ""); r.onerror = rej; r.readAsDataURL(f); });
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmtSize = (n) => n >= 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " KB";

export function mountWorkbench(root, host = {}) {
  const maxBytes = host.maxBytes || 1024 * 1024 * 1024;
  // stack: [{uid, type, on, cfg, entry}] — entry is this step's slice of the last preview
  // report (match counts / refusals). afterPages: page count of the applied PDF (a `pages`
  // step can change it); null until the first After preview.
  const S = { session: null, files: [], activeId: null, page: 1, dpi: 128, view: "after",
              stack: [], expanded: null, afterPages: null, zoom: "fit", formats: {},
              out: { format: "pdf", merge: false, toc: false, tabs: false, tocFont: "sans" },
              rendering: false,     // a preview request is in flight (drives the veil + locks Download)
              lastApplied: null };  // report.applied for the ACTIVE file's last After render
  let uidSeq = 0;

  root.classList.add("wb");
  root.innerHTML = `
    <aside class="wb-left">
      <div class="wb-lhead"><span class="wb-eyebrow wb-sesslabel">Files</span><button class="wb-add">+ Add PDF</button></div>
      <div class="wb-files"></div>
      <div class="wb-lhint" hidden>✓ = goes in your download · drop PDFs anywhere to add more</div>
      <div class="wb-pageslabel wb-eyebrow" hidden>Pages</div>
      <div class="wb-pages"></div>
      <input type="file" class="wb-fileinput" accept="application/pdf" multiple hidden>
    </aside>
    <main class="wb-center">
      <div class="wb-viewtop"></div>
      <div class="wb-errbar" hidden></div>
      <div class="wb-stagewrap">
        <div class="wb-stage"></div>
        <div class="wb-veil" hidden><span></span></div>
      </div>
      <div class="wb-nav" hidden></div>
    </main>
    <aside class="wb-right">
      <div class="wb-tabs"><button class="wb-tab on" data-t="stack">Edits<span class="wb-count" hidden></span></button><button class="wb-tab" data-t="inspect">Inspect</button><button class="wb-recbtn">Recipes ▾</button></div>
      <div class="wb-recmenu" hidden>
        <div class="wb-recsave"><input class="wb-fin wb-recname" placeholder="name these edits…" maxlength="80"><button class="wb-recsavebtn">Save</button></div>
        <div class="wb-reclist"></div>
      </div>
      <div class="wb-rightbody">
        <div class="wb-tabbody" data-t="stack">
          <div class="wb-steps"></div>
          <button class="wb-addstep">+ Add an edit</button>
          <div class="wb-stepmenu" hidden></div>
        </div>
        <div class="wb-tabbody" data-t="inspect" hidden>
          <div class="wb-inspect"></div>
        </div>
      </div>
      <div class="wb-output">
        <div class="wb-outtop"><span class="wb-eyebrow">Output</span><span class="wb-outsum"></span></div>
        <div class="wb-fmts"></div>
        <div class="wb-outopts">
          <input type="password" class="wb-fin wb-opw" placeholder="password (optional)" autocomplete="new-password">
          <select class="wb-fin wb-oopt"><option value="">full quality</option><option value="150">compress · 150 dpi</option><option value="96">compress · 96 dpi</option></select>
          <button type="button" class="wb-chip wb-omerge">Merge into one PDF</button>
          <button type="button" class="wb-chip wb-otoc" hidden>Table of contents</button>
          <button type="button" class="wb-chip wb-otabs" hidden>Numbered tabs</button>
          <select class="wb-fin wb-otocfont" hidden><option value="sans">contents in Helvetica</option><option value="serif">contents in Times</option></select>
        </div>
        <div class="wb-quote" hidden></div>
        <button class="wb-download" disabled>Download</button>
        <button class="wb-reciperow" type="button">Save these edits as a recipe</button>
      </div>
    </aside>`;

  const $ = (s) => root.querySelector(s);
  const filesEl = $(".wb-files"), pagesEl = $(".wb-pages"), stageEl = $(".wb-stage"), navEl = $(".wb-nav"), viewtop = $(".wb-viewtop"), errbar = $(".wb-errbar");
  const activeRec = () => S.files.find((x) => x.id === S.activeId);
  // One silent retry on a dropped connection: transient resets happen (seen in testing) and
  // will happen more once every stack edit triggers a preview render. Worst case a retried
  // wbadd leaves an orphan copy in the session dir until the session is reaped — harmless.
  const post = async (path, payload) => {
    for (let attempt = 0; ; attempt++) {
      try { return await host.post(path, payload); }
      catch (e) {
        if (attempt >= 1) return { ok: false, error: "Network error. Try again." };
        await new Promise((r) => setTimeout(r, 350));
      }
    }
  };
  async function ensureSession() {
    if (!S.session) { const d = await post("wbsession", {}); S.session = d.session; S.formats = d.formats || {}; renderOutput(); }
    return S.session;
  }

  // ---------- add files ----------
  async function addFiles(list) {
    await ensureSession();
    for (const f of list) {
      if (f.type !== "application/pdf" && !/\.pdf$/i.test(f.name)) continue;
      if (f.size > maxBytes) { const r = { name: f.name, size: f.size, error: `over ${Math.round(maxBytes / 1048576)} MB` }; S.files.push(r); renderFiles(); continue; }
      const rec = { id: null, name: f.name, size: f.size, pages: 0, isScan: false, included: true, loading: true };
      S.files.push(rec); renderFiles();
      const b64 = await fileToB64(f);
      const d = await post("wbadd", { session: S.session, pdf: b64, name: f.name });
      rec.loading = false;
      if (!d.ok) { rec.error = d.error || "couldn't read this PDF"; renderFiles(); continue; }
      rec.id = d.fileId; rec.pages = d.pages; rec.isScan = d.isScan; rec.analysis = d.analysis;
      if (!S.activeId) { S.activeId = rec.id; S.page = 1; }
      renderFiles();
      if (!S.stack.length) renderStack(); // the empty-stack hint adapts to scans
      if (S.activeId === rec.id) { renderPagesRail(); renderCenter(); }
    }
  }

  // ---------- file list ----------
  function renderFiles() {
    const n = S.files.length;
    $(".wb-sesslabel").textContent = n ? `Files · ${n}` : "Files";
    $(".wb-lhint").hidden = !n;
    filesEl.innerHTML = "";
    S.files.forEach((rec) => {
      const row = el("div", "wb-file" + (rec.id && rec.id === S.activeId ? " active" : "") + (rec.error ? " err" : ""));
      const chk = el("label", "wb-inc"); const cb = el("input"); cb.type = "checkbox"; cb.checked = rec.included !== false; cb.disabled = !rec.id;
      cb.title = "include in output"; cb.onchange = () => { rec.included = cb.checked; row.classList.toggle("muted", !cb.checked); if (!S.stack.length) renderStack(); else renderOutput(); };
      chk.appendChild(cb);
      const sub = rec.loading ? "reading…" : rec.error ? esc(rec.error)
        : `${rec.pages} page${rec.pages === 1 ? "" : "s"} · ${fmtSize(rec.size)}${rec.isScan ? ' <span class="wb-scan">scan</span>' : ""}`;
      const meta = el("div", "wb-fmeta", `<div class="wb-fn">${esc(rec.name)}</div><div class="wb-fsub">${sub}</div>`);
      meta.onclick = () => { if (rec.id) { S.activeId = rec.id; S.page = 1; S.afterPages = null; S.lastApplied = null; renderFiles(); renderPagesRail(); renderCenter(); if (inspectVisible()) renderInspect(); } };
      const rm = el("button", "wb-frm", "×"); rm.title = "remove"; rm.onclick = async (e) => {
        e.stopPropagation();
        if (rec.id) await post("wbremove", { session: S.session, file: rec.id });
        S.files = S.files.filter((x) => x !== rec);
        if (S.activeId === rec.id) { const first = S.files.find((x) => x.id); S.activeId = first ? first.id : null; S.page = 1; S.afterPages = null; }
        renderFiles(); renderPagesRail(); renderCenter();
        if (!S.stack.length) renderStack();
        if (inspectVisible()) renderInspect();
      };
      row.append(chk, meta, rm);
      if (rec.included === false) row.classList.add("muted");
      filesEl.appendChild(row);
    });
    $(".wb-pageslabel").hidden = !S.activeId;
    renderOutput();
  }

  // ---------- page thumbnails (lazy, sequential so we don't hammer the renderer) ----------
  let thumbToken = 0;
  async function renderPagesRail() {
    const rec = activeRec(); pagesEl.innerHTML = "";
    if (!rec) return;
    const myToken = ++thumbToken;
    for (let pg = 1; pg <= rec.pages; pg++) {
      const t = el("div", "wb-thumb" + (pg === S.page ? " on" : "")); t.dataset.pg = pg;
      t.innerHTML = `<div class="wb-thumbimg"></div><span class="wb-thumbn">${pg}</span>`;
      t.onclick = () => { S.page = pg; renderCenter(); markThumb(); };
      pagesEl.appendChild(t);
    }
    for (let pg = 1; pg <= rec.pages; pg++) {
      if (myToken !== thumbToken) return;
      const d = await post("wbrender", { session: S.session, file: rec.id, page: pg, dpi: 26 });
      if (myToken !== thumbToken) return;
      const cell = pagesEl.querySelector(`.wb-thumb[data-pg="${pg}"] .wb-thumbimg`);
      if (cell && d.ok) cell.style.backgroundImage = `url(${d.image})`;
    }
  }
  function markThumb() { pagesEl.querySelectorAll(".wb-thumb").forEach((t) => t.classList.toggle("on", +t.dataset.pg === S.page)); }

  // ---------- the Stack: step registry ----------
  // Each type: menu label/blurb, default cfg, summary(cfg, entry), rule(cfg) -> wire rule or
  // null while incomplete, form(body, st) -> inline controls. Wire format = the same rules
  // apply_actions already takes (the Edit app's changes[]). protect/optimize are deliberately
  // absent: they're output-stage steps (the engine pins them last) — they join the Output
  // panel in P3.
  const q = (s) => `“${s}”`;
  const SCOPES = [["first", "first match"], ["all", "all matches"], ["2", "2nd match"], ["3", "3rd match"]];
  const POSITIONS = ["center", "top", "top-left", "top-center", "top-right", "bottom", "bottom-left", "bottom-center", "bottom-right", "left", "right"];
  const DETECTORS = ["email", "phone", "iban", "credit_card", "ssn", "date", "amount"];
  const scopeLabel = (v) => (SCOPES.find(([k]) => k === v) || SCOPES[0])[1];
  const findForm = (body, st) => {
    fText(body, st, "Find", "find", "text to find");
    if (st.type === "replace") fText(body, st, "Replace with", "replace", "replacement");
    fSel(body, st, "Scope", "scope", SCOPES);
    fChips(body, st, "Options", [["ci", "Ignore case"], ["word", "Whole word"], ["regex", "Regex"]]);
    // the substitution opt-in surfaces as a chip once it's active (or on offer), so it
    // can be turned back off; the primary entry point is the prompt on a refused card
    if (st.type === "replace" && (st.cfg.substituteFont ||
        (st.entry && st.entry.refused && st.entry.substitutable !== false)))
      fChips(body, st, "Font", [["substituteFont", "Substitute a similar font"]]);
  };
  const findRule = (t) => (c) => c.find.trim() ? { action: t, find: c.find.trim(), ...(t === "replace" ? { replace: c.replace } : {}), scope: c.scope, ci: !!c.ci, word: !!c.word, regex: !!c.regex, ...(c.substituteFont ? { substituteFont: true } : {}) } : null;
  const typesRule = (t) => (c) => ({ action: t, ...(c.types.length ? { types: c.types } : {}),
    ...(t === "anonymize" && c.names.trim() ? { names: c.names.split(",").map((x) => x.trim()).filter(Boolean) } : {}) });

  const STEPS = {
    replace: { title: "Replace", blurb: "find text, swap it",
      cfg: () => ({ find: "", replace: "", scope: "all", ci: false, word: false, regex: false }),
      summary: (c) => c.find.trim() ? `${q(c.find)} → ${q(c.replace)} · ${scopeLabel(c.scope)}` : "set the text to find",
      rule: findRule("replace"), form: findForm },
    remove: { title: "Remove text", blurb: "find text, delete it",
      cfg: () => ({ find: "", scope: "all", ci: false, word: false, regex: false }),
      summary: (c) => c.find.trim() ? `${q(c.find)} · ${scopeLabel(c.scope)}` : "set the text to find",
      rule: findRule("remove"), form: findForm },
    redact: { title: "Redact", blurb: "remove text + black bar",
      cfg: () => ({ find: "", scope: "all", ci: false, word: false, regex: false }),
      summary: (c) => c.find.trim() ? `${q(c.find)} · ${scopeLabel(c.scope)}` : "set the text to find",
      rule: findRule("redact"), form: findForm },
    scrub: { title: "Scrub PII", blurb: "remove emails, phones, IBANs…",
      cfg: () => ({ types: [] }),
      summary: (c) => (c.types.length ? c.types.join(" · ") : "all detectors") + " → mask",
      rule: typesRule("scrub"),
      form: (body, st) => fChips(body, st, "Detect", DETECTORS.map((d) => [d, d.replace("_", " ")]), "types") },
    anonymize: { title: "Anonymize", blurb: "replace PII with realistic fakes",
      cfg: () => ({ types: [], names: "" }),
      summary: (c) => (c.types.length ? c.types.join(" · ") : "all detectors") + " → fakes",
      rule: typesRule("anonymize"),
      form: (body, st) => { fChips(body, st, "Detect", DETECTORS.map((d) => [d, d.replace("_", " ")]), "types"); fText(body, st, "Also names", "names", "Jane Doe, Acme Corp"); } },
    watermark: { title: "Watermark", blurb: "text or image across pages",
      cfg: () => ({ text: "DRAFT", position: "center", opacity: 0.18, rotation: 45, tile: false, assetImage: null, assetName: "" }),
      summary: (c) => `${c.assetImage ? c.assetName || "image" : q(c.text)} · ${Math.round(c.opacity * 100)}% · ${c.rotation}°`,
      rule: (c) => (c.text.trim() || c.assetImage) ? { action: "watermark", ...(c.assetImage ? { assetImage: c.assetImage } : { text: c.text.trim() }),
        position: c.position, opacity: +c.opacity, rotation: +c.rotation, tile: !!c.tile } : null,
      form: (body, st) => { fText(body, st, "Text", "text", "DRAFT"); fSel(body, st, "Position", "position", POSITIONS.map((p) => [p, p]));
        fNum(body, st, "Opacity", "opacity", { min: 0.05, max: 1, step: 0.05 }); fNum(body, st, "Rotation", "rotation", { min: -180, max: 180, step: 15 });
        fChips(body, st, "", [["tile", "Tile"]]); fFile(body, st, "Image", "image/*", "assetImage"); } },
    number: { title: "Page numbers", blurb: "Page {n} of {total}",
      cfg: () => ({ format: "Page {n} of {total}", position: "bottom-center", start: 1 }),
      summary: (c) => `${c.format} · ${c.position.replace("-", " ")}`,
      rule: (c) => ({ action: "number", format: c.format, position: c.position, start: +c.start || 1 }),
      form: (body, st) => { fText(body, st, "Format", "format", "Page {n} of {total}"); fSel(body, st, "Position", "position", POSITIONS.map((p) => [p, p])); fNum(body, st, "Start at", "start", { min: 1, step: 1 }); } },
    bates: { title: "Bates numbers", blurb: "legal running numbers",
      cfg: () => ({ prefix: "", digits: 6, start: 1, position: "bottom-right" }),
      summary: (c) => `${c.prefix}${String(c.start).padStart(+c.digits || 6, "0")}… · ${c.position.replace("-", " ")}`,
      rule: (c) => ({ action: "bates", prefix: c.prefix, digits: +c.digits || 6, start: +c.start || 1, position: c.position }),
      form: (body, st) => { fText(body, st, "Prefix", "prefix", "ACME-"); fNum(body, st, "Digits", "digits", { min: 1, max: 12, step: 1 }); fNum(body, st, "Start at", "start", { min: 1, step: 1 }); fSel(body, st, "Position", "position", POSITIONS.map((p) => [p, p])); } },
    stamp: { title: "Stamp", blurb: "an image or PDF overlay",
      cfg: () => ({ assetImage: null, assetPdf: null, assetName: "", position: "center", scale: 0.4, opacity: 1, under: false }),
      summary: (c) => (c.assetImage || c.assetPdf) ? `${c.assetName || "file"} · ${c.position.replace("-", " ")}` : "choose an image or PDF",
      rule: (c) => (c.assetImage || c.assetPdf) ? { action: "stamp", ...(c.assetImage ? { assetImage: c.assetImage } : { assetPdf: c.assetPdf }),
        position: c.position, scale: +c.scale, opacity: +c.opacity, under: !!c.under } : null,
      form: (body, st) => { fFile(body, st, "File", "image/*,application/pdf", "assetImage"); fSel(body, st, "Position", "position", POSITIONS.map((p) => [p, p]));
        fNum(body, st, "Scale", "scale", { min: 0.05, max: 1, step: 0.05 }); fNum(body, st, "Opacity", "opacity", { min: 0.05, max: 1, step: 0.05 }); fChips(body, st, "", [["under", "Under content"]]); } },
    pages: { title: "Pages", blurb: "keep, drop, reorder",
      cfg: () => ({ keep: "" }),
      summary: (c) => c.keep.trim() ? `keep ${c.keep}` : "set pages to keep",
      rule: (c) => c.keep.trim() ? { action: "pages", keep: c.keep.trim() } : null,
      form: (body, st) => fText(body, st, "Keep", "keep", "1-3,5 or 3,1,2") },
    rotate: { title: "Rotate", blurb: "turn pages by 90° steps",
      cfg: () => ({ degrees: 90, pages: "" }),
      summary: (c) => `${c.degrees}°${c.pages.trim() ? " · pages " + c.pages : " · all pages"}`,
      rule: (c) => ({ action: "rotate", degrees: +c.degrees, ...(c.pages.trim() ? { pages: c.pages.trim() } : {}) }),
      form: (body, st) => { fSel(body, st, "Degrees", "degrees", [["90", "90°"], ["180", "180°"], ["270", "270°"]]); fText(body, st, "Pages", "pages", "all"); } },
    crop: { title: "Crop", blurb: "trim page margins",
      cfg: () => ({ margins: "20,20,20,20", pages: "" }),
      summary: (c) => `trim ${c.margins}pt${c.pages.trim() ? " · pages " + c.pages : ""}`,
      rule: (c) => { const m = c.margins.split(",").map((x) => parseFloat(x)); return m.length === 4 && m.every((x) => !isNaN(x))
        ? { action: "crop", margins: m, ...(c.pages.trim() ? { pages: c.pages.trim() } : {}) } : null; },
      form: (body, st) => { fText(body, st, "Margins", "margins", "left,bottom,right,top pt"); fText(body, st, "Pages", "pages", "all"); } },
    meta: { title: "Set metadata", blurb: "title, author…",
      cfg: () => ({ metaField: "Title", metaValue: "" }),
      summary: (c) => `${c.metaField} = ${q(c.metaValue)}`,
      rule: (c) => ({ action: "meta", metaField: c.metaField, metaValue: c.metaValue }),
      form: (body, st) => { fSel(body, st, "Field", "metaField", ["Title", "Author", "Subject", "Keywords", "Creator", "Producer"].map((f) => [f, f])); fText(body, st, "Value", "metaValue", ""); } },
    stripmeta: { title: "Strip metadata", blurb: "remove all document info",
      cfg: () => ({}), summary: () => "all metadata removed", rule: () => ({ action: "stripmeta" }), form: () => {} },
    clean: { title: "Clean scan", blurb: "pure white pages, crisp ink",
      cfg: () => ({ strength: "standard", bilevel: false }),
      summary: (c) => `${c.strength}${c.bilevel ? " · pure black & white" : ""}`,
      rule: (c) => ({ action: "clean", strength: c.strength, ...(c.bilevel ? { bilevel: true } : {}) }),
      form: (body, st) => { fSel(body, st, "Strength", "strength", [["gentle", "gentle — light shadows only"], ["standard", "standard — most scans"], ["strong", "strong — dark or stained pages"]]);
        fChips(body, st, "", [["bilevel", "Pure black & white"]]); } },
  };

  // ---------- the Stack: form field helpers ----------
  function fRow(body, label) {
    const row = el("div", "wb-frow");
    row.appendChild(el("span", "wb-flabel", esc(label)));
    body.appendChild(row);
    return row;
  }
  function fInput(body, st, label, key, ph, type, attrs) {
    const row = fRow(body, label);
    const i = el("input", "wb-fin"); i.type = type; i.placeholder = ph || ""; i.value = st.cfg[key] ?? "";
    Object.assign(i, attrs || {});
    i.oninput = () => { st.cfg[key] = type === "number" ? i.value : i.value; stackChanged(st, false); };
    row.appendChild(i);
  }
  const fText = (body, st, label, key, ph) => fInput(body, st, label, key, ph, "text");
  const fNum = (body, st, label, key, attrs) => fInput(body, st, label, key, "", "number", attrs);
  function fSel(body, st, label, key, options) {
    const row = fRow(body, label);
    const s = el("select", "wb-fin");
    options.forEach(([v, l]) => { const o = el("option", null, esc(l)); o.value = v; s.appendChild(o); });
    s.value = String(st.cfg[key]);
    s.onchange = () => { st.cfg[key] = s.value; stackChanged(st, false); };
    row.appendChild(s);
  }
  // chips: with listKey, a multi-select feeding cfg[listKey]; without, boolean toggles on cfg[key]
  function fChips(body, st, label, items, listKey) {
    const row = fRow(body, label);
    const wrap = el("div", "wb-chips");
    items.forEach(([k, l]) => {
      const c = el("button", "wb-chip", esc(l)); c.type = "button";
      const on = () => listKey ? st.cfg[listKey].includes(k) : !!st.cfg[k];
      c.classList.toggle("on", on());
      c.onclick = () => {
        if (listKey) st.cfg[listKey] = on() ? st.cfg[listKey].filter((x) => x !== k) : [...st.cfg[listKey], k];
        else st.cfg[k] = !st.cfg[k];
        c.classList.toggle("on", on()); stackChanged(st, false);
      };
      wrap.appendChild(c);
    });
    row.appendChild(wrap);
  }
  function fFile(body, st, label, accept, kind) {
    const row = fRow(body, label);
    const btn = el("button", "wb-fpick", st.cfg.assetName ? esc(st.cfg.assetName) : "choose…"); btn.type = "button";
    const inp = el("input"); inp.type = "file"; inp.accept = accept; inp.hidden = true;
    btn.onclick = () => inp.click();
    inp.onchange = async () => {
      const f = inp.files[0]; if (!f) return;
      btn.textContent = "uploading…";
      const d = await post("wbasset", { session: await ensureSession(), data: await fileToB64(f), name: f.name });
      if (!d.ok) { btn.textContent = "failed — retry"; return; }
      const isPdf = /\.pdf$/i.test(f.name);
      if (kind === "assetImage" && isPdf && "assetPdf" in st.cfg) { st.cfg.assetPdf = d.assetId; st.cfg.assetImage = null; }
      else st.cfg[kind] = d.assetId;
      st.cfg.assetName = f.name; btn.textContent = esc(f.name);
      stackChanged(st, true);
    };
    row.append(btn, inp);
  }

  // ---------- the Stack: cards ----------
  const stepsEl = $(".wb-steps"), menuEl = $(".wb-stepmenu"), countEl = $(".wb-count");
  const stepById = (uid) => S.stack.find((x) => x.uid === uid);

  function stackRules() {
    // active wire rules + which step each one came from (to hand report entries back)
    const rules = [], srcs = [];
    S.stack.forEach((st) => {
      if (!st.on) return;
      const r = STEPS[st.type].rule(st.cfg);
      if (r) { rules.push(r); srcs.push(st); }
    });
    return { rules, srcs };
  }

  const cleanFont = (f) => String(f || "").replace(/^[A-Z]{6}\+/, "");
  function summaryFor(st) {
    let text = STEPS[st.type].summary(st.cfg), warn = false, broken = false;
    const e = st.entry;
    if (e && st.on) {
      if (e.refused) {
        // the font detect-and-refuse: this step is BROKEN, not just unlucky
        broken = true;
        const hint = /glyph IDs|Type3|vector glyphs/.test(e.reason || "")
          ? "its text is stored as glyph IDs, which pdfblah can't rewrite yet"
          : /missing glyph/.test(e.reason || "")
          ? "try a replacement without those characters"
          : /standard encoding/.test(e.reason || "")
          ? "those characters can't be shown in any standard font"
          : "this font can't take new text";
        text = `${cleanFont(e.font) || "font"} refused — ${hint}`;
      } else if (e.applied === false) { text = e.reason || e.error || "no matches in this file"; warn = true; }
      else if (e.count != null) {
        text += ` · ${e.count} match${e.count === 1 ? "" : "es"}`;
        if (e.substituted) text += ` · ${e.substituted.to} substituted`;
      }
    }
    return { text, warn, broken, full: e && e.reason ? e.reason : "" };
  }

  function syncFixRow(card, st, broken) {
    // the substitution prompt lives on the refused card itself: one explicit click,
    // marked in the report, and only offered where the engine can actually do it
    // never offer substitution where the engine says it can't work (CID/Type3 fonts)
    const offer = broken && st.type === "replace" && !st.cfg.substituteFont
      && !(st.entry && st.entry.substitutable === false);
    let row = card.querySelector(".wb-fixrow");
    if (!offer) { if (row) row.remove(); return; }
    if (row) return;
    row = el("div", "wb-fixrow");
    const b = el("button", "wb-fixbtn", "Use a similar standard font instead"); b.type = "button";
    b.onclick = (e) => { e.stopPropagation(); st.cfg.substituteFont = true; renderStack(); stackChanged(st, true); };
    row.append(b, el("span", "wb-fixnote", "swaps just this text, named in the report"));
    card.appendChild(row);
  }

  function renderStackSummaries() {
    S.stack.forEach((st) => {
      const card = stepsEl.querySelector(`.wb-step[data-uid="${st.uid}"]`);
      const elx = card && card.querySelector(".wb-ssum");
      if (!elx) return;
      const { text, warn, broken, full } = summaryFor(st);
      elx.textContent = text; elx.title = full;
      elx.classList.toggle("warn", warn);
      card.classList.toggle("brk", broken);
      syncFixRow(card, st, broken);
    });
  }

  function addStep(type) {
    const st = { uid: "s" + (++uidSeq), type, on: true, cfg: STEPS[type].cfg(), entry: null };
    S.stack.push(st); S.expanded = st.uid;
    S.view = "after"; // you just added an edit — show its effect
    renderStack(); stackChanged(st, true);
    const f = stepsEl.querySelector(`.wb-step[data-uid="${st.uid}"] input, .wb-step[data-uid="${st.uid}"] select`);
    if (f) f.focus();
  }

  function moveStep(st, d) {
    const i = S.stack.indexOf(st), j = i + d;
    if (j < 0 || j >= S.stack.length) return;
    S.stack.splice(i, 1); S.stack.splice(j, 0, st);
    renderStack(); stackChanged(st, true);
  }

  function renderStack() {
    stepsEl.innerHTML = "";
    countEl.hidden = !S.stack.length; countEl.textContent = S.stack.length;
    renderOutput();
    if (!S.stack.length) {
      // scanned files get a one-click path to the edit made for them
      if (S.files.some((x) => x.id && x.included !== false && x.isScan)) {
        const hint = el("div", "wb-stackhint",
          `This looks like a scan. <b>Clean scan</b> makes the paper pure white and the ink crisp. `);
        const b = el("button", "wb-fixbtn", "Clean it up"); b.type = "button";
        b.onclick = () => addStep("clean");
        hint.appendChild(b);
        stepsEl.appendChild(hint);
      } else {
        stepsEl.appendChild(el("div", "wb-stackhint",
          `Add your first edit: <b>Replace</b>, <b>Redact</b>, <b>Watermark</b>… Edits apply top to bottom, and the <b>After</b> view updates as you type.`));
      }
    }
    S.stack.forEach((st, i) => {
      const { text, warn, broken, full } = summaryFor(st);
      const card = el("div", "wb-step" + (st.on ? "" : " off") + (S.expanded === st.uid ? " open" : "") + (broken ? " brk" : ""));
      card.dataset.uid = st.uid;
      const head = el("div", "wb-shead");
      head.innerHTML = `<span class="wb-sn">${i + 1}</span><div class="wb-smeta"><div class="wb-stitle">${esc(STEPS[st.type].title)}</div><div class="wb-ssum${warn ? " warn" : ""}" title="${esc(full)}">${esc(text)}</div></div>`;
      head.onclick = () => { S.expanded = S.expanded === st.uid ? null : st.uid; renderStack(); };
      const rm = el("button", "wb-srm", "×"); rm.title = "remove this edit";
      rm.onclick = (e) => { e.stopPropagation(); S.stack = S.stack.filter((x) => x !== st); if (S.expanded === st.uid) S.expanded = null; renderStack(); stackChanged(null, true); };
      const sw = el("label", "wb-sw"); sw.title = "edit on/off"; sw.onclick = (e) => e.stopPropagation();
      const cb = el("input"); cb.type = "checkbox"; cb.checked = st.on;
      cb.onchange = () => { st.on = cb.checked; card.classList.toggle("off", !st.on); stackChanged(st, true); };
      sw.append(cb, el("span", "wb-swk"));
      head.append(rm, sw);
      card.appendChild(head);
      if (S.expanded === st.uid) {
        const body = el("div", "wb-sbody");
        STEPS[st.type].form(body, st);
        const foot = el("div", "wb-sfoot");
        const up = el("button", "wb-smv", "↑"); up.title = "move up"; up.disabled = i === 0; up.onclick = () => moveStep(st, -1);
        const dn = el("button", "wb-smv", "↓"); dn.title = "move down"; dn.disabled = i === S.stack.length - 1; dn.onclick = () => moveStep(st, 1);
        foot.append(up, dn);
        body.appendChild(foot);
        card.appendChild(body);
      }
      syncFixRow(card, st, broken);
      // drag reorder on the card header, with a live insertion line
      const clearDrop = () => stepsEl.querySelectorAll(".wb-step").forEach((c) => c.classList.remove("dropb", "dropa"));
      head.draggable = true;
      head.ondragstart = (e) => { e.dataTransfer.setData("text/wb-step", st.uid); e.dataTransfer.effectAllowed = "move"; };
      card.ondragover = (e) => {
        if (!e.dataTransfer.types.includes("text/wb-step")) return;
        e.preventDefault();
        clearDrop();
        card.classList.add(e.offsetY > card.offsetHeight / 2 ? "dropa" : "dropb");
      };
      card.ondragleave = () => card.classList.remove("dropb", "dropa");
      card.ondrop = (e) => {
        e.preventDefault(); clearDrop();
        const src = stepById(e.dataTransfer.getData("text/wb-step"));
        if (!src || src === st) return;
        S.stack.splice(S.stack.indexOf(src), 1);
        S.stack.splice(S.stack.indexOf(st) + (e.offsetY > card.offsetHeight / 2 ? 1 : 0), 0, src);
        renderStack(); stackChanged(src, true);
      };
      stepsEl.appendChild(card);
    });
  }

  const MENU_GROUPS = [
    ["Text", ["replace", "remove", "redact", "scrub", "anonymize"]],
    ["Pages & marks", ["watermark", "number", "bates", "stamp", "pages", "rotate", "crop", "clean"]],
    ["Document", ["meta", "stripmeta"]],
  ];
  function renderStepMenu() {
    menuEl.innerHTML = "";
    MENU_GROUPS.forEach(([label, types]) => {
      menuEl.appendChild(el("div", "wb-menugroup wb-eyebrow", esc(label)));
      types.forEach((type) => {
        const def = STEPS[type];
        const it = el("button", "wb-menuitem", `<b>${esc(def.title)}</b><span>${esc(def.blurb)}</span>`);
        it.onclick = () => { menuEl.hidden = true; addStep(type); };
        menuEl.appendChild(it);
      });
    });
  }

  // ---------- Output (P3): format + download of the stack applied to included files ----------
  const FMTS = [["pdf", "PDF"], ["word", "Word ⚙"], ["png", "PNG"], ["text", "Text"]];
  const includedFiles = () => S.files.filter((x) => x.id && x.included !== false);
  function renderOutput() {
    const inc = includedFiles(), nAll = S.files.filter((x) => x.id).length;
    const steps = stackRules().rules.length;
    // steps are active but the last After render applied none of them (refused fonts,
    // no matches): there is nothing to generate for this file
    const noop = steps > 0 && S.lastApplied === 0;
    const sum = $(".wb-outsum");
    sum.textContent = noop && activeRec()
      ? `no edit applies to ${activeRec().name} — nothing to change`
      : nAll ? `${inc.length} of ${nAll} file${nAll === 1 ? "" : "s"} · ${steps} edit${steps === 1 ? "" : "s"}` : "";
    sum.classList.toggle("warn", noop);
    const fmts = $(".wb-fmts");
    if (!fmts.children.length) {
      FMTS.forEach(([v, l]) => {
        const b = el("button", "wb-fmt", esc(l)); b.dataset.f = v;
        b.onclick = () => { if (b.disabled) return; S.out.format = v; renderOutput(); };
        fmts.appendChild(b);
      });
      $(".wb-omerge").onclick = () => { S.out.merge = !S.out.merge; renderOutput(); };
      $(".wb-otoc").onclick = () => { S.out.toc = !S.out.toc; renderOutput(); };
      $(".wb-otabs").onclick = () => { S.out.tabs = !S.out.tabs; if (S.out.tabs) S.out.toc = true; renderOutput(); };
      $(".wb-otocfont").onchange = (e) => { S.out.tocFont = e.target.value; };
      $(".wb-download").onclick = downloadOutput;
    }
    fmts.querySelectorAll(".wb-fmt").forEach((b) => {
      b.classList.toggle("on", b.dataset.f === S.out.format);
      if (b.dataset.f === "word") {
        b.disabled = !S.formats.word;
        b.title = S.formats.word ? "" : 'editable Word needs the local converter — run: pip install "pdfblah[convert]"';
      }
    });
    const isPdf = S.out.format === "pdf";
    $(".wb-opw").hidden = $(".wb-oopt").hidden = !isPdf;
    const mg = $(".wb-omerge");
    mg.hidden = !(isPdf && inc.length > 1);
    mg.classList.toggle("on", S.out.merge && !mg.hidden);
    // the binder options ride on merge: a clickable Contents page, numbered edge tabs
    const merging = S.out.merge && !mg.hidden;
    const tocB = $(".wb-otoc"), tabsB = $(".wb-otabs"), fontS = $(".wb-otocfont");
    tocB.hidden = tabsB.hidden = !merging;
    fontS.hidden = !(merging && S.out.toc);
    tocB.classList.toggle("on", merging && S.out.toc);
    tabsB.classList.toggle("on", merging && S.out.tabs);
    // pricing is the HOST's business (the hosted site quotes, the local app never
    // shows money): an optional hook returns {text, href?, title?} or null
    const qEl = $(".wb-quote");
    if (typeof host.quote === "function") {
      const q = host.quote({ docs: inc.map((x) => ({ pages: x.pages || 1 })),
                             rules: stackRules().rules, options: {
                               password: $(".wb-opw").value || undefined,
                               downsampleDpi: $(".wb-oopt").value || undefined,
                               toc: (S.out.merge && S.out.toc) || undefined,
                               tabs: (S.out.merge && S.out.tabs) || undefined },
                             format: S.out.format, merge: S.out.merge });
      qEl.hidden = !q || !inc.length;
      if (q && inc.length) {
        qEl.innerHTML = "";
        const node = q.href ? el("a", "wb-quotelink", esc(q.text)) : el("span", null, esc(q.text));
        if (q.href) node.href = q.href;
        if (q.title) node.title = q.title;
        qEl.appendChild(node);
      }
    }
    const dl = $(".wb-download");
    // locked while a preview render is checking fonts, and when the stack can't change
    // the only included file (other files might still match, so multi-file stays live)
    dl.disabled = !inc.length || S.rendering || (noop && inc.length === 1);
    dl.textContent = !inc.length ? "Download"
      : S.rendering ? "Checking…"
      : (noop && inc.length === 1) ? "Nothing to download"
      : (isPdf && S.out.merge && inc.length > 1) ? (S.out.toc ? "Download binder" : "Download merged PDF")
      : `Download ${inc.length} file${inc.length === 1 ? "" : "s"}`;
  }
  async function downloadOutput() {
    const dl = $(".wb-download"), inc = includedFiles();
    if (!inc.length || dl.classList.contains("busy")) return;
    dl.classList.add("busy"); const old = dl.textContent; dl.textContent = "Preparing…";
    const d = await post("wboutput", {
      session: S.session, files: inc.map((x) => x.id), rules: stackRules().rules,
      format: S.out.format, merge: S.out.merge,
      options: { password: $(".wb-opw").value || undefined, downsampleDpi: $(".wb-oopt").value || undefined,
                 toc: (S.out.merge && S.out.toc) || undefined, tabs: (S.out.merge && S.out.tabs) || undefined,
                 tocFont: (S.out.merge && S.out.toc && S.out.tocFont !== "sans") ? S.out.tocFont : undefined },
    });
    dl.classList.remove("busy"); dl.textContent = old;
    if (!d.ok) { $(".wb-outsum").textContent = d.error || "could not produce the output"; return; }
    renderOutput(); // restore the summary if a previous error replaced it
    const a = el("a"); a.href = host.downloadUrl ? host.downloadUrl(d.fileId) : "/download/" + d.fileId;
    a.download = d.name; root.appendChild(a); a.click(); a.remove();
  }

  // ---------- Recipes (P5): named stacks, saved through the host ----------
  // Serialized as [{type, on, cfg}] — session-scoped asset ids are stripped (a recipe
  // outlives the session its images were uploaded into; the step asks to re-pick).
  const recMenu = $(".wb-recmenu"), recList = $(".wb-reclist"), recName = $(".wb-recname");
  const stripAssets = (cfg) => {
    const c = { ...cfg };
    if ("assetImage" in c) c.assetImage = null;
    if ("assetPdf" in c) c.assetPdf = null;
    if ("assetName" in c) c.assetName = "";
    return c;
  };
  function renderRecipes(recipes) {
    recList.innerHTML = "";
    if (!recipes.length) { recList.appendChild(el("div", "wb-recempty", "No recipes yet. Make some edits, name them, Save.")); return; }
    recipes.forEach((r) => {
      const known = r.steps.filter((st) => STEPS[st.type]);
      const row = el("div", "wb-recrow");
      const meta = el("div", "wb-recmeta",
        `<div class="wb-recn">${esc(r.name)}</div><div class="wb-recsub">${known.length} edit${known.length === 1 ? "" : "s"}</div>`);
      meta.onclick = () => { loadRecipe(known); recMenu.hidden = true; };
      const rm = el("button", "wb-frm", "×"); rm.title = "delete recipe";
      rm.onclick = async (e) => { e.stopPropagation(); const d = await post("wbrecipes", { op: "delete", name: r.name }); if (d.ok) renderRecipes(d.recipes); };
      row.append(meta, rm);
      recList.appendChild(row);
    });
  }
  function loadRecipe(steps) {
    S.stack = steps.map((st) => ({ uid: "s" + (++uidSeq), type: st.type, on: st.on !== false,
                                   cfg: { ...STEPS[st.type].cfg(), ...(st.cfg || {}) }, entry: null }));
    S.expanded = null;
    S.view = "after";
    switchTab("stack");
    renderStack(); stackChanged(null, true);
  }
  async function openRecipes(focusName) {
    recMenu.hidden = false;
    renderRecipes([]); recList.firstChild.textContent = "loading…";
    if (focusName) recName.focus();
    const d = await post("wbrecipes", { op: "list" });
    if (recMenu.hidden) return;
    renderRecipes(d.ok ? d.recipes : []);
  }
  async function saveRecipe() {
    const name = recName.value.trim();
    if (!name || !S.stack.length) { recName.placeholder = S.stack.length ? "name these edits…" : "no edits to save yet"; recName.focus(); return; }
    const steps = S.stack.map((st) => ({ type: st.type, on: st.on, cfg: stripAssets(st.cfg) }));
    const d = await post("wbrecipes", { op: "save", name, steps });
    if (!d.ok) { recName.value = ""; recName.placeholder = d.error || "could not save"; return; }
    recName.value = ""; recName.placeholder = "saved ✓";
    setTimeout(() => { recName.placeholder = "name these edits…"; }, 1600);
    renderRecipes(d.recipes);
  }
  $(".wb-recbtn").onclick = () => { if (recMenu.hidden) openRecipes(false); else recMenu.hidden = true; };
  $(".wb-recsavebtn").onclick = saveRecipe;
  recName.addEventListener("keydown", (e) => { if (e.key === "Enter") saveRecipe(); });
  $(".wb-reciperow").onclick = () => openRecipes(true);

  // ---------- Inspect (P4): read-only document facts ----------
  // Metadata / fonts / security come straight from the wbadd analysis (no round-trip);
  // forms / signatures / attachments load lazily via wbinspect, once per file.
  const inspectEl = $(".wb-inspect");
  function isect(title) {
    const s = el("div", "wb-isect");
    s.appendChild(el("div", "wb-eyebrow wb-ihead", esc(title)));
    inspectEl.appendChild(s);
    return s;
  }
  function irow(sec, k, v) { sec.appendChild(el("div", "wb-irow", `<span>${esc(k)}</span><span>${esc(v)}</span>`)); }
  function inote(sec, text, warn) { sec.appendChild(el("div", "wb-inote" + (warn ? " warn" : ""), esc(text))); }
  function ilink(sec, label, fn) {
    const b = el("button", "wb-ilink", esc(label)); b.type = "button"; b.onclick = fn;
    sec.appendChild(b);
  }
  async function renderInspect() {
    const rec = activeRec();
    inspectEl.innerHTML = "";
    if (!rec) { inspectEl.appendChild(el("div", "wb-soon", "Add a PDF to inspect it.")); return; }
    const a = rec.analysis || {};
    const meta = a.metadata || { docinfo: {}, xmp: {}, fields: 0 };

    const doc = isect("Document");
    const di = meta.docinfo || {};
    const order = ["Title", "Author", "Subject", "Keywords", "Creator", "Producer", "CreationDate", "ModDate"];
    const keys = [...order.filter((k) => k in di), ...Object.keys(di).filter((k) => !order.includes(k))].slice(0, 10);
    keys.forEach((k) => irow(doc, k.replace("CreationDate", "Created").replace("ModDate", "Modified"),
      String(di[k]).replace(/^\//, ""))); // "/False" etc: bare PDF name objects read better without the slash
    irow(doc, "Pages", String(a.pages ?? "?"));
    irow(doc, "Size", fmtSize(a.size || rec.size || 0));
    const nx = Object.keys(meta.xmp || {}).length;
    if (nx) inote(doc, `+ ${nx} XMP field${nx === 1 ? "" : "s"}`);
    if (!keys.length && !nx) inote(doc, "No metadata set.");
    else ilink(doc, "Strip all → add to stack", () => { addStep("stripmeta"); switchTab("stack"); });

    const fonts = isect("Fonts");
    const fp = (a.fonts && a.fonts.problems) || [];
    if (!a.fonts || !a.fonts.total) inote(fonts, "No fonts detected (image-only pages?).");
    else if (!fp.length) inote(fonts, `${a.fonts.total} font${a.fonts.total === 1 ? "" : "s"} · all reproducible for text edits`);
    else {
      inote(fonts, `${fp.length} of ${a.fonts.total} fonts cannot be reproduced — text edits using them will refuse:`, true);
      fp.slice(0, 6).forEach((p) => irow(fonts, p.font, p.reason));
    }

    const sec = isect("Security");
    inote(sec, a.encrypted ? "Password-protected — unlock it before editing." : "Not encrypted.", !!a.encrypted);
    if (!a.encrypted) ilink(sec, "Protect → set a password in Output", () => $(".wb-opw").focus());

    const sigSec = isect("Signatures"), attSec = isect("Attachments"), formSec = isect("Form fields");
    [sigSec, attSec, formSec].forEach((x) => inote(x, "loading…"));
    inspectEl.appendChild(el("div", "wb-ifoot", "Inspect is read-only — nothing here changes the file until you add it to the stack."));

    if (!rec.inspect) rec.inspect = await post("wbinspect", { session: S.session, file: rec.id });
    if (activeRec() !== rec || !inspectEl.contains(sigSec)) return; // switched away meanwhile
    const d = rec.inspect;
    [sigSec, attSec, formSec].forEach((x) => x.querySelector(".wb-inote").remove());
    const sigs = d.signatures?.signatures || [];
    if (!d.signatures?.ok) inote(sigSec, d.signatures?.error || "could not read signatures", true);
    else if (!sigs.length) inote(sigSec, "No digital signatures found.");
    else sigs.forEach((g) => irow(sigSec, g.field || g.name || "signature", [g.name, g.reason, g.time].filter(Boolean).join(" · ") || "present"));
    const atts = d.attachments?.attachments || [];
    if (!d.attachments?.ok) inote(attSec, d.attachments?.error || "could not read attachments", true);
    else if (!atts.length) inote(attSec, "No embedded attachments.");
    else atts.forEach((t) => {
      const row = el("div", "wb-irow wb-iatt", `<span>${esc(t.name)}</span><span>${t.size != null ? esc(fmtSize(t.size)) : ""}</span>`);
      const ex = el("button", "wb-ilink", "Extract"); ex.type = "button";
      ex.onclick = async () => {
        const r = await post("wbextract", { session: S.session, file: rec.id, name: t.name });
        if (!r.ok) { ex.textContent = r.error || "failed"; return; }
        const link = el("a"); link.href = host.downloadUrl ? host.downloadUrl(r.fileId) : "/download/" + r.fileId;
        link.download = r.name; root.appendChild(link); link.click(); link.remove();
      };
      row.appendChild(ex); attSec.appendChild(row);
    });
    const fields = d.forms?.fields || [];
    if (!d.forms?.ok) inote(formSec, d.forms?.error || "could not read form fields", true);
    else if (!fields.length) inote(formSec, "0 interactive fields.");
    else {
      fields.slice(0, 12).forEach((f) => irow(formSec, f.name || "(unnamed)", `${f.type}${f.value ? " · " + f.value : ""}`));
      if (fields.length > 12) inote(formSec, `and ${fields.length - 12} more`);
    }
  }

  // a stack edit: refresh this card's summary now, and (debounced) re-render the After view.
  // structural=true (add/remove/toggle/reorder/asset) skips the debounce.
  let pvTimer = null;
  function stackChanged(st, structural) {
    if (st) renderStackSummaries();
    S.lastApplied = null; // stale until the next After render reports back
    renderOutput(); // active-step count / button label track validity as you type
    S.afterPages = null; // page count may change (a `pages` step)
    clearTimeout(pvTimer);
    if (S.view !== "after") return; // Before view: nothing to re-render
    pvTimer = setTimeout(() => renderCenter(), structural ? 0 : 250);
  }

  // ---------- center preview ----------
  // Both views render through wbpreview (same renderer, so Before/After compare cleanly):
  // Before = no rules, After = the active stack. Coalesced by token — the newest request
  // wins, stale responses are dropped. Page thumbnails deliberately keep showing the
  // ORIGINAL pages; visual page ops on thumbnails come in a later phase.
  let renderToken = 0;
  function pageCount() {
    const rec = activeRec();
    return (S.view === "after" && S.afterPages != null ? S.afterPages : rec?.pages) || 1;
  }
  // zoom: "fit" scales to the pane; a number is a real-size percentage (100% = the page at
  // 96 css-px/inch, like a desktop PDF viewer). Pure CSS width — zooming never re-renders.
  const ZOOMS = [50, 67, 80, 100, 125, 150, 200, 300];
  function zoomedWidth(img, pct) { return img.naturalWidth * (96 / S.dpi) * (pct / 100); }
  function effectiveZoom(img) {
    if (S.zoom !== "fit") return S.zoom;
    if (!img || !img.naturalWidth) return 100;
    return Math.round((img.getBoundingClientRect().width / zoomedWidth(img, 100)) * 100);
  }
  function applyZoom() {
    const img = stageEl.querySelector(".wb-page");
    if (img && img.naturalWidth) {
      if (S.zoom === "fit") { img.style.width = ""; img.style.maxWidth = "100%"; }
      else { img.style.maxWidth = "none"; img.style.width = zoomedWidth(img, S.zoom) + "px"; }
    }
    const lbl = viewtop.querySelector(".wb-zlabel");
    if (lbl) lbl.textContent = (S.zoom === "fit" ? "fit · " : "") + effectiveZoom(img) + "%";
    const fit = viewtop.querySelector('.wb-zbtn[data-z="fit"]');
    if (fit) fit.classList.toggle("on", S.zoom === "fit");
  }
  function stepZoom(d) {
    const img = stageEl.querySelector(".wb-page");
    const cur = effectiveZoom(img);
    const next = d > 0 ? ZOOMS.find((z) => z > cur + 2) : [...ZOOMS].reverse().find((z) => z < cur - 2);
    if (next) { S.zoom = next; applyZoom(); }
  }
  function drawChrome(rec) {
    // built once, then mutated in place — replacing these nodes mid-interaction would yank
    // buttons out from under a click (the chrome redraws on every preview response)
    if (!viewtop.querySelector(".wb-viewseg")) {
      viewtop.innerHTML =
        `<span class="wb-vname"></span><span class="wb-busy" hidden></span>` +
        `<div class="wb-viewseg"><button class="wb-vbtn" data-v="before">Before</button><button class="wb-vbtn" data-v="after">After</button></div>` +
        `<span class="wb-zoom"></span>` +
        `<div class="wb-zctl"><button class="wb-zbtn" data-z="-" title="zoom out">−</button><span class="wb-zlabel"></span><button class="wb-zbtn" data-z="+" title="zoom in">+</button><button class="wb-zbtn" data-z="fit" title="fit to window">Fit</button></div>`;
      viewtop.querySelectorAll(".wb-vbtn").forEach((b) => b.onclick = () => { if (S.view === b.dataset.v) return; S.view = b.dataset.v; renderCenter(); });
      viewtop.querySelector('.wb-zbtn[data-z="-"]').onclick = () => stepZoom(-1);
      viewtop.querySelector('.wb-zbtn[data-z="+"]').onclick = () => stepZoom(1);
      viewtop.querySelector('.wb-zbtn[data-z="fit"]').onclick = () => { S.zoom = "fit"; applyZoom(); };
      navEl.innerHTML = `<button class="wb-navb" data-d="-1">&lsaquo;</button><span></span><button class="wb-navb" data-d="1">&rsaquo;</button>`;
      navEl.querySelectorAll(".wb-navb").forEach((b) => b.onclick = () => { S.page = Math.min(pageCount(), Math.max(1, S.page + (+b.dataset.d))); renderCenter(); markThumb(); });
    }
    const total = pageCount();
    viewtop.querySelector(".wb-vname").textContent = rec.name;
    viewtop.querySelectorAll(".wb-vbtn").forEach((b) => b.classList.toggle("on", b.dataset.v === S.view));
    viewtop.querySelector(".wb-zoom").textContent = `page ${S.page} / ${total}`;
    applyZoom();
    navEl.hidden = false;
    navEl.querySelector("span").textContent = `${S.page} / ${total}`;
    navEl.querySelector('[data-d="-1"]').disabled = S.page <= 1;
    navEl.querySelector('[data-d="1"]').disabled = S.page >= total;
  }
  let lastPageKey = null;
  function setRendering(on, checkingFonts) {
    S.rendering = on;
    const veil = $(".wb-veil");
    // the veil locks the preview pane while the server applies + checks fonts, so "is
    // anything happening?" is never a question; typing in the rail stays free
    veil.hidden = !on || !checkingFonts;
    if (!veil.hidden) veil.querySelector("span").textContent = "checking fonts · applying your edits…";
    const busy = viewtop.querySelector(".wb-busy");
    if (busy) busy.hidden = !on;
    renderOutput(); // Download locks while a render is in flight
  }
  async function renderCenter() {
    const rec = activeRec();
    if (!rec) { stageEl.innerHTML = `<div class="wb-empty">Drop PDFs here to start<span>or use + Add</span></div>`; navEl.hidden = true; viewtop.innerHTML = ""; errbar.hidden = true; $(".wb-veil").hidden = true; return; }
    drawChrome(rec);
    const { rules, srcs } = S.view === "after" ? stackRules() : { rules: [], srcs: [] };
    const myToken = ++renderToken;
    if (!stageEl.querySelector(".wb-page")) stageEl.innerHTML = `<div class="wb-loading">rendering…</div>`;
    setRendering(true, rules.length > 0);
    const d = await post("wbpreview", { session: S.session, file: rec.id, page: S.page, dpi: S.dpi, rules });
    if (myToken !== renderToken) return;
    setRendering(false, false);
    let img = stageEl.querySelector(".wb-page");
    if (!d.ok) {
      // keep the last good page on screen; a mid-typed rule (half a regex) shouldn't blank
      // the document — surface the error in a slim banner instead
      if (img) { errbar.textContent = d.error || "could not render"; errbar.classList.remove("info"); errbar.hidden = false; }
      else stageEl.innerHTML = `<div class="wb-empty">${esc(d.error || "could not render")}</div>`;
      return;
    }
    const noopNow = S.view === "after" && rules.length > 0 && d.report && d.report.applied === 0;
    errbar.textContent = noopNow ? "No step applies to this file yet, so After matches Before." : "";
    errbar.classList.toggle("info", noopNow);
    errbar.hidden = !noopNow;
    if (!img || img.src !== d.image) {
      // decode off-screen first, then swap src in place: no white flash, and the stage keeps
      // its scroll position on a same-page re-render (tweaking a footer step stays put).
      // identical output (a stack that changed nothing) skips the swap entirely — no blink.
      const pre = new Image(); pre.src = d.image;
      await pre.decode().catch(() => {});
      if (myToken !== renderToken) return;
      img = stageEl.querySelector(".wb-page");
      if (img) img.src = d.image;
      else { stageEl.innerHTML = `<img class="wb-page" src="${d.image}" alt="page ${d.page}">`; img = stageEl.querySelector(".wb-page"); }
    }
    img.alt = `page ${d.page}`;
    const key = rec.id + ":" + d.page;
    if (key !== lastPageKey) stageEl.scrollTop = stageEl.scrollLeft = 0;
    lastPageKey = key;
    if (S.view === "after") {
      S.afterPages = d.pages;
      S.lastApplied = d.report ? d.report.applied : null;
      if (d.page !== S.page) { S.page = d.page; markThumb(); } // stack shrank the doc
      // hand each step its slice of the report (match counts, refusals)
      S.stack.forEach((st) => { if (!srcs.includes(st)) st.entry = null; });
      (d.report?.rules || []).forEach((entry, i) => { if (srcs[i]) srcs[i].entry = entry; });
      renderStackSummaries();
      renderOutput(); // the no-op guard reads lastApplied
    }
    drawChrome(rec); // page count may have just changed
  }

  // ---------- wiring ----------
  const inp = $(".wb-fileinput");
  $(".wb-add").onclick = () => inp.click();
  inp.onchange = () => { addFiles([...inp.files]); inp.value = ""; };
  root.addEventListener("dragover", (e) => { e.preventDefault(); root.classList.add("wb-drag"); });
  root.addEventListener("dragleave", (e) => { if (e.target === root) root.classList.remove("wb-drag"); });
  root.addEventListener("drop", (e) => { e.preventDefault(); root.classList.remove("wb-drag"); if (e.dataTransfer.files.length) addFiles([...e.dataTransfer.files]); });
  function switchTab(t) {
    root.querySelectorAll(".wb-tab").forEach((x) => x.classList.toggle("on", x.dataset.t === t));
    root.querySelectorAll(".wb-tabbody").forEach((x) => { x.hidden = x.dataset.t !== t; });
    if (t === "inspect") renderInspect();
  }
  const inspectVisible = () => !root.querySelector('.wb-tabbody[data-t="inspect"]').hidden;
  root.querySelectorAll(".wb-tab").forEach((b) => b.onclick = () => switchTab(b.dataset.t));
  $(".wb-addstep").onclick = () => { menuEl.hidden = !menuEl.hidden; };
  root.addEventListener("click", (e) => {
    if (!menuEl.hidden && !menuEl.contains(e.target) && !e.target.closest(".wb-addstep")) menuEl.hidden = true;
    if (!recMenu.hidden && !recMenu.contains(e.target) && !e.target.closest(".wb-recbtn") && !e.target.closest(".wb-reciperow")) recMenu.hidden = true;
  });
  // keyboard: ←/→ turn pages (outside form fields), Esc closes the menus
  root.ownerDocument.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && (!menuEl.hidden || !recMenu.hidden)) { menuEl.hidden = true; recMenu.hidden = true; return; }
    if (/^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName)) return;
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    if (!activeRec()) return;
    const next = Math.min(pageCount(), Math.max(1, S.page + (e.key === "ArrowRight" ? 1 : -1)));
    if (next !== S.page) { e.preventDefault(); S.page = next; renderCenter(); markThumb(); }
  });

  renderStepMenu();
  renderStack();
  renderCenter();
  ensureSession();
  return { getState: () => S, addFiles, addStep };
}
