"use strict";
// Passport Studio. Shares helpers ($, el, icon, api, toast, saveBlob,
// segmented, clamp, stemOf, withBusy) with app.js, which loads first.

const PP_DPI = 300;
const mmPx = (mm, dpi = PP_DPI) => Math.round((mm / 25.4) * dpi);

// Paper formats for the print sheet. Photos are always printed at their exact
// size; these only change paper, spacing and how many go in a row. Each
// format uses whichever orientation fits more photos.
const FORMATS = [
  { id: "a4", name: "A4", paper: [210, 297], margin: 5, gap: 2, maxCols: 99 },
  { id: "a4-4", name: "A4", paper: [210, 297], margin: 8, gap: 6, maxCols: 4 },
  { id: "4x6", name: "4×6\"", paper: [101.6, 152.4], margin: 3, gap: 2, maxCols: 99 },
];
const QUICK_COPIES = [6, 12, 18, 24, 30, 36, 42];

// Colour looks: RGB offset, plus extra contrast/saturation (percent).
const LOOKS = {
  natural: { tint: [0, 0, 0], contrast: 0, sat: 0 },
  cool: { tint: [-6, 2, 12], contrast: 0, sat: 0 },
  rosy: { tint: [12, -3, 6], contrast: 0, sat: 4 },
  warm: { tint: [12, 4, -10], contrast: 0, sat: 2 },
  standard: { tint: [2, 2, 2], contrast: 8, sat: 8 },
};
const STRENGTH = { low: 0.5, med: 1, high: 1.6 };

const pp = {
  items: [],
  active: null,
  sizes: [],
  sizeId: "passport",
  custom: { w: 35, h: 45, head: 0.62, top: 0.1 },
  bgOn: true, bg: "#ffffff",
  enhanceOn: true, strength: "low", look: "natural",
  borderOn: true, borderMm: 0.4, borderColor: "#3c4655",
  comparing: false,
  quality: "fast",     // background-removal model: fast | best
  format: "a4",
};
let ppNextId = 1;
let ppChain = Promise.resolve();

function sizeObj() {
  if (pp.sizeId === "custom") {
    const { w, h, head, top } = pp.custom;
    return { id: "custom", label: `Custom Size (${w / 10}×${h / 10} cm)`, w_mm: w, h_mm: h, head, top,
             description: "Your own width and height in millimetres" };
  }
  return pp.sizes.find((s) => s.id === pp.sizeId) || pp.sizes[0];
}

// ---------- tool switch ----------

function showTool(tool) {
  document.body.dataset.tool = tool;
  for (const b of $("tool-switch").children) b.classList.toggle("on", b.dataset.tool === tool);
  $("card-view").hidden = tool !== "card";
  $("card-bar").hidden = tool !== "card";
  $("passport-view").hidden = tool !== "passport";
  $("passport-bar").hidden = tool !== "passport";
  if (tool === "passport") renderAllPP();
}
for (const b of $("tool-switch").children) b.addEventListener("click", () => showTool(b.dataset.tool));
// app.js routes window-level file drops here while this tool is open.
window.onToolDrop = (files) => {
  if (document.body.dataset.tool !== "passport") return false;
  ppAddFiles(files);
  return true;
};

// ---------- size presets ----------

fetch("/api/passport/sizes").then((r) => r.json()).then(({ sizes, custom }) => {
  pp.sizes = sizes;
  Object.assign(pp.custom, custom);
  const list = $("pp-preset-list");
  const opts = [...sizes, { id: "custom", label: "Custom Size", description: "Enter your own width and height in mm" }];
  for (const s of opts) {
    list.append(el("li", { role: "option", "data-id": s.id, onclick: () => chooseSize(s.id) },
      el("b", {}, s.label), el("small", {}, s.description)));
  }
  renderAllPP();
});

$("pp-preset-btn").addEventListener("click", (e) => {
  e.stopPropagation();
  $("pp-preset-list").hidden = !$("pp-preset-list").hidden;
});
document.addEventListener("click", () => ($("pp-preset-list").hidden = true));

function chooseSize(id) {
  $("pp-preset-list").hidden = true;
  pp.sizeId = id;
  $("pp-custom").hidden = id !== "custom";
  // A new shape needs new frames.
  for (const item of pp.items) if (item.cut) item.box = autoBox(item);
  renderAllPP();
}

for (const id of ["pp-custom-w", "pp-custom-h"]) {
  $(id).addEventListener("change", () => {
    pp.custom.w = clamp(Math.round(Number($("pp-custom-w").value) || 35), 15, 150);
    pp.custom.h = clamp(Math.round(Number($("pp-custom-h").value) || 45), 15, 150);
    $("pp-custom-w").value = pp.custom.w;
    $("pp-custom-h").value = pp.custom.h;
    chooseSize("custom");
  });
}

