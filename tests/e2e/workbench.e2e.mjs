// Browser E2E of the whole workbench: spawns the real gui server, drives the real UI
// with headless Chrome (uploads, live preview, Stack editing, Output downloads via CDP,
// Inspect, Recipes), screenshots each stage, then kills everything and exits.
//
// Self-contained: generates its fixtures (make_fixtures.py), needs only
//   npm i puppeteer-core  +  a Chrome/Chromium binary  +  the package installed (-e .[test]).
// Env: E2E_CHROME (browser path), E2E_PYTHON (python with pdfblah installed), E2E_WORK (dir).
import { execFileSync, spawn } from "node:child_process";
import { once } from "node:events";
import { existsSync, readFileSync, readdirSync, mkdirSync, rmSync } from "node:fs";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import puppeteer from "puppeteer-core";

const REPO = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const SCRATCH = process.env.E2E_WORK || mkdtempSync(`${tmpdir()}/pdfblah-e2e-`);
const PYBIN_DEFAULT = process.env.E2E_PYTHON || "python3";
const CHROME = process.env.E2E_CHROME || ["/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser"].find(existsSync);
if (!CHROME) { console.error("no Chrome/Chromium found; set E2E_CHROME"); process.exit(1); }
mkdirSync(SCRATCH, { recursive: true });
console.log(`work dir: ${SCRATCH}  browser: ${CHROME}`);
execFileSync(PYBIN_DEFAULT, [resolve(REPO, "tests/e2e/make_fixtures.py"), SCRATCH], { stdio: "inherit" });

const fail = (m) => { console.error("FAIL:", m); process.exitCode = 1; };
const ok = (m) => console.log("ok  ✓", m);

// --- start the gui server on a fixed port via a tiny python bootstrap ---
const PORT = 8791;
const PYBIN = PYBIN_DEFAULT;
const py = `
import sys; sys.path.insert(0, ${JSON.stringify(REPO)})
import pdfblah.gui.server as S
assert S.__file__.startswith(${JSON.stringify(REPO)}), "loaded wrong pdfblah: " + S.__file__
from http.server import ThreadingHTTPServer
httpd = ThreadingHTTPServer(("127.0.0.1", ${PORT}), S._Handler)
print("SERVER_UP", flush=True)
httpd.serve_forever()
`;
rmSync(`${SCRATCH}/xdg-ui`, { recursive: true, force: true }); // hermetic recipes store per run
const server = spawn(PYBIN, ["-c", py], { cwd: REPO, stdio: ["ignore", "pipe", "pipe"],
  env: { ...process.env, XDG_CONFIG_HOME: `${SCRATCH}/xdg-ui` } });
let serverErr = "";
let serverExited = null;
server.stderr.on("data", (d) => { serverErr += d; });
server.on("exit", (c, sig) => { serverExited = { c, sig }; });
await new Promise((res, rej) => {
  const to = setTimeout(() => rej(new Error("server never came up:\n" + serverErr)), 15000);
  server.stdout.on("data", (d) => { if (String(d).includes("SERVER_UP")) { clearTimeout(to); res(); } });
  server.on("exit", (c) => rej(new Error("server exited " + c + "\n" + serverErr)));
});
ok("gui server up on :" + PORT);