function renderPresetPP() {
  const s = sizeObj();
  if (!s) return;
  $("pp-preset-label").textContent = s.label;
  $("pp-preset-desc").textContent = s.description;
  $("pp-size-text").textContent = `${s.w_mm / 10} × ${s.h_mm / 10} cm`;
  for (const li of $("pp-preset-list").children) li.setAttribute("aria-selected", li.dataset.id === pp.sizeId);
}

// ---------- photo queue ----------

const ACCEPT_PHOTO = /\.(jpe?g|png|webp)$/i;

function ppAddFiles(fileList) {
  const files = [...fileList].filter((f) => ACCEPT_PHOTO.test(f.name));
  if (!files.length) return toast("Please choose JPG, PNG or WEBP photos", "err");
  for (const file of files) {
    if (file.size > 15 * 1024 * 1024) { toast(`${file.name} is larger than 15 MB`, "err"); continue; }
    const item = {
      id: ppNextId++, file, name: file.name, stem: stemOf(file.name), status: "busy", checked: true,
      thumb: URL.createObjectURL(file), copies: 6, cut: null, box: null, seq: 0,
      adj: { brightness: 0, contrast: 0, soft: 40, despill: 60 }, person: null, personKey: "",
    };
    pp.items.push(item);
    ppChain = ppChain.then(() => cutOut(item));
    if (!pp.active) pp.active = item;
  }
  renderAllPP();
}

const loadImg = (src) => new Promise((resolve, reject) => {
  const img = new Image();
  img.onload = () => resolve(img);
  img.onerror = reject;
  img.src = src;
});

// POST with upload progress (fetch cannot report it). Resolves to parsed JSON;
// rejects with the server's error message like api() does.
function postWithProgress(url, body, onUpload) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.responseType = "json";
    xhr.upload.onprogress = (e) => e.lengthComputable && onUpload(e.loaded / e.total);
    xhr.upload.onload = () => onUpload(1);
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) return resolve(xhr.response);
      const err = xhr.response || {};
      reject(Object.assign(new Error(err.message || `Server error (${xhr.status})`), { code: err.error }));
    };
    xhr.onerror = () => reject(new Error("Network error, please try again"));
    xhr.send(body);
  });
}

// Progress: upload 0–15 %, then the server's model state. A first-run model
// download reports real bytes; the cut-out itself is estimated from how long
// recent photos took on this server.
function setProgress(item, p, stage) {
  item.progress = clamp(p, 0, 1);
  item.stage = stage;
  renderProgress();
}

async function cutOut(item) {
  if (!pp.items.includes(item)) return;
  const seq = ++item.seq;
  item.status = "busy";
  setProgress(item, 0, "Uploading photo…");
  renderAllPP();
  const body = new FormData();
  body.append("file", item.file);
  body.append("quality", pp.quality);
  let uploaded = false, workStart = 0, timer = 0;
  const poll = async () => {
    if (!uploaded || seq !== item.seq || item.status !== "busy") return;
    try {
      const st = await (await fetch(`/api/passport/status?quality=${pp.quality}`)).json();
      if (seq !== item.seq || item.status !== "busy") return;
      if (st.state === "downloading") {
        setProgress(item, st.progress, "Downloading AI model (first time only)…");
        workStart = 0;
      } else if (st.state === "loading" || st.state === "idle") {
        setProgress(item, 0.15, "Loading AI model…");
        workStart = 0;
      } else {
        workStart ||= performance.now();
        const t = (performance.now() - workStart) / 1000;
        const p = 0.15 + 0.8 * (1 - Math.exp((-1.6 * t) / Math.max(0.5, st.seconds)));
        setProgress(item, p, t < st.seconds * 0.85 ? "Removing background…" : "Finding face & refining edges…");
      }
    } catch { /* keep the last progress */ }
    timer = setTimeout(poll, 400);
  };
  try {
    const data = await postWithProgress("/api/passport/cutout", body, (f) => {
      if (seq !== item.seq) return;
      if (f >= 1 && !uploaded) { uploaded = true; poll(); }
      else if (!uploaded) setProgress(item, 0.15 * f, `Uploading photo… ${Math.round(f * 100)}%`);
    });
    clearTimeout(timer);
    if (seq !== item.seq) return;
    setProgress(item, 0.97, "Preparing preview…");
    const [img, orig] = await Promise.all([loadImg(data.image), loadImg(data.original)]);
    if (seq !== item.seq) return;
    setProgress(item, 1, "Done");
    item.cut = { img, orig, width: data.width, height: data.height, heads: data.heads };
    item.person = null;
    item.box = autoBox(item);
    item.status = "ready";
  } catch (e) {
    clearTimeout(timer);
    if (seq !== item.seq) return;
    item.status = "error";
    item.error = e.message;
    toast(`${item.name}: ${e.message}`, "err");
  }
  renderAllPP();
}

// Update only the progress bars and labels (cheap; called every poll).
function renderProgress() {
  for (const item of pp.items) {
    const row = $("pp-list").querySelector(`[data-id="${item.id}"]`);
    if (!row) continue;
    const bar = row.querySelector(".mini-bar span");
    if (bar) bar.style.width = `${Math.round((item.progress || 0) * 100)}%`;
    const label = row.querySelector(".q-status.busy .label");
    if (label) label.textContent = item.stage || "Waiting in queue…";
  }
  const item = pp.active;
  const busy = item?.status === "busy";
  for (const id of ["pp-work-progress", "pp-preview-progress"]) {
    const box = $(id);
    box.hidden = !busy;
    if (!busy) continue;
    const pct = Math.round((item.progress || 0) * 100);
    box.querySelector(".bar span").style.width = `${pct}%`;
    box.querySelector(".pct").textContent = `${pct}%`;
    box.querySelector(".pg-stage").textContent = item.stage || "Waiting in queue…";
  }
}

function removeItem(item) {
  URL.revokeObjectURL(item.thumb);
  pp.items = pp.items.filter((i) => i !== item);
  if (pp.active === item) pp.active = pp.items[0] || null;
  renderAllPP();
}

// Best quality is the default when the server has a GPU.
fetch("/api/passport/info").then((r) => r.json()).then((info) => {
  pp.info = info;
  if (!info.available) {
    $("pp-drop").querySelector("p").textContent =
      "Background removal is not installed on the server: pip install -e .[photo]";
  }
  const best = $("pp-quality").querySelector("[data-quality=best]");
  best.textContent = info.gpu ? "Best edges · GPU" : "Best edges (slow)";
  best.title = info.gpu ? "BiRefNet on your GPU, about a second per photo"
    : "BiRefNet on the CPU: cleaner hair and edges, 10–30 s per photo";
  pp.quality = info.default_quality;
  for (const b of $("pp-quality").children) b.classList.toggle("on", b.dataset.quality === pp.quality);
});

// A new cutout quality re-runs the ticked photos (the open one if none ticked).
segmented($("pp-quality"), "quality", (v) => {
  pp.quality = v;
  if (v === "best" && !pp.info?.gpu) toast("Best edges on CPU: about 10–30 seconds per photo");
  const ticked = pp.items.filter((i) => i.checked && i.status !== "busy");
  for (const item of ticked.length ? ticked : [pp.active].filter(Boolean)) {
    Object.assign(item, { status: "busy", progress: 0, stage: "Waiting in queue…" });
    ppChain = ppChain.then(() => cutOut(item));
  }
  renderAllPP();
});

function renderList() {
  const list = $("pp-list");
  list.replaceChildren();
  for (const item of pp.items) {
    const [ic, text, cls] = item.status === "busy" ? ["clock", item.stage || "Waiting in queue…", "busy"]
      : item.status === "error" ? ["alert", "Error", "error"] : ["sparkles", "AI Cutout", "cropped"];
    list.append(el("div", {
      class: `q-item${item === pp.active ? " active" : ""}`, title: item.error || item.name, "data-id": item.id,
      onclick: () => { pp.active = item; renderAllPP(); },
    },
      el("label", { class: "checkbox", onclick: (e) => e.stopPropagation() },
        el("input", { type: "checkbox", ...(item.checked ? { checked: "" } : {}),
          onchange: (e) => { item.checked = e.target.checked; renderList(); renderBarPP(); } }),
        el("span", { class: "box" }, icon("check"))),
      el("img", { class: "q-thumb", src: item.thumb, alt: "" }),
      el("div", { class: "q-body" },
        el("div", { class: "q-name" }, item.name),
        el("div", { class: "q-meta" }, el("span", { class: "pill" }, fmtSize(item.file.size)), " · ",
          el("span", { class: `q-status ${cls}` }, icon(ic), el("span", { class: "label" }, text))),
        ...(item.status === "busy"
          ? [el("div", { class: "mini-bar" }, el("span", { style: `width:${Math.round((item.progress || 0) * 100)}%` }))]
          : [])),
      el("button", { class: "q-del", "aria-label": `Remove ${item.name}`,
        onclick: (e) => { e.stopPropagation(); removeItem(item); } }, icon("trash")),
    ));
  }
  const n = pp.items.length, checked = pp.items.filter((i) => i.checked).length;
  $("pp-count").textContent = `${n} photo${n === 1 ? "" : "s"} in queue`;
  $("pp-drop").hidden = n > 0;
  $("pp-add-more").hidden = n === 0;
  $("pp-tools").hidden = n === 0;
  $("pp-select-all").checked = n > 0 && checked === n;
  $("pp-select-label").textContent = `Select All (${checked}/${n})`;
}