const base = `http://127.0.0.1:${PORT}`;
let browser;
try {
  browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: "new",
    args: ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--window-size=1400,900"],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1400, height: 900 });
  const pageErrors = [];
  page.on("pageerror", (e) => pageErrors.push(String(e)));
  // real JS console errors still count; the generic "Failed to load resource" line is just
  // Chrome echoing an HTTP status — covered precisely by the response listener below.
  page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) pageErrors.push("console: " + m.text()); });
  // ignore the browser's automatic /favicon.ico request (app-wide, not page-emitted)
  page.on("requestfailed", (r) => { if (!/favicon\.ico$/.test(r.url())) pageErrors.push("requestfailed: " + r.url()); });
  page.on("response", (r) => { if (r.status() >= 400 && !/favicon\.ico$/.test(r.url())) pageErrors.push(`http ${r.status()}: ${r.url()}`); });

  await page.goto(`${base}/workbench`, { waitUntil: "networkidle0", timeout: 20000 });

  // 3-pane shell present?
  const panes = await page.$$eval(".wb-left, .wb-center, .wb-right", (n) => n.length);
  if (panes === 3) ok("3-pane shell rendered"); else fail("expected 3 panes, got " + panes);
  const emptyTxt = await page.$eval(".wb-empty", (n) => n.textContent).catch(() => "");
  if (/Drop PDFs/.test(emptyTxt)) ok("empty state shows drop prompt"); else fail("no empty-state prompt: " + emptyTxt);
  await page.screenshot({ path: `${SCRATCH}/shot-1-empty.png` });

  // --- upload both PDFs through the hidden file input ---
  const input = await page.$(".wb-fileinput");
  await input.uploadFile(`${SCRATCH}/report.pdf`, `${SCRATCH}/scanned.pdf`);

  // wait for two file rows with resolved ids (no longer "reading…")
  await page.waitForFunction(() => {
    const rows = [...document.querySelectorAll(".wb-file")];
    return rows.length === 2 && rows.every((r) => !/reading…/.test(r.textContent));
  }, { timeout: 30000 });
  ok("both files added to session rail");

  const rows = await page.$$eval(".wb-file", (ns) => ns.map((r) => ({
    name: r.querySelector(".wb-fn")?.textContent,
    sub: r.querySelector(".wb-fsub")?.textContent?.trim(),
    scan: !!r.querySelector(".wb-scan"),
    active: r.classList.contains("active"),
    err: r.classList.contains("err"),
  })));
  console.log("   rows:", JSON.stringify(rows));
  const report = rows.find((r) => r.name === "report.pdf");
  const scan = rows.find((r) => r.name === "scanned.pdf");
  if (report && /3 pages/.test(report.sub) && !report.scan) ok("report.pdf → 3 pages, not flagged scan");
  else fail("report.pdf metadata wrong: " + JSON.stringify(report));
  if (scan && scan.scan) ok("scanned.pdf → scan badge shown"); else fail("scanned.pdf missing scan badge: " + JSON.stringify(scan));
  if (rows.some((r) => r.err)) fail("a file row is in error state");

  // center preview should have rendered the active file's page 1
  await page.waitForSelector(".wb-page", { timeout: 20000 });
  const previewOk = await page.$eval(".wb-page", (img) => img.complete && img.naturalWidth > 0 && img.src.startsWith("data:image/png"));
  if (previewOk) ok("center preview rendered a page PNG"); else fail("center preview image not loaded");

  // page thumbnails for the 3-page active file
  await page.waitForFunction(() => document.querySelectorAll(".wb-thumb").length === 3, { timeout: 20000 });
  ok("3 page thumbnails in rail");
  // thumbs get background images filled in sequentially
  await page.waitForFunction(() =>
    [...document.querySelectorAll(".wb-thumbimg")].filter((t) => t.style.backgroundImage && t.style.backgroundImage !== "none").length >= 1,
    { timeout: 20000 });
  ok("thumbnail images populated");
  await page.screenshot({ path: `${SCRATCH}/shot-2-loaded.png` });

  // --- page navigation: click next ---
  const navLabelBefore = await page.$eval(".wb-nav span", (n) => n.textContent.trim());
  await page.click('.wb-navb[data-d="1"]');
  await page.waitForFunction(() => /(^|\s)2 \/ 3/.test(document.querySelector(".wb-nav span")?.textContent || ""), { timeout: 20000 });
  const navLabelAfter = await page.$eval(".wb-nav span", (n) => n.textContent.trim());
  ok(`page nav works (${navLabelBefore} → ${navLabelAfter})`);
  await page.screenshot({ path: `${SCRATCH}/shot-3-page2.png` });

  // --- switch active file to the scan by clicking its meta ---
  await page.evaluate(() => {
    const scanRow = [...document.querySelectorAll(".wb-file")].find((r) => r.querySelector(".wb-fn")?.textContent === "scanned.pdf");
    scanRow.querySelector(".wb-fmeta").click();
  });
  await page.waitForFunction(() => /scanned\.pdf/.test(document.querySelector(".wb-vname")?.textContent || ""), { timeout: 20000 });
  await page.waitForFunction(() => document.querySelectorAll(".wb-thumb").length === 1, { timeout: 20000 });
  ok("switching active file updates preview + thumbnails (1 page)");

  // --- Before/After + Stack/Inspect tab toggles are wired ---
  await page.click('.wb-vbtn[data-v="before"]');
  const beforeOn = await page.$eval('.wb-vbtn[data-v="before"]', (b) => b.classList.contains("on"));
  await page.click('.wb-tab[data-t="inspect"]');
  const inspectOn = await page.$eval('.wb-tab[data-t="inspect"]', (b) => b.classList.contains("on"));
  if (beforeOn && inspectOn) ok("Before/After + Stack/Inspect toggles respond"); else fail("segment toggles not toggling");

  // --- remove the scan file ---
  await page.evaluate(() => {
    const scanRow = [...document.querySelectorAll(".wb-file")].find((r) => r.querySelector(".wb-fn")?.textContent === "scanned.pdf");
    scanRow.querySelector(".wb-frm").click();
  });
  await page.waitForFunction(() => document.querySelectorAll(".wb-file").length === 1, { timeout: 20000 });
  const remaining = await page.$eval(".wb-file .wb-fn", (n) => n.textContent);
  if (remaining === "report.pdf") ok("remove works; active fell back to report.pdf"); else fail("after remove, wrong file: " + remaining);
  await page.screenshot({ path: `${SCRATCH}/shot-4-after-remove.png` });

  // ================= P2: the Stack =================
  const stageSrc = () => page.$eval(".wb-page", (i) => i.src).catch(() => null);
  const waitNewStage = async (old, what) => {
    await page.waitForFunction((prev) => {
      const i = document.querySelector(".wb-page");
      return i && i.complete && i.naturalWidth > 0 && i.src !== prev;
    }, { timeout: 25000 }, old);
    ok(what);
  };
  const cardByTitle = (t) => page.evaluateHandle((title) =>
    [...document.querySelectorAll(".wb-step")].find((c) => c.querySelector(".wb-stitle")?.textContent === title), t);
  const addStepViaMenu = async (title) => {
    await page.click(".wb-addstep");
    await page.waitForSelector(".wb-stepmenu:not([hidden])", { timeout: 5000 });
    await page.evaluate((t) => {
      [...document.querySelectorAll(".wb-menuitem")].find((m) => m.querySelector("b").textContent === t).click();
    }, title);
  };

  // back to the Stack tab + After view (P1 checks left us on Inspect/Before)
  await page.click('.wb-tab[data-t="stack"]');
  const stackVisible = await page.$eval('.wb-tabbody[data-t="stack"]', (n) => !n.hidden);
  if (stackVisible) ok("Stack tab body visible"); else fail("Stack tab body hidden");
  await page.click('.wb-vbtn[data-v="after"]');
  await page.waitForSelector(".wb-page", { timeout: 25000 });

  // --- UX: empty-stack hint, session count, rail caption, grouped menu + Esc ---
  if (await page.$(".wb-stackhint")) ok("empty-stack hint card shown"); else fail("no empty-stack hint");
  const sess = await page.$eval(".wb-sesslabel", (n) => n.textContent);
  if (/Files · 1/.test(sess)) ok(`file list counts files (${sess})`); else fail("files label: " + sess);
  const hintHidden = await page.$eval(".wb-lhint", (n) => n.hidden);
  if (!hintHidden) ok("left-rail include/drop caption visible"); else fail("left-rail caption hidden");
  await page.click(".wb-addstep");
  await page.waitForSelector(".wb-stepmenu:not([hidden])", { timeout: 5000 });
  const groups = await page.$$eval(".wb-menugroup", (ns) => ns.map((n) => n.textContent));
  if (groups.join(",") === "Text,Pages & marks,Document") ok("step menu grouped: " + groups.join(" / ")); else fail("menu groups: " + groups);
  await page.keyboard.press("Escape");
  await page.waitForFunction(() => document.querySelector(".wb-stepmenu").hidden, { timeout: 5000 });
  ok("Esc closes the step menu");

  // --- add a Replace step, type find/replace, expect live preview + match count ---
  const srcOriginal = await stageSrc();
  await addStepViaMenu("Replace");
  await page.waitForSelector(".wb-step.open", { timeout: 5000 });
  ok("Replace step added via + Add step menu");
  await page.type(".wb-step.open .wb-frow:nth-child(1) input", "Acme Corp");
  await page.type(".wb-step.open .wb-frow:nth-child(2) input", "Blahco Ltd");
  await waitNewStage(srcOriginal, "live After preview re-rendered on typing (debounced)");
  await page.waitForFunction(() => /3 matches/.test(
    [...document.querySelectorAll(".wb-step")].find((c) => c.querySelector(".wb-stitle")?.textContent === "Replace")
      ?.querySelector(".wb-ssum")?.textContent || ""), { timeout: 25000 });
  ok("step summary shows report count (3 matches)");
  const badge = await page.$eval(".wb-count", (n) => ({ hidden: n.hidden, text: n.textContent }));
  if (!badge.hidden && badge.text === "1") ok("Stack count badge = 1"); else fail("bad count badge: " + JSON.stringify(badge));
  await page.screenshot({ path: `${SCRATCH}/shot-5-stack-replace.png` });

  // --- Before view shows the original; After shows the edit ---
  const srcAfter = await stageSrc();
  await page.click('.wb-vbtn[data-v="before"]');
  await waitNewStage(srcAfter, "Before view re-rendered");
  await page.click('.wb-vbtn[data-v="after"]');
  await waitNewStage(await stageSrc(), "After view re-rendered");

  // --- toggle the step off -> count clears; on -> returns ---
  const srcOn = await stageSrc();
  await page.evaluate(async (t) => {
    [...document.querySelectorAll(".wb-step")].find((c) => c.querySelector(".wb-stitle")?.textContent === t)
      .querySelector(".wb-sw input").click();
  }, "Replace");
  await page.waitForFunction(() => {
    const c = [...document.querySelectorAll(".wb-step")].find((x) => x.querySelector(".wb-stitle")?.textContent === "Replace");
    return c.classList.contains("off") && !/\d+ match/.test(c.querySelector(".wb-ssum").textContent);
  }, { timeout: 25000 });
  await waitNewStage(srcOn, "toggling step off re-renders + clears its count");
  await page.evaluate((t) => {
    [...document.querySelectorAll(".wb-step")].find((c) => c.querySelector(".wb-stitle")?.textContent === t)
      .querySelector(".wb-sw input").click();
  }, "Replace");
  await page.waitForFunction(() => /3 matches/.test(
    [...document.querySelectorAll(".wb-step")].find((c) => c.querySelector(".wb-stitle")?.textContent === "Replace")
      ?.querySelector(".wb-ssum")?.textContent || ""), { timeout: 25000 });
  ok("toggling step back on restores the edit + count");

  // --- second step + reorder ---
  await addStepViaMenu("Page numbers");
  await page.waitForFunction(() => document.querySelectorAll(".wb-step").length === 2, { timeout: 5000 });
  const order0 = await page.$$eval(".wb-stitle", (ns) => ns.map((n) => n.textContent));
  await page.click(".wb-step.open .wb-smv:first-child"); // ↑ on the expanded (2nd) card
  await page.waitForFunction(() => document.querySelector(".wb-step .wb-stitle")?.textContent === "Page numbers", { timeout: 5000 });
  const order1 = await page.$$eval(".wb-stitle", (ns) => ns.map((n) => n.textContent));
  ok(`reorder via ↑ works (${order0.join(" → ")} became ${order1.join(" → ")})`);
  const badge2 = await page.$eval(".wb-count", (n) => n.textContent);
  if (badge2 === "2") ok("count badge = 2"); else fail("count badge should be 2, got " + badge2);

  // --- a `pages` step changes the After page count; removing it restores ---
  await addStepViaMenu("Pages");
  await page.type(".wb-step.open .wb-frow input", "1-2");
  await page.waitForFunction(() => /1 \/ 2/.test(document.querySelector(".wb-nav span")?.textContent || ""), { timeout: 25000 });
  ok("pages-keep step: After nav shows 1 / 2 (doc shrank)");
  await page.evaluate(() => {
    [...document.querySelectorAll(".wb-step")].find((c) => c.querySelector(".wb-stitle")?.textContent === "Pages")
      .querySelector(".wb-srm").click();
  });
  await page.waitForFunction(() => /1 \/ 3/.test(document.querySelector(".wb-nav span")?.textContent || ""), { timeout: 25000 });
  ok("removing the pages step restores 1 / 3");

  // --- UX: zoom controls (pure CSS, no re-render) ---
  const w0 = await page.$eval(".wb-page", (i) => i.getBoundingClientRect().width);
  await page.click('.wb-zbtn[data-z="+"]');
  const w1 = await page.$eval(".wb-page", (i) => i.getBoundingClientRect().width);
  const zl = await page.$eval(".wb-zlabel", (n) => n.textContent);
  if (w1 > w0 && /%$/.test(zl)) ok(`zoom + widens the page (${Math.round(w0)} → ${Math.round(w1)}px, label ${zl})`);
  else fail(`zoom + didn't widen: ${w0} → ${w1}, label ${zl}`);
  await page.click('.wb-zbtn[data-z="fit"]');
  const zl2 = await page.$eval(".wb-zlabel", (n) => n.textContent);
  if (/^fit · /.test(zl2)) ok(`Fit restores fit mode (${zl2})`); else fail("fit label: " + zl2);

  // --- UX: bad regex mid-typing -> banner over the last good page, not a blank stage ---
  await addStepViaMenu("Replace");
  await page.evaluate(() => {
    const open = document.querySelector(".wb-step.open");
    [...open.querySelectorAll(".wb-chip")].find((c) => c.textContent === "Regex").click();
  });
  await page.type(".wb-step.open .wb-frow:nth-child(1) input", "(");
  await page.waitForSelector(".wb-errbar:not([hidden])", { timeout: 25000 });
  const stillThere = await page.$(".wb-page");
  const errText = await page.$eval(".wb-errbar", (n) => n.textContent);
  if (stillThere && /unterminated|missing/.test(errText)) ok(`bad regex -> error banner over intact page ("${errText.slice(0, 40)}…")`);
  else fail("error banner UX broken: page=" + !!stillThere + " text=" + errText);
  await page.evaluate(() => document.querySelector(".wb-step.open .wb-srm").click());
  await page.waitForFunction(() => document.querySelector(".wb-errbar").hidden, { timeout: 25000 });
  ok("removing the bad step clears the banner");

  // --- UX: keyboard page nav ---
  await page.click(".wb-stage");
  await page.keyboard.press("ArrowRight");
  await page.waitForFunction(() => /2 \/ 3/.test(document.querySelector(".wb-nav span")?.textContent || ""), { timeout: 25000 });
  ok("ArrowRight turns the page (2 / 3)");
  await page.keyboard.press("ArrowLeft");
  await page.waitForFunction(() => /1 \/ 3/.test(document.querySelector(".wb-nav span")?.textContent || ""), { timeout: 25000 });
  ok("ArrowLeft turns back (1 / 3)");

  // ================= P3: Output =================
  const DL = `${SCRATCH}/dl`;
  rmSync(DL, { recursive: true, force: true }); mkdirSync(DL, { recursive: true });
  const cdp = await page.createCDPSession();
  await cdp.send("Browser.setDownloadBehavior", { behavior: "allow", downloadPath: DL });
  const waitDl = async (name, what) => {
    const path = `${DL}/${name}`;
    for (let i = 0; i < 100; i++) {
      if (existsSync(path) && !readdirSync(DL).some((f) => f.endsWith(".crdownload"))) { ok(what); return readFileSync(path); }
      await new Promise((r) => setTimeout(r, 300));
    }
    fail(`download ${name} never arrived (have: ${readdirSync(DL).join(",") || "none"})`); return null;
  };

  const outSum = () => page.$eval(".wb-outsum", (n) => n.textContent);
  if (/1 of 1 file · 2 edits/.test(await outSum())) ok(`output summary correct (${await outSum()})`);
  else fail("output summary: " + await outSum());
  const wordPill = await page.$eval('.wb-fmt[data-f="word"]', (b) => ({ disabled: b.disabled, title: b.title }));
  if (wordPill.disabled && /pdfblah\[convert\]/.test(wordPill.title)) ok("Word pill gated on missing local converter");
  else fail("word pill state: " + JSON.stringify(wordPill));

  // download as PDF (stack applied)
  await page.waitForFunction(() => !document.querySelector(".wb-download").disabled, { timeout: 30000 });
  await page.click(".wb-download");
  const pdfBytes = await waitDl("report-edited.pdf", "Download → report-edited.pdf arrives");
  if (pdfBytes && pdfBytes.subarray(0, 4).toString() === "%PDF") ok("downloaded file is a PDF"); else fail("not a PDF");

  // download as Text: replacement applied
  await page.click('.wb-fmt[data-f="text"]');
  const optsHidden = await page.$eval(".wb-opw", (n) => n.hidden);
  if (optsHidden) ok("pdf-only options hide for Text"); else fail("options visible for Text");
  await page.waitForFunction(() => !document.querySelector(".wb-download").disabled, { timeout: 30000 });
  await page.click(".wb-download");
  const txt = await waitDl("report.txt", "Download → report.txt arrives");
  if (txt && /Blahco Ltd/.test(String(txt)) && !/Acme Corp/.test(String(txt))) ok("text output has the stack applied");
  else fail("text content wrong");

  // include-toggle drives the button
  await page.evaluate(() => document.querySelector(".wb-file .wb-inc input").click());
  await page.waitForFunction(() => document.querySelector(".wb-download").disabled, { timeout: 5000 });
  if (/0 of 1/.test(await outSum())) ok("unchecking include → 0 of 1, Download disabled"); else fail("include toggle sum: " + await outSum());
  await page.evaluate(() => document.querySelector(".wb-file .wb-inc input").click());
  await page.waitForFunction(() => !document.querySelector(".wb-download").disabled, { timeout: 5000 });
  ok("re-including re-enables Download");
  await page.click('.wb-fmt[data-f="pdf"]');

  // --- page nav in the After view ---
  const srcP1 = await stageSrc();
  await page.click('.wb-navb[data-d="1"]');
  await page.waitForFunction(() => /2 \/ 3/.test(document.querySelector(".wb-nav span")?.textContent || ""), { timeout: 25000 });
  await waitNewStage(srcP1, "page nav in After view renders page 2 from the cached apply");
  await page.screenshot({ path: `${SCRATCH}/shot-6-stack-final.png` });

  // ================= P4: Inspect =================
  await (await page.$(".wb-fileinput")).uploadFile(`${SCRATCH}/inspect.pdf`);
  await page.waitForFunction(() => {
    const rows = [...document.querySelectorAll(".wb-file")];
    return rows.length === 2 && rows.every((r) => !/reading…/.test(r.textContent));
  }, { timeout: 30000 });
  await page.evaluate(() => {
    [...document.querySelectorAll(".wb-file")].find((r) => r.querySelector(".wb-fn")?.textContent === "inspect.pdf")
      .querySelector(".wb-fmeta").click();
  });
  await page.waitForFunction(() => /inspect\.pdf/.test(document.querySelector(".wb-vname")?.textContent || ""), { timeout: 25000 });
  await page.click('.wb-tab[data-t="inspect"]');
  await page.waitForFunction(() => // lazy sections resolved: no "loading…" notes left
    document.querySelectorAll(".wb-isect").length === 6 &&
    ![...document.querySelectorAll(".wb-inote")].some((n) => /loading/.test(n.textContent)), { timeout: 25000 });
  ok("Inspect tab renders all six sections");
  const irows = await page.$$eval(".wb-irow", (ns) => ns.map((n) => n.textContent));
  const hasMeta = irows.some((t) => /Title.*Master Services Agreement/.test(t)) && irows.some((t) => /Author.*K\. Osei/.test(t));
  if (hasMeta) ok("Document metadata rows (Title / Author) shown"); else fail("metadata rows: " + irows.slice(0, 5).join(" | "));
  const notes = await page.$$eval(".wb-inote", (ns) => ns.map((n) => n.textContent).join(" ~ "));
  if (/all reproducible/.test(notes)) ok("Fonts: all reproducible"); else fail("fonts note missing: " + notes);
  if (/Not encrypted\./.test(notes)) ok("Security: not encrypted"); else fail("security note missing");
  if (/No digital signatures found\./.test(notes)) ok("Signatures empty state"); else fail("signatures note missing");
  if (/0 interactive fields\./.test(notes)) ok("Form fields empty state"); else fail("forms note missing");
  const attRow = irows.find((t) => /terms-schedule\.csv/.test(t));
  if (attRow && /1 KB/.test(attRow)) ok(`attachment listed (${attRow.trim()})`); else fail("attachment row: " + attRow);

  // Extract downloads the embedded file byte-for-byte
  await page.evaluate(() => {
    [...document.querySelectorAll(".wb-iatt .wb-ilink")].find((b) => b.textContent === "Extract").click();
  });
  const csv = await waitDl("terms-schedule.csv", "Extract → terms-schedule.csv downloads");
  if (csv && String(csv) === readFileSync(`${SCRATCH}/terms-schedule.csv`, "utf8")) ok("extracted bytes match the embedded file");
  else fail("extracted content mismatch");
  await page.screenshot({ path: `${SCRATCH}/shot-7-inspect.png` });

  // Strip all -> adds a stripmeta step and jumps to the Stack
  const stepsBefore = await page.$$eval(".wb-step", (n) => n.length);
  await page.evaluate(() => {
    [...document.querySelectorAll(".wb-ilink")].find((b) => /Strip all/.test(b.textContent)).click();
  });
  await page.waitForFunction((n) => document.querySelectorAll(".wb-step").length === n + 1, { timeout: 25000 }, stepsBefore);
  const stackOn = await page.$eval('.wb-tab[data-t="stack"]', (b) => b.classList.contains("on"));
  const lastTitle = await page.$eval(".wb-step:last-child .wb-stitle", (n) => n.textContent);
  if (stackOn && lastTitle === "Strip metadata") ok("Strip all → stripmeta step added, Stack tab active");
  else fail(`strip-all: stackOn=${stackOn} last=${lastTitle}`);

  // ================= P5: Recipes =================
  const stackTitles = () => page.$$eval(".wb-stitle", (ns) => ns.map((n) => n.textContent));
  const savedTitles = await stackTitles(); // [Page numbers, Replace, Strip metadata]

  // save via the Output footer flow
  await page.click(".wb-reciperow");
  await page.waitForSelector(".wb-recmenu:not([hidden])", { timeout: 5000 });
  const focused = await page.evaluate(() => document.activeElement?.className.includes("wb-recname"));
  if (focused) ok("Save stack as recipe opens Recipes with the name focused"); else fail("name input not focused");
  await page.type(".wb-recname", "client handoff");
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => [...document.querySelectorAll(".wb-recn")].some((n) => n.textContent === "client handoff"), { timeout: 25000 });
  const recSub = await page.$eval(".wb-recrow .wb-recsub", (n) => n.textContent);
  if (/3 edits/.test(recSub)) ok(`recipe saved (${recSub})`); else fail("recipe sub: " + recSub);
  await page.keyboard.press("Escape");
  await page.waitForFunction(() => document.querySelector(".wb-recmenu").hidden, { timeout: 5000 });
  ok("Esc closes the Recipes menu");

  // clear the stack, then load the recipe back
  while (await page.$(".wb-step")) {
    await page.evaluate(() => document.querySelector(".wb-step .wb-srm").click());
    await new Promise((r) => setTimeout(r, 120));
  }
  await page.waitForSelector(".wb-stackhint", { timeout: 25000 });
  ok("stack cleared (hint card back)");
  const srcEmpty = await stageSrc();
  await page.click(".wb-recbtn");
  await page.waitForFunction(() => [...document.querySelectorAll(".wb-recn")].some((n) => n.textContent === "client handoff"), { timeout: 25000 });
  await page.evaluate(() => [...document.querySelectorAll(".wb-recn")].find((n) => n.textContent === "client handoff").click());
  await page.waitForFunction(() => document.querySelectorAll(".wb-step").length === 3, { timeout: 25000 });
  const loaded = await stackTitles();
  if (loaded.join(",") === savedTitles.join(",")) ok(`recipe load restores the stack (${loaded.join(" → ")})`);
  else fail(`loaded ${loaded} != saved ${savedTitles}`);
  const badge3 = await page.$eval(".wb-count", (n) => n.textContent);
  if (badge3 === "3") ok("count badge = 3 after load"); else fail("badge: " + badge3);
  await waitNewStage(srcEmpty, "After preview re-renders with the loaded stack");
  await page.screenshot({ path: `${SCRATCH}/shot-8-recipes.png` });

  // delete -> empty state
  await page.click(".wb-recbtn");
  await page.waitForSelector(".wb-recrow", { timeout: 25000 });
  await page.evaluate(() => document.querySelector(".wb-recrow .wb-frm").click());
  await page.waitForSelector(".wb-recempty", { timeout: 25000 });
  ok("deleting the recipe shows the empty state");
  await page.keyboard.press("Escape");

  // ================= font detect-and-refuse UX =================
  // fontrefuse.pdf uses an embedded SUBSET font; replacing with out-of-subset glyphs
  // ("Zebra Corp": Z, b, a unseen) must refuse — and the UI must make that unmissable.
  while (await page.$(".wb-step")) {
    await page.evaluate(() => document.querySelector(".wb-step .wb-srm").click());
    await new Promise((r) => setTimeout(r, 120));
  }
  await (await page.$(".wb-fileinput")).uploadFile(`${SCRATCH}/fontrefuse.pdf`);
  await page.waitForFunction(() => [...document.querySelectorAll(".wb-fn")].some((n) => n.textContent === "fontrefuse.pdf")
    && ![...document.querySelectorAll(".wb-file")].some((r) => /reading…/.test(r.textContent)), { timeout: 30000 });
  await page.evaluate(() => {
    [...document.querySelectorAll(".wb-file")].find((r) => r.querySelector(".wb-fn")?.textContent === "fontrefuse.pdf")
      .querySelector(".wb-fmeta").click();
  });
  await page.waitForFunction(() => /fontrefuse\.pdf/.test(document.querySelector(".wb-vname")?.textContent || ""), { timeout: 25000 });
  // only fontrefuse.pdf included -> the single-file no-op guard is exercisable
  await page.evaluate(() => {
    [...document.querySelectorAll(".wb-file")].forEach((r) => {
      const name = r.querySelector(".wb-fn")?.textContent;
      const cb = r.querySelector(".wb-inc input");
      if (name !== "fontrefuse.pdf" && cb.checked) cb.click();
    });
  });
  await addStepViaMenu("Replace");
  await page.type(".wb-step.open .wb-frow:nth-child(1) input", "Acme Corp");
  await page.type(".wb-step.open .wb-frow:nth-child(2) input", "Zebra Corp");
  // the veil must show while the server applies + checks fonts (MutationObserver catches it)
  await page.waitForSelector(".wb-veil:not([hidden])", { timeout: 8000 });
  ok("checking veil appears while fonts are verified");
  await page.waitForSelector(".wb-veil[hidden]", { timeout: 30000 });
  await page.waitForSelector(".wb-step.brk", { timeout: 30000 });
  const brkSum = await page.$eval(".wb-step.brk .wb-ssum", (n) => n.textContent);
  if (/BitstreamVeraSans/.test(brkSum) && /refused/.test(brkSum) && /try a replacement/.test(brkSum))
    ok(`refused step looks broken and names the font (${brkSum.slice(0, 60)}…)`);
  else fail("broken summary: " + brkSum);
  await page.waitForFunction(() => /no edit applies to fontrefuse\.pdf/.test(document.querySelector(".wb-outsum")?.textContent || ""), { timeout: 30000 });
  const dlState = await page.$eval(".wb-download", (b) => ({ disabled: b.disabled, label: b.textContent }));
  if (dlState.disabled && dlState.label === "Nothing to download") ok("no-op guard: Download locked with an honest label");
  else fail("download state: " + JSON.stringify(dlState));
  await page.waitForFunction(() => {
    const e = document.querySelector(".wb-errbar");
    return e && !e.hidden && e.classList.contains("info") && /After matches Before/.test(e.textContent);
  }, { timeout: 30000 });
  ok("center explains the dead After view (info banner)");
  await page.screenshot({ path: `${SCRATCH}/shot-9-font-refused.png` });

  // --- the substitution prompt: one explicit click, honest report ---
  await page.waitForSelector(".wb-step.brk .wb-fixbtn", { timeout: 10000 });
  ok("refused card offers the substitution prompt");
  await page.click(".wb-step.brk .wb-fixbtn");
  await page.waitForFunction(() => !document.querySelector(".wb-step.brk"), { timeout: 30000 });
  await page.waitForFunction(() => /Helvetica substituted/.test(
    [...document.querySelectorAll(".wb-ssum")].map((n) => n.textContent).join(" ")), { timeout: 30000 });
  ok("substitution applies and the summary names the font");
  await page.waitForFunction(() => !document.querySelector(".wb-download").disabled, { timeout: 30000 });
  await page.click('.wb-fmt[data-f="text"]');
  await page.waitForFunction(() => !document.querySelector(".wb-download").disabled, { timeout: 30000 });
  await page.click(".wb-download");
  const subTxt = await waitDl("fontrefuse.txt", "substituted edit downloads");
  if (subTxt && /Zebra Corp/.test(String(subTxt))) ok("downloaded text contains the substituted replacement");
  else fail("substituted text wrong: " + String(subTxt).slice(0, 60));
  await page.click('.wb-fmt[data-f="pdf"]');
  await page.screenshot({ path: `${SCRATCH}/shot-10-substituted.png` });
  // undo via the chip -> refused again
  await page.evaluate(() => {
    [...document.querySelectorAll(".wb-step.open .wb-chip")].find((c) => /Substitute a similar font/.test(c.textContent)).click();
  });
  await page.waitForSelector(".wb-step.brk", { timeout: 30000 });
  ok("turning the substitution chip off restores the honest refusal");

  // fixing the replacement revives everything (glyphs from the original subset)
  await page.evaluate(() => { const i = document.querySelector(".wb-step.open .wb-frow:nth-child(2) input"); i.value = ""; });
  await page.type(".wb-step.open .wb-frow:nth-child(2) input", "Acme Corn");
  await page.waitForFunction(() => {
    const b = document.querySelector(".wb-download");
    return !b.disabled && /Download 1 file/.test(b.textContent);
  }, { timeout: 30000 });
  await page.waitForFunction(() => !document.querySelector(".wb-step.brk"), { timeout: 30000 });
  ok("in-subset replacement clears the broken state and unlocks Download");
  await page.waitForFunction(() => document.querySelector(".wb-errbar").hidden, { timeout: 30000 });
  ok("info banner clears once a step applies");

  // ================= CID fonts: honest refusal, no dead-end offers =================
  // cidfont.pdf is Identity-H (glyph IDs). Rewriting AND substitution are impossible,
  // so the card must say why and must NOT show the substitute button or chip.
  while (await page.$(".wb-step")) {
    await page.evaluate(() => document.querySelector(".wb-step .wb-srm").click());
    await new Promise((r) => setTimeout(r, 120));
  }
  await (await page.$(".wb-fileinput")).uploadFile(`${SCRATCH}/cidfont.pdf`);
  await page.waitForFunction(() => [...document.querySelectorAll(".wb-fn")].some((n) => n.textContent === "cidfont.pdf")
    && ![...document.querySelectorAll(".wb-file")].some((r) => /reading…/.test(r.textContent)), { timeout: 30000 });
  await page.evaluate(() => {
    [...document.querySelectorAll(".wb-file")].find((r) => r.querySelector(".wb-fn")?.textContent === "cidfont.pdf")
      .querySelector(".wb-fmeta").click();
  });
  await page.waitForFunction(() => /cidfont\.pdf/.test(document.querySelector(".wb-vname")?.textContent || ""), { timeout: 25000 });
  await addStepViaMenu("Replace");
  await page.type(".wb-step.open .wb-frow:nth-child(1) input", "Acme Corp");
  await page.type(".wb-step.open .wb-frow:nth-child(2) input", "Foo");
  await page.waitForSelector(".wb-step.brk", { timeout: 30000 });
  const cidSum = await page.$eval(".wb-step.brk .wb-ssum", (n) => n.textContent);
  if (/glyph IDs/.test(cidSum)) ok(`CID refusal says why (${cidSum.slice(0, 55)}…)`); else fail("cid summary: " + cidSum);
  if (!(await page.$(".wb-fixbtn"))) ok("no substitute button on a CID refusal"); else fail("dead-end substitute button offered");
  const cidChip = await page.evaluate(() =>
    [...document.querySelectorAll(".wb-step.open .wb-chip")].some((c) => /Substitute/.test(c.textContent)));
  if (!cidChip) ok("no substitute chip on a CID refusal"); else fail("substitute chip offered");
  await page.evaluate(() => document.querySelector(".wb-step .wb-srm").click());
  await page.waitForSelector(".wb-stackhint", { timeout: 25000 });

  // the local app must never show a price line (pricing is hosted-only)
  const quoteShown = await page.$eval(".wb-quote", (n) => !n.hidden).catch(() => false);
  if (!quoteShown) ok("open-core boundary: no price line in the local app"); else fail("price line visible locally!");

  if (pageErrors.length) fail("page/console errors:\n   " + pageErrors.join("\n   "));
  else ok("no page or console errors");

  console.log(process.exitCode ? "\n=== VISUAL VERIFY: FAILURES ABOVE ===" : "\n=== VISUAL VERIFY: ALL CHECKS PASSED ===");
} catch (e) {
  fail("driver threw: " + (e.stack || e.message));
} finally {
  if (serverExited) fail(`gui server DIED mid-run: exit=${serverExited.c} sig=${serverExited.sig}`);
  if (serverErr.trim()) console.error("--- server stderr ---\n" + serverErr.slice(-4000));
  if (browser) await browser.close().catch(() => {});
  server.kill("SIGKILL");
  await once(server, "exit").catch(() => {});
}