const ppInput = $("pp-input");
ppInput.addEventListener("change", () => { ppAddFiles(ppInput.files); ppInput.value = ""; });
$("pp-add").addEventListener("click", () => ppInput.click());
$("pp-add-more").addEventListener("click", () => ppInput.click());
$("pp-select-all").addEventListener("change", (e) => {
  for (const i of pp.items) i.checked = e.target.checked;
  renderAllPP();
});
$("pp-clear").addEventListener("click", () => {
  const any = pp.items.some((i) => i.checked);
  for (const item of [...pp.items]) if (!any || item.checked) removeItem(item);
});

// ---------- framing ----------

// Frame (page fractions x0, y0, x1, y1) so all heads, crown to chin, fill
// `head` of the photo height with `top` space above, centred between them.
function autoBox(item) {
  const s = sizeObj(), { width: W, height: H, heads } = item.cut;
  const A = s.w_mm / s.h_mm;
  const top = Math.min(...heads.map((h) => h.crown));
  const bottom = Math.max(...heads.map((h) => h.chin));
  const left = Math.min(...heads.map((h) => h.center - 0.75 * h.width));
  const right = Math.max(...heads.map((h) => h.center + 0.75 * h.width));
  let bh = (bottom - top) / s.head, bw = bh * A;
  if (right - left > 0.9 * bw) { bw = (right - left) / 0.9; bh = bw / A; }
  const x0 = (left + right) / 2 - bw / 2, y0 = top - s.top * bh;
  return [x0 / W, y0 / H, (x0 + bw) / W, (y0 + bh) / H];
}

// Largest frame of the right shape that fits inside the photo.
function fitBox(item) {
  const s = sizeObj(), { width: W, height: H } = item.cut;
  const A = s.w_mm / s.h_mm;
  let bw = W, bh = W / A;
  if (bh > H) { bh = H; bw = H * A; }
  const x0 = (W - bw) / 2, y0 = (H - bh) / 2;
  return [x0 / W, y0 / H, (x0 + bw) / W, (y0 + bh) / H];
}

$("pp-autocenter").addEventListener("click", () => {
  const item = pp.active;
  if (item?.status !== "ready") return;
  item.box = autoBox(item);
  renderAllPP();
});
$("pp-fit").addEventListener("click", () => {
  const item = pp.active;
  if (item?.status !== "ready") return;
  item.box = fitBox(item);
  renderAllPP();
});
$("pp-rotate").addEventListener("click", async () => {
  const item = pp.active;
  if (item?.status !== "ready") return;
  const o = item.cut.orig;
  const c = el("canvas", { width: o.height, height: o.width });
  const x = c.getContext("2d");
  x.translate(c.width, 0);
  x.rotate(Math.PI / 2);
  x.drawImage(o, 0, 0);
  const blob = await new Promise((r) => c.toBlob(r, "image/jpeg", 0.95));
  item.file = new File([blob], item.name, { type: "image/jpeg" });
  URL.revokeObjectURL(item.thumb);
  item.thumb = URL.createObjectURL(blob);
  cutOut(item);
});

// ---------- workspace: drag and resize the frame ----------

const ppStage = $("pp-stage");
const ppBox = $("pp-box");
ppBox.append(el("div", { class: "grid" }));
for (const d of ["nw", "n", "ne", "e", "se", "s", "sw", "w"]) ppBox.append(el("span", { class: `handle ${d}`, "data-dir": d }));

function renderWorkspacePP() {
  const item = pp.active;
  const ready = item?.status === "ready";
  ppStage.hidden = !ready;
  $("pp-work-tools").hidden = !ready && !(item?.status === "busy");
  $("pp-work-empty").hidden = ready;
  const s = sizeObj();
  if (s) $("pp-chip-size").querySelector("span").textContent = s.label.replace(/ ·.*\(/, " (");
  const chip = $("pp-chip-status");
  chip.classList.toggle("teal", ready);
  chip.classList.toggle("busy", !ready);
  chip.querySelector("span").textContent = ready ? `${item.cut.heads.length > 1 ? `${item.cut.heads.length} People` : "Character"} Isolated` : "AI Cutout…";
  if (!ready) {
    const empty = $("pp-work-empty");
    empty.querySelector("h3").textContent = item?.status === "busy" ? "AI Cutout in progress…" : item?.status === "error" ? "Could not process" : "Passport Photo Workspace";
    empty.querySelector("p").innerHTML = item?.status === "busy" ? ""
      : item?.status === "error" ? item.error : "Upload a photo in <b>Column 1</b>. The person is cut out and framed automatically; drag the frame to adjust.";
    return;
  }
  const img = $("pp-original");
  if (img.dataset.key !== `${item.id}:${item.seq}`) {
    img.src = item.cut.orig.src;
    img.dataset.key = `${item.id}:${item.seq}`;
  }
  const wrap = $("pp-stage-wrap");
  const k = Math.min((wrap.clientWidth - 28) / item.cut.width, (wrap.clientHeight - 28) / item.cut.height);
  ppStage.style.width = `${item.cut.width * k}px`;
  ppStage.style.height = `${item.cut.height * k}px`;
  const [x0, y0, x1, y1] = item.box;
  Object.assign(ppBox.style, { left: `${x0 * 100}%`, top: `${y0 * 100}%`, width: `${(x1 - x0) * 100}%`, height: `${(y1 - y0) * 100}%` });
}
window.addEventListener("resize", () => document.body.dataset.tool === "passport" && renderWorkspacePP());

let ppDrag = null;
ppStage.addEventListener("pointerdown", (e) => {
  if (!e.target.closest("#pp-box")) return;
  const item = pp.active;
  ppDrag = { dir: e.target.dataset.dir || "move", sx: e.clientX, sy: e.clientY, start: [...item.box],
             rect: ppStage.getBoundingClientRect() };
  ppStage.setPointerCapture(e.pointerId);
  e.preventDefault();
});
ppStage.addEventListener("pointermove", (e) => {
  if (!ppDrag) return;
  const item = pp.active, s = sizeObj();
  const r = (s.w_mm / s.h_mm) * (item.cut.height / item.cut.width);   // box width / height, in page fractions
  const dx = (e.clientX - ppDrag.sx) / ppDrag.rect.width, dy = (e.clientY - ppDrag.sy) / ppDrag.rect.height;
  let [x0, y0, x1, y1] = ppDrag.start;
  const d = ppDrag.dir;
  if (d === "move") {
    const w = x1 - x0, h = y1 - y0;
    x0 = clamp(x0 + dx, -w / 2, 1 - w / 2);
    y0 = clamp(y0 + dy, -h / 2, 1 - h / 2);
    item.box = [x0, y0, x0 + w, y0 + h];
  } else {
    // Resize keeping the photo shape; the opposite side (or centre) stays put.
    let w = x1 - x0, h = y1 - y0;
    if (d.includes("e")) w += dx; else if (d.includes("w")) w -= dx;
    else if (d.includes("s")) w = (h + dy) * r; else if (d.includes("n")) w = (h - dy) * r;
    w = clamp(w, 0.05, 3);
    h = w / r;
    const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
    const nx0 = d.includes("w") ? x1 - w : d.includes("e") ? x0 : cx - w / 2;
    const ny0 = d.includes("n") ? y1 - h : d.includes("s") ? y0 : cy - h / 2;
    item.box = [nx0, ny0, nx0 + w, ny0 + h];
  }
  renderWorkspacePP();
  schedulePreview();
});
const endPPDrag = () => { ppDrag = null; };
ppStage.addEventListener("pointerup", endPPDrag);
ppStage.addEventListener("pointercancel", endPPDrag);

// ---------- rendering the photo ----------

// The cut-out person with edge, despill and enhancement applied (cached).
function personCanvas(item) {
  const { brightness, contrast, soft, despill } = item.adj;
  const key = [brightness, contrast, soft, despill, pp.enhanceOn, pp.strength, pp.look].join(":");
  if (item.person && item.personKey === key) return item.person;
  const { img } = item.cut;
  const c = el("canvas", { width: img.width, height: img.height });
  const x = c.getContext("2d", { willReadFrequently: true });
  x.drawImage(img, 0, 0);
  const px = x.getImageData(0, 0, c.width, c.height), d = px.data;

  const ew = 0.08 + (soft / 100) * 0.84, e0 = 0.5 - ew / 2;      // edge ramp
  const ds = despill / 100;
  const look = LOOKS[pp.look], k = STRENGTH[pp.strength];
  const on = pp.enhanceOn;
  const off = on ? brightness * 2.55 : 0;
  const cf = on ? 1 + (contrast + look.contrast * k) / 100 : 1;
  const sf = on ? 1 + (look.sat * k) / 100 : 1;
  const [tr, tg, tb] = on ? look.tint.map((v) => v * k) : [0, 0, 0];
  for (let i = 0; i < d.length; i += 4) {
    const a = d[i + 3] / 255;
    if (a === 0) continue;
    let r = d[i], g = d[i + 1], b = d[i + 2];
    // Despill: semi-transparent edge pixels carry the old backdrop colour; neutralise it.
    const kd = ds * Math.min(1, (1 - a) * 2.5);
    if (kd > 0) {
      const l = 0.299 * r + 0.587 * g + 0.114 * b;
      r += (l - r) * kd; g += (l - g) * kd; b += (l - b) * kd;
    }
    if (on) {
      r = (r + off + tr - 128) * cf + 128;
      g = (g + off + tg - 128) * cf + 128;
      b = (b + off + tb - 128) * cf + 128;
      if (sf !== 1) {
        const l = 0.299 * r + 0.587 * g + 0.114 * b;
        r = l + (r - l) * sf; g = l + (g - l) * sf; b = l + (b - l) * sf;
      }
    }
    d[i] = r; d[i + 1] = g; d[i + 2] = b;
    d[i + 3] = 255 * clamp((a - e0) / ew, 0, 1);
  }
  x.putImageData(px, 0, 0);
  item.person = c;
  item.personKey = key;
  return c;
}

// Final photo at `scale` × 300 DPI. compare=true shows the untouched crop.
function renderPhoto(item, scale = 1, compare = false) {
  const s = sizeObj();
  const W = Math.round(mmPx(s.w_mm) * scale), H = Math.round(mmPx(s.h_mm) * scale);
  const c = el("canvas", { width: W, height: H });
  const x = c.getContext("2d");
  x.imageSmoothingQuality = "high";
  const { width: iw, height: ih } = item.cut;
  const [x0, y0, x1, y1] = item.box;
  const crop = (src) => x.drawImage(src, x0 * iw, y0 * ih, (x1 - x0) * iw, (y1 - y0) * ih, 0, 0, W, H);
  x.fillStyle = compare || !pp.bgOn ? "#ffffff" : pp.bg;
  x.fillRect(0, 0, W, H);
  if (compare || !pp.bgOn) crop(item.cut.orig);
  if (!compare) crop(personCanvas(item));
  if (!compare && pp.borderOn) {
    const lw = Math.max(1, mmPx(pp.borderMm) * scale);
    x.strokeStyle = pp.borderColor;
    x.lineWidth = lw;
    x.strokeRect(lw / 2, lw / 2, W - lw, H - lw);
  }
  return c;
}

let previewQueued = false;
function schedulePreview() {
  if (previewQueued) return;
  previewQueued = true;
  requestAnimationFrame(() => { previewQueued = false; renderPreviewPP(); });
}

function renderPreviewPP() {
  const item = pp.active;
  const ready = item?.status === "ready" && sizeObj();
  $("pp-canvas").hidden = !ready;
  $("pp-preview-empty").hidden = !!ready;
  if (!ready) {
    $("pp-preview-empty").querySelector("h4").textContent =
      item?.status === "busy" ? "AI Cutout in progress…" : item?.status === "error" ? "Could not process" : "No Photo Selected";
    $("pp-preview-empty").querySelector("p").textContent =
      item?.status === "error" ? item.error : item?.status === "busy" ? "" : "Upload a photo in Column 1.";
    return;
  }
  const c = renderPhoto(item, 1, pp.comparing);
  const out = $("pp-canvas");
  if (out.width !== c.width || out.height !== c.height) Object.assign(out, { width: c.width, height: c.height });
  out.getContext("2d").drawImage(c, 0, 0);
}

// Hold to compare with the original.
for (const t of [$("pp-compare"), $("pp-canvas")]) {
  t.addEventListener("pointerdown", (e) => { e.preventDefault(); pp.comparing = true; renderPreviewPP(); });
}
for (const ev of ["pointerup", "pointercancel", "blur"]) {
  window.addEventListener(ev, () => { if (pp.comparing) { pp.comparing = false; renderPreviewPP(); } });
}

// ---------- controls ----------

const PP_SLIDERS = {
  "pp-brightness": ["brightness", (v) => `${v > 0 ? "+" : ""}${v}`],
  "pp-contrast": ["contrast", (v) => `${v > 0 ? "+" : ""}${v}`],
  "pp-soft": ["soft", (v) => `${v}%`],
  "pp-despill": ["despill", (v) => `${v}%`],
};
for (const [id, [field, fmt]] of Object.entries(PP_SLIDERS)) {
  $(id).addEventListener("input", (e) => {
    const item = pp.active;
    if (item?.status !== "ready") return;
    item.adj[field] = Number(e.target.value);
    $(`${id}-val`).textContent = fmt(item.adj[field]);
    schedulePreview();
  });
}

function renderControlsPP() {
  const adj = pp.active?.adj || { brightness: 0, contrast: 0, soft: 40, despill: 60 };
  for (const [id, [field, fmt]] of Object.entries(PP_SLIDERS)) {
    $(id).value = adj[field];
    $(`${id}-val`).textContent = fmt(adj[field]);
  }
}

function pickSwatch(container, attr, value, custom) {
  for (const b of container.querySelectorAll("button")) b.classList.toggle("on", b.dataset[attr] === value);
  container.querySelector(".swatch-custom").classList.toggle("on", !!custom);
}
for (const b of $("pp-bg").querySelectorAll("button")) {
  b.addEventListener("click", () => { pp.bg = b.dataset.bg; pickSwatch($("pp-bg"), "bg", pp.bg); schedulePreview(); });
}
$("pp-bg-custom").addEventListener("input", (e) => { pp.bg = e.target.value; pickSwatch($("pp-bg"), "bg", null, true); schedulePreview(); });
$("pp-bg-on").addEventListener("change", (e) => { pp.bgOn = e.target.checked; schedulePreview(); });
$("pp-enhance-on").addEventListener("change", (e) => { pp.enhanceOn = e.target.checked; schedulePreview(); });
segmented($("pp-strength"), "strength", (v) => { pp.strength = v; schedulePreview(); });
segmented($("pp-looks"), "look", (v) => { pp.look = v; schedulePreview(); });
$("pp-border-on").addEventListener("change", (e) => { pp.borderOn = e.target.checked; schedulePreview(); });
segmented($("pp-border-w"), "bw", (v) => { pp.borderMm = Number(v); schedulePreview(); });
for (const b of $("pp-border-c").querySelectorAll("button")) {
  b.addEventListener("click", () => { pp.borderColor = b.dataset.bc; pickSwatch($("pp-border-c"), "bc", pp.borderColor); schedulePreview(); });
}
$("pp-border-custom").addEventListener("input", (e) => { pp.borderColor = e.target.value; pickSwatch($("pp-border-c"), "bc", null, true); schedulePreview(); });

function renderBarPP() {
  const item = pp.active, s = sizeObj();
  $("pp-name").textContent = item ? item.name : "No photo loaded";
  $("pp-size-label").textContent = s ? `${s.w_mm / 10} × ${s.h_mm / 10} cm` : "";
  $("pp-status").textContent = !item ? "Upload in Column 1"
    : item.status === "busy" ? "AI cutout in progress…" : item.status === "error" ? "Could not process" : "Ready to print";
  $("pp-dl-photo").disabled = item?.status !== "ready";
  $("pp-print").disabled = !sheetItems().length;
}

function renderAllPP() {
  renderPresetPP();
  renderList();
  renderWorkspacePP();
  renderControlsPP();
  renderPreviewPP();
  renderBarPP();
  renderProgress();
}

// ---------- downloads ----------

const canvasBlob = (c, type = "image/jpeg", q = 0.95) => new Promise((r) => c.toBlob(r, type, q));

$("pp-dl-photo").addEventListener("click", async () => {
  const item = pp.active;
  if (item?.status !== "ready") return;
  saveBlob(await canvasBlob(renderPhoto(item, 1)), `${item.stem}_${sizeObj().id}.jpg`);
});

// ---------- print sheet ----------

const sheetItems = () => {
  const ticked = pp.items.filter((i) => i.checked && i.status === "ready");
  return ticked.length ? ticked : pp.items.filter((i) => i === pp.active && i.status === "ready");
};

function sheetLayout(fmt = FORMATS.find((f) => f.id === pp.format)) {
  const s = sizeObj();
  const pw = mmPx(s.w_mm), ph = mmPx(s.h_mm), gap = mmPx(fmt.gap), m = mmPx(fmt.margin);
  const fit = ([w, h]) => {
    const [PW, PH] = [mmPx(w), mmPx(h)];
    const cols = Math.max(0, Math.min(fmt.maxCols, Math.floor((PW - 2 * m + gap) / (pw + gap))));
    const rows = Math.max(0, Math.floor((PH - 2 * m + gap) / (ph + gap)));
    const ox = Math.floor((PW - (cols * pw + (cols - 1) * gap)) / 2);
    return { fmt, paper: [w, h], PW, PH, pw, ph, gap, cols, rows, ox, oy: m, perPage: cols * rows };
  };
  const portrait = fit(fmt.paper), landscape = fit([fmt.paper[1], fmt.paper[0]]);
  return landscape.perPage > portrait.perPage ? landscape : portrait;
}

function buildSheetPages(scale) {
  const L = sheetLayout();
  if (!L.perPage) return [];
  const pages = [];
  let slot = L.perPage;
  for (const item of sheetItems()) {
    if (!item.copies) continue;
    const photo = renderPhoto(item, 1);
    for (let n = 0; n < item.copies; n++) {
      if (slot === L.perPage) {
        const p = el("canvas", { width: Math.round(L.PW * scale), height: Math.round(L.PH * scale) });
        const x = p.getContext("2d");
        x.fillStyle = "#fff";
        x.fillRect(0, 0, p.width, p.height);
        pages.push(p);
        slot = 0;
      }
      const r = Math.floor(slot / L.cols), c = slot % L.cols;
      const x = pages[pages.length - 1].getContext("2d");
      x.imageSmoothingQuality = "high";
      x.drawImage(photo, (L.ox + c * (L.pw + L.gap)) * scale, (L.oy + r * (L.ph + L.gap)) * scale, L.pw * scale, L.ph * scale);
      slot++;
    }
  }
  return pages;
}

function openSheet() {
  if (!sheetItems().length) return;
  $("sheet-modal").hidden = false;
  renderSheet();
}
function closeSheet() { $("sheet-modal").hidden = true; }
$("pp-print").addEventListener("click", openSheet);
$("sheet-close").addEventListener("click", closeSheet);
$("sheet-cancel").addEventListener("click", closeSheet);
$("sheet-modal").addEventListener("click", (e) => { if (e.target === $("sheet-modal")) closeSheet(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("sheet-modal").hidden) closeSheet(); });

function renderSheet() {
  const formats = $("sheet-formats");
  formats.replaceChildren();
  for (const f of FORMATS) {
    const L = sheetLayout(f);
    formats.append(el("button", {
      class: f.id === pp.format ? "on" : "", ...(L.perPage ? {} : { disabled: "" }),
      onclick: () => { pp.format = f.id; renderSheet(); },
    }, `${f.name} (${L.perPage} Max)`, el("small", {}, `${L.cols} per row`)));
  }

  const list = $("sheet-copies");
  list.replaceChildren();
  for (const item of sheetItems()) {
    const thumb = renderPhoto(item, 0.25);
    const set = (n) => { item.copies = clamp(n, 0, 240); renderSheet(); };
    const quick = el("select", { "aria-label": "Quick copies", onchange: (e) => set(Number(e.target.value)) });
    quick.append(new Option("Qty", "", true, true));
    quick.options[0].disabled = true;
    for (const n of QUICK_COPIES) quick.append(new Option(`${n}`, n));
    list.append(el("div", { class: "copy-row" }, thumb,
      el("div", { class: "q-body" }, el("div", { class: "q-name" }, item.name), el("small", {}, `${item.copies} copies`)),
      el("div", { class: "stepper" },
        el("button", { "aria-label": "Fewer copies", onclick: () => set(item.copies - 6) }, icon("minus")),
        el("b", {}, String(item.copies)),
        el("button", { "aria-label": "More copies", onclick: () => set(item.copies + 6) }, icon("plus"))),
      quick));
  }

  const L = sheetLayout();
  const used = sheetItems().reduce((n, i) => n + i.copies, 0);
  const pages = L.perPage ? Math.max(1, Math.ceil(used / L.perPage)) : 0;
  const fmtLabel = `${L.fmt.name} ${L.cols}-Col photo sheet`;
  $("sheet-sub").textContent = pages > 1
    ? `${used} photos on ${pages} pages • ${fmtLabel}`
    : `${used} of ${L.perPage} slots used • ${fmtLabel}`;
  $("sheet-cap-text").textContent = `${used} / ${L.perPage * Math.max(1, pages)} photos`;
  $("sheet-cap-bar").style.width = `${L.perPage ? Math.min(100, (used / (L.perPage * Math.max(1, pages))) * 100) : 0}%`;
  $("sheet-print").querySelector("span").textContent = `Print ${used} Photos Sheet${pages > 1 ? "s" : ""}`;
  $("sheet-print").disabled = $("sheet-pdf").disabled = !used || !L.perPage;
  $("sheet-pages").replaceChildren(...buildSheetPages(0.22));
}

async function fullSheetBlobs() {
  const out = [];
  for (const p of buildSheetPages(1)) out.push(await canvasBlob(p));
  return out;
}

$("sheet-pdf").addEventListener("click", (e) => withBusy(e.currentTarget, async () => {
  const body = new FormData();
  for (const [i, b] of (await fullSheetBlobs()).entries()) body.append("pages", b, `page${i + 1}.jpg`);
  body.append("dpi", PP_DPI);
  body.append("name", `passport_photos_${sizeObj().id}`);
  const res = await api("/api/pdf", body);
  saveBlob(await res.blob(), `passport_photos_${sizeObj().id}.pdf`);
}));

$("sheet-print").addEventListener("click", async () => {
  const L = sheetLayout();
  const urls = (await fullSheetBlobs()).map((b) => URL.createObjectURL(b));
  const [w, h] = L.paper;
  const frame = $("print-frame");
  const doc = frame.contentDocument;
  doc.open();
  doc.write(`<!doctype html><html><head><title>Passport photos</title><style>
    @page { size: ${w}mm ${h}mm; margin: 0; }
    html, body { margin: 0; }
    img { display: block; width: ${w}mm; height: ${h}mm; break-after: page; }
  </style></head><body>${urls.map((u) => `<img src="${u}">`).join("")}</body></html>`);
  doc.close();
  await Promise.all([...doc.images].map((img) => img.decode().catch(() => {})));
  frame.contentWindow.focus();
  frame.contentWindow.print();
  setTimeout(() => urls.forEach(URL.revokeObjectURL), 60000);
});

showTool("card");
