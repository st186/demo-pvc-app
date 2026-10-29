"use strict";

const $ = (id) => document.getElementById(id);
const CARD_ASPECT = 85.6 / 53.98;
const ACCEPT = /\.(pdf|jpe?g|png)$/i;
const SIDES = ["front", "back"];
const LABEL = { front: "Front Side", back: "Back Side" };

const state = {
  items: [],
  active: null,
  editSide: "front",
  previewSide: "front",
  zoom: 1,
  preset: "auto",          // preset for new uploads
  color: "rgb",
  dpi: 600,                // HD by default; 300 = standard
  sharpen: true,
  colourAadhaar: true,     // soft colour background on Aadhaar cards
  types: [],
};
let nextId = 1;
let openChain = Promise.resolve();

// ---------- helpers ----------

function el(tag, attrs = {}, ...children) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v);
  }
  for (const c of children) n.append(c);
  return n;
}
const icon = (name) => {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
};
const fmtSize = (b) => (b > 1048576 ? `${(b / 1048576).toFixed(1)} MB` : `${(b / 1024).toFixed(1)} KB`);
const stemOf = (name) => name.replace(/\.[^.]+$/, "").replace(/[^\w-]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 60) || "card";
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const cloneSide = (s) => s && { page: s.page, box: [...s.box] };

let toastTimer;
function toast(msg, kind = "") {
  const t = $("toast");
  t.textContent = msg;
  t.className = `toast ${kind}`;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), 3500);
}

async function api(path, body) {
  const res = await fetch(path, { method: "POST", body });
  if (res.ok) return res;
  let err = { error: "server", message: `Server error (${res.status})` };
  try { err = await res.json(); } catch {}
  throw Object.assign(new Error(err.message), { code: err.error, status: res.status });
}

// ---------- presets ----------

fetch("/api/types").then((r) => r.json()).then((types) => {
  state.types = types;
  const list = $("preset-list");
  const opts = [{ id: "auto", label: "Auto-detect", description: "Recognise the document automatically" }, ...types];
  for (const t of opts) {
    list.append(el("li", { role: "option", "data-id": t.id, onclick: () => choosePreset(t.id) },
      el("b", {}, t.label), el("small", {}, t.description)));
  }
  renderPreset();
});

const typeById = (id) => state.types.find((t) => t.id === id);

function renderPreset() {
  const item = state.active;
  const chosen = item ? item.docType : state.preset;
  let label = "Auto-detect", desc = "Recognise the document automatically";
  if (chosen !== "auto") {
    const t = typeById(chosen);
    if (t) ({ label, description: desc } = t);
  } else if (item?.type) {
    const t = typeById(item.type);
    label = item.label;
    desc = t ? `Auto-detected · ${t.description}` : "Auto-detected · card shapes found on the page";
  }
  $("preset-label").textContent = label;
  $("preset-desc").textContent = desc;
  for (const li of $("preset-list").children) li.setAttribute("aria-selected", li.dataset.id === chosen);
}

$("preset-btn").addEventListener("click", (e) => {
  e.stopPropagation();
  $("preset-list").hidden = !$("preset-list").hidden;
});
document.addEventListener("click", () => ($("preset-list").hidden = true));

function choosePreset(id) {
  $("preset-list").hidden = true;
  const item = state.active;
  if (!item) {
    state.preset = id;
    renderPreset();
    return;
  }
  item.docType = id;
  renderPreset();
  if (item.status !== "locked") openItem(item, { keepAdj: true });
}

// ---------- queue ----------

function addFiles(fileList) {
  const files = [...fileList].filter((f) => ACCEPT.test(f.name));
  if (!files.length) return toast("Please choose PDF, JPG or PNG files", "err");
  for (const file of files) {
    if (file.size > 15 * 1024 * 1024) { toast(`${file.name} is larger than 15 MB`, "err"); continue; }
    if (!file.size) { toast(`${file.name} is empty`, "err"); continue; }
    // Unique names so cards don't overwrite each other in Download All.
    let stem = stemOf(file.name);
    for (let n = 2; state.items.some((i) => i.stem === stem); n++) stem = `${stemOf(file.name)}_${n}`;
    const item = {
      id: nextId++, file, name: file.name, stem, size: file.size, openSeq: 0, version: 0,
      docType: state.preset, password: "", status: "busy", checked: true,
      pages: null, front: null, back: null, suggested: null,
      result: { front: null, back: null }, adj: { brightness: 0, contrast: 0, saturation: 0 },
      fit: "stretch", dirty: false,
    };
    state.items.push(item);
    openChain = openChain.then(() => openItem(item));
    if (!state.active) setActive(item);
  }
  renderQueue();
}

async function openItem(item, { keepAdj = false } = {}) {
  if (!state.items.includes(item)) return "removed";
  const seq = ++item.openSeq;
  item.version++;
  item.status = "busy";
  renderQueue();
  const body = new FormData();
  body.append("file", item.file);
  body.append("password", item.password);
  body.append("doc_type", item.docType);
  const stale = () => seq !== item.openSeq || !state.items.includes(item);
  try {
    const data = await (await api("/api/open", body)).json();
    if (stale()) return "stale";
    Object.assign(item, {
      pages: data.pages, type: data.type, label: data.label,
      front: cloneSide(data.front), back: cloneSide(data.back),
      suggested: { front: cloneSide(data.front), back: cloneSide(data.back) },
      result: { front: null, back: null }, status: "ready", error: null,
    });
    if (!keepAdj) item.adj = { brightness: 0, contrast: 0, saturation: 0 };
  } catch (e) {
    if (stale()) return "stale";
    if (e.code === "password_required" || e.code === "wrong_password") {
      item.status = "locked";
      item.error = e.code;
    } else {
      item.status = "error";
      item.error = e.message;
    }
  }
  renderQueue();
  if (item === state.active) {
    if (item.status === "locked") showPassword(item);
    renderAll();
  }
  return item.status;
}

function removeItems(pred) {
  state.items = state.items.filter((i) => !pred(i));
  if (state.active && !state.items.includes(state.active)) setActive(state.items[0] || null);
  renderQueue();
}

const STATUS = {
  busy: ["clock", "Opening…"],
  locked: ["lock", "Password"],
  ready: ["clock", "Ready"],
  cropped: ["check-circle", "Cropped"],
  error: ["alert", "Error"],
};

function renderQueue() {
  const q = $("queue");
  q.replaceChildren();
  for (const item of state.items) {
    const [ic, text] = STATUS[item.status];
    const pages = item.pages ? `${item.pages.length} Page${item.pages.length > 1 ? "s" : ""}` : "PDF";
    const box = el("label", { class: "checkbox", onclick: (e) => e.stopPropagation() },
      el("input", { type: "checkbox", ...(item.checked ? { checked: "" } : {}),
        onchange: (e) => { item.checked = e.target.checked; renderQueue(); } }),
      el("span", { class: "box" }, icon("check")));
    q.append(el("div", {
      class: `q-item${item === state.active ? " active" : ""}`, title: item.error && item.status === "error" ? item.error : item.name,
      onclick: () => setActive(item),
    },
      box,
      el("div", { class: "q-icon" }, icon("file")),
      el("div", { class: "q-body" },
        el("div", { class: "q-name" }, item.name),
        el("div", { class: "q-meta" }, pages, " · ", el("span", { class: "pill" }, fmtSize(item.size)),
          " · ", el("span", { class: `q-status ${item.status}` }, icon(ic), text))),
      el("button", { class: "q-del", "aria-label": `Remove ${item.name}`,
        onclick: (e) => { e.stopPropagation(); removeItems((i) => i === item); } }, icon("trash")),
    ));
  }
  const n = state.items.length;
  const checked = state.items.filter((i) => i.checked).length;
  $("queue-count").textContent = `${n} file${n === 1 ? "" : "s"} in queue`;
  $("queue-tools").hidden = n === 0;
  const batch = selectedCropped().length;
  $("batch-tools").hidden = batch < 2;
  $("dl-selected").querySelector("span").textContent = `Download Selected (${batch})`;
  $("add-more").hidden = n === 0;
  $("dropzone").hidden = n > 0;
  $("select-all").checked = n > 0 && checked === n;
  $("select-all-label").textContent = `Select All (${checked}/${n})`;
  renderBar();
}

$("select-all").addEventListener("change", (e) => {
  for (const i of state.items) i.checked = e.target.checked;
  renderQueue();
});
$("clear-btn").addEventListener("click", () => {
  const any = state.items.some((i) => i.checked);
  removeItems((i) => !any || i.checked);
});

function setActive(item) {
  state.active = item;
  state.editSide = "front";
  state.previewSide = "front";
  state.zoom = 1;
  renderQueue();
  renderAll();
  if (item?.status === "locked") showPassword(item);
}

function renderAll() {
  renderPreset();
  renderWorkspace();
  renderAdjust();
  renderPreview();
  renderBar();
}

// ---------- file input & drag-drop ----------

const fileInput = $("file-input");
fileInput.addEventListener("change", () => { addFiles(fileInput.files); fileInput.value = ""; });
$("add-btn").addEventListener("click", () => fileInput.click());
$("add-more").addEventListener("click", () => fileInput.click());

let dragDepth = 0;
window.addEventListener("dragenter", (e) => { e.preventDefault(); dragDepth++; document.body.classList.add("dragging"); });
window.addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; document.body.classList.remove("dragging"); } });
window.addEventListener("dragover", (e) => e.preventDefault());
window.addEventListener("drop", (e) => {
  e.preventDefault();
  dragDepth = 0;
  document.body.classList.remove("dragging");
  if (!e.dataTransfer?.files?.length) return;
  if (window.onToolDrop?.(e.dataTransfer.files)) return;  // another tool is open
  addFiles(e.dataTransfer.files);
});

// ---------- password modal ----------

let pwItem = null;
function showPassword(item) {
  pwItem = item;
  $("pw-file").textContent = `${item.name} requires an unlock password`;
  $("pw-input").value = "";
  $("pw-submit").disabled = true;
  $("pw-error").hidden = item.error !== "wrong_password";
  $("pw-error").textContent = "Wrong password, please try again.";
  $("pw-modal").hidden = false;
  setTimeout(() => $("pw-input").focus(), 0);
}
function hidePassword() { $("pw-modal").hidden = true; pwItem = null; }

$("pw-input").addEventListener("input", (e) => ($("pw-submit").disabled = !e.target.value));
$("pw-close").addEventListener("click", hidePassword);
$("pw-cancel").addEventListener("click", hidePassword);
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("pw-modal").hidden) hidePassword(); });
$("pw-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const item = pwItem;
  if (!item) return;
  item.password = $("pw-input").value;
  $("pw-submit").disabled = true;
  $("pw-submit").textContent = "Unlocking…";
  $("pw-modal").hidden = true;   // reopened by openItem if still wrong
  await openItem(item);
  $("pw-submit").textContent = "Unlock PDF";
  if (item.status === "ready") toast(`Unlocked · ${item.label}`);
});

// ---------- workspace ----------

const stage = $("stage");
const pageImg = $("page-img");
const boxEls = Object.fromEntries([...stage.querySelectorAll(".crop-box")].map((b) => [b.dataset.side, b]));
for (const b of Object.values(boxEls)) {
  for (const d of ["nw", "n", "ne", "e", "se", "s", "sw", "w"]) b.append(el("span", { class: `handle ${d}`, "data-dir": d }));
}

function currentPage() {
  const item = state.active;
  const side = item?.[state.editSide] || item?.front;
  return side ? side.page : 0;
}

function renderWorkspace() {
  const item = state.active;
  const ready = item && item.pages && (item.status === "ready" || item.status === "cropped");
  $("work-tools").hidden = !ready;
  stage.hidden = !ready;
  $("work-empty").hidden = !!ready;
  if (!ready) {
    const empty = $("work-empty");
    const [h, p] = !item ? ["Document Cropper Workspace", "Upload or select a card in <b>Column 1</b> to crop front and back sides."]
      : item.status === "locked" ? ["Password Protected PDF", "Enter the password to unlock this document."]
      : item.status === "error" ? ["Could not open file", item.error || ""]
      : ["Opening document…", "Finding the card on the page."];
    empty.querySelector("h3").textContent = h;
    empty.querySelector("p").innerHTML = p;
    return;
  }

  for (const b of document.querySelectorAll("[data-edit]")) b.classList.toggle("on", b.dataset.edit === state.editSide);

  const tabs = $("page-tabs");
  tabs.replaceChildren();
  tabs.hidden = item.pages.length < 2;
  const pageNo = currentPage();
  item.pages.forEach((_, i) => tabs.append(el("button", {
    class: i === pageNo ? "on" : "", onclick: () => moveToPage(i),
  }, `Page ${i + 1}`)));

  const page = item.pages[pageNo];
  if (pageImg.dataset.src !== `${item.id}:${pageNo}`) {
    pageImg.src = page.image;
    pageImg.dataset.src = `${item.id}:${pageNo}`;
  }
  layoutStage();
  for (const side of SIDES) {
    const s = item[side];
    const b = boxEls[side];
    const show = s && s.page === pageNo;
    b.hidden = !show;
    if (!show) continue;
    const [x0, y0, x1, y1] = s.box;
    Object.assign(b.style, { left: `${x0 * 100}%`, top: `${y0 * 100}%`, width: `${(x1 - x0) * 100}%`, height: `${(y1 - y0) * 100}%` });
    const active = side === state.editSide;
    b.classList.toggle("active", active);
    b.dataset.label = active ? `${LABEL[side]} (54×86mm)` : `${LABEL[side]} (click to edit)`;
  }
}

function layoutStage() {
  const item = state.active;
  if (!item?.pages) return;
  const page = item.pages[currentPage()];
  const wrap = $("stage-wrap");
  const availW = wrap.clientWidth - 28;
  const availH = wrap.clientHeight - 28;
  const fit = Math.min(availW / page.width, (availH > 200 ? availH : availW * 1.5) / page.height);
  const s = fit * state.zoom;
  stage.style.width = `${page.width * s}px`;
  stage.style.height = `${page.height * s}px`;
  $("zoom-val").textContent = `${Math.round(state.zoom * 100)}%`;
}
window.addEventListener("resize", layoutStage);

function moveToPage(i) {
  const item = state.active;
  const side = item[state.editSide];
  if (side) side.page = i;
  else item[state.editSide] = { page: i, box: [0.1, 0.1, 0.6, 0.4] };
  markDirty(item);
  renderWorkspace();
}

for (const b of document.querySelectorAll("[data-edit]")) {
  b.addEventListener("click", () => selectEditSide(b.dataset.edit));
}

function selectEditSide(side) {
  const item = state.active;
  if (!item) return;
  if (!item[side]) {
    // Single-sided scans: start the back box next to the front one.
    const f = item.front.box;
    const w = f[2] - f[0], h = f[3] - f[1];
    const x0 = f[2] + w * 0.05 + w <= 1 ? f[2] + w * 0.05 : Math.max(0, f[0]);
    const y0 = x0 === f[0] ? Math.min(1 - h, f[3] + h * 0.05) : f[1];
    item[side] = { page: item.front.page, box: [x0, y0, x0 + w, y0 + h] };
    markDirty(item);
  }
  state.editSide = side;
  renderWorkspace();
}

$("auto-snap").addEventListener("click", () => {
  const item = state.active;
  if (!item?.suggested) return;
  item.front = cloneSide(item.suggested.front);
  item.back = cloneSide(item.suggested.back);
  markDirty(item);
  renderWorkspace();
  toast("Snapped to detected card edges");
});

$("zoom-in").addEventListener("click", () => { state.zoom = clamp(state.zoom + 0.25, 0.5, 3); layoutStage(); });
$("zoom-out").addEventListener("click", () => { state.zoom = clamp(state.zoom - 0.25, 0.5, 3); layoutStage(); });
$("zoom-fit").addEventListener("click", () => { state.zoom = 1; layoutStage(); });

function markDirty(item) {
  item.version++;
  if (item.status === "cropped") item.dirty = true;
  renderBar();
  renderPreview();
}

// Drag to move, handles to resize. Corner handles keep the CR80 shape.
let drag = null;
stage.addEventListener("pointerdown", (e) => {
  const boxEl = e.target.closest(".crop-box");
  if (!boxEl) return;
  const item = state.active;
  const side = boxEl.dataset.side;
  if (side !== state.editSide) {
    state.editSide = side;
    renderWorkspace();
  }
  const rect = stage.getBoundingClientRect();
  const page = item.pages[currentPage()];
  drag = {
    side, dir: e.target.dataset.dir || "move", sx: e.clientX, sy: e.clientY,
    start: [...item[side].box], rect, pageAspect: page.width / page.height,
  };
  stage.setPointerCapture(e.pointerId);
  e.preventDefault();
});

stage.addEventListener("pointermove", (e) => {
  if (!drag) return;
  const item = state.active;
  const dx = (e.clientX - drag.sx) / drag.rect.width;
  const dy = (e.clientY - drag.sy) / drag.rect.height;
  let [x0, y0, x1, y1] = drag.start;
  const min = 0.03;
  if (drag.dir === "move") {
    const w = x1 - x0, h = y1 - y0;
    x0 = clamp(x0 + dx, 0, 1 - w);
    y0 = clamp(y0 + dy, 0, 1 - h);
    x1 = x0 + w;
    y1 = y0 + h;
  } else {
    const d = drag.dir;
    if (d.includes("w")) x0 = clamp(x0 + dx, 0, x1 - min);
    if (d.includes("e")) x1 = clamp(x1 + dx, x0 + min, 1);
    if (d.includes("n")) y0 = clamp(y0 + dy, 0, y1 - min);
    if (d.includes("s")) y1 = clamp(y1 + dy, y0 + min, 1);
    if (d.length === 2) {
      // Height follows width so the box stays card-shaped.
      const h = ((x1 - x0) * drag.pageAspect) / CARD_ASPECT;
      if (d.includes("n")) y0 = clamp(y1 - h, 0, y1 - min);
      else y1 = clamp(y0 + h, y0 + min, 1);
    }
  }
  item[drag.side].box = [x0, y0, x1, y1];
  renderWorkspace();
});

const endDrag = () => {
  if (!drag) return;
  const item = state.active;
  const moved = item[drag.side].box.some((v, i) => Math.abs(v - drag.start[i]) > 1e-4);
  drag = null;
  if (moved) markDirty(item);
};
stage.addEventListener("pointerup", endDrag);
stage.addEventListener("pointercancel", endDrag);

// ---------- crop ----------

$("crop-btn").addEventListener("click", cropSelected);

function cropTargets() {
  const usable = (i) => i.pages && (i.status === "ready" || i.status === "cropped");
  const checked = state.items.filter((i) => i.checked && usable(i));
  const active = state.active && usable(state.active) ? [state.active] : [];
  return [...new Set([...active, ...checked])];
}

// Cards that still need cropping: new, edited, or the one on screen.
const pendingCrops = () => cropTargets().filter((i) => i.status !== "cropped" || i.dirty || i === state.active);

// While a batch runs, `cropProgress` holds the button label and renderBar
// leaves the button alone.
let cropping = false, cropAgain = false, cropProgress = "";
async function cropSelected() {
  if (cropping) { cropAgain = true; return; }
  const targets = pendingCrops();
  if (!targets.length) return;
  cropping = true;
  let done = 0;
  const progress = () => {
    cropProgress = targets.length > 1 ? `Cropping ${done}/${targets.length}…` : "Cropping…";
    renderBar();
  };
  progress();
  for (const item of targets) {
    try {
      await cropItem(item);
    } catch (e) {
      toast(`${item.name}: ${e.message}`, "err");
    }
    done++;
    progress();
  }
  cropping = false;
  cropProgress = "";
  renderQueue();
  renderAll();
  if (cropAgain) { cropAgain = false; cropSelected(); }
}

async function cropItem(item) {
  if (!state.items.includes(item)) return;
  const { openSeq, version } = item;
  const body = new FormData();
  body.append("file", item.file);
  body.append("password", item.password);
  body.append("front", JSON.stringify(item.front));
  if (item.back) body.append("back", JSON.stringify(item.back));
  body.append("fit", item.fit);
  body.append("dpi", state.dpi);
  body.append("sharpen", state.sharpen);
  body.append("colour", state.colourAadhaar && item.type === "aadhaar");
  const data = await (await api("/api/crop", body)).json();
  if (item.openSeq !== openSeq) return;  // reopened (e.g. preset changed) meanwhile
  const [front, back] = await Promise.all([toBlob(data.front), data.back ? toBlob(data.back) : null]);
  const size = await imageSize(front);
  item.result = { front, back, ...size };
  item.status = "cropped";
  item.dirty = item.version !== version;   // boxes edited while this crop ran
}

// Results are kept as compressed JPEG blobs; pixels are decoded on demand
// (only a few at a time) so a long queue doesn't exhaust browser memory.
const toBlob = (dataUrl) => fetch(dataUrl).then((r) => r.blob());
async function imageSize(blob) {
  const bmp = await createImageBitmap(blob);
  const size = { width: bmp.width, height: bmp.height };
  bmp.close();
  return size;
}
const pixelCache = new Map();
async function getPixels(blob) {
  if (pixelCache.has(blob)) {
    const v = pixelCache.get(blob);
    pixelCache.delete(blob);
    pixelCache.set(blob, v);   // most recently used last
    return v;
  }
  const bmp = await createImageBitmap(blob);
  const c = el("canvas", { width: bmp.width, height: bmp.height });
  const x = c.getContext("2d");
  x.drawImage(bmp, 0, 0);
  bmp.close();
  const data = x.getImageData(0, 0, c.width, c.height);
  pixelCache.set(blob, data);
  while (pixelCache.size > 4) pixelCache.delete(pixelCache.keys().next().value);
  return data;
}

// ---------- preview & adjustments ----------

const canvas = $("preview-canvas");
const ctx = canvas.getContext("2d");

function adjusted(base, adj) {
  const out = new ImageData(new Uint8ClampedArray(base.data), base.width, base.height);
  const { brightness, contrast, saturation } = adj;
  if (!brightness && !contrast && !saturation) return out;
  const off = brightness * 2.55, cf = 1 + contrast / 100, sf = 1 + saturation / 100;
  const d = out.data;
  for (let i = 0; i < d.length; i += 4) {
    const r = (d[i] + off - 128) * cf + 128;
    const g = (d[i + 1] + off - 128) * cf + 128;
    const b = (d[i + 2] + off - 128) * cf + 128;
    const gray = 0.299 * r + 0.587 * g + 0.114 * b;
    d[i] = gray + (r - gray) * sf;
    d[i + 1] = gray + (g - gray) * sf;
    d[i + 2] = gray + (b - gray) * sf;
  }
  return out;
}

let drawPending = false;
function renderPreview() {
  const item = state.active;
  const result = item?.result?.[state.previewSide] || null;
  const hasAny = !!item?.result?.front;
  for (const b of $("preview-side").children) {
    b.classList.toggle("on", b.dataset.side === state.previewSide);
    b.disabled = hasAny && !item.result[b.dataset.side];
  }
  canvas.hidden = !result;
  $("badges").hidden = !hasAny;
  $("preview-empty").hidden = !!result;
  $("preview-box").classList.toggle("empty", !result);
  if (!result) {
    const ready = item?.pages && item.status !== "locked";
    $("preview-empty-title").textContent = ready ? "Ready to Crop" : "No Document Loaded";
    $("preview-empty-text").textContent = ready
      ? 'Tap "Crop Card" below to render the glossy PVC card preview.'
      : "Select or upload a file in Column 1.";
    return;
  }
  if (drawPending) return;
  drawPending = true;
  requestAnimationFrame(async () => {
    const item = state.active;
    const blob = item?.result?.[state.previewSide];
    if (!blob) { drawPending = false; return; }
    const px = await getPixels(blob);
    drawPending = false;
    if (state.active !== item || item.result[state.previewSide] !== blob) return renderPreview();
    if (canvas.width !== px.width) Object.assign(canvas, { width: px.width, height: px.height });
    ctx.putImageData(adjusted(px, item.adj), 0, 0);
  });
}

for (const b of $("preview-side").children) {
  b.addEventListener("click", () => { state.previewSide = b.dataset.side; renderPreview(); });
}

const SLIDERS = ["brightness", "contrast", "saturation"];
function renderAdjust() {
  const adj = state.active?.adj || { brightness: 0, contrast: 0, saturation: 0 };
  for (const k of SLIDERS) {
    $(k).value = adj[k];
    $(`${k}-val`).textContent = `${adj[k]}%`;
  }
  for (const b of $("fit-mode").children) b.classList.toggle("on", b.dataset.fit === (state.active?.fit || "stretch"));
}
for (const k of SLIDERS) {
  $(k).addEventListener("input", (e) => {
    if (!state.active) return;
    state.active.adj[k] = Number(e.target.value);
    $(`${k}-val`).textContent = `${e.target.value}%`;
    renderPreview();
  });
}
$("reset-adj").addEventListener("click", () => {
  if (!state.active) return;
  state.active.adj = { brightness: 0, contrast: 0, saturation: 0 };
  renderAdjust();
  renderPreview();
});

// Stretch the 1st–99th percentile of luminance to the full range.
$("auto-enhance").addEventListener("click", async () => {
  const item = state.active;
  if (!item?.result?.front) return toast("Crop the card first");
  const base = await getPixels(item.result.front);
  const hist = new Uint32Array(256);
  const d = base.data;
  let n = 0;
  for (let i = 0; i < d.length; i += 4) {
    hist[Math.round(0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2])]++;
    n++;
  }
  const pct = (p) => { let s = 0; for (let v = 0; v < 256; v++) { s += hist[v]; if (s >= n * p) return v; } return 255; };
  const lo = pct(0.01), hi = pct(0.99);
  const cf = Math.min(1.35, 255 / Math.max(1, hi - lo));
  item.adj = {
    contrast: Math.round((cf - 1) * 100),
    brightness: Math.round(clamp(-((lo + hi) / 2 - 128) / 2.55, -20, 20)),
    saturation: 10,
  };
  renderAdjust();
  renderPreview();
});

function segmented(container, attr, onPick) {
  container.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b || b.disabled) return;
    for (const x of container.children) x.classList.toggle("on", x === b);
    onPick(b.dataset[attr]);
  });
}
segmented($("fit-mode"), "fit", (v) => {
  const item = state.active;
  if (!item) return;
  item.fit = v;
  if (item.status === "cropped") cropSelected();
});
segmented($("color-mode"), "color", (v) => (state.color = v));

// Quality and sharpening apply when cropping, so re-crop what is already done.
function recropAll() {
  for (const i of state.items) if (i.status === "cropped") { i.dirty = true; i.version++; }
  renderBar();
  if (state.active?.status === "cropped") cropSelected();
}
segmented($("quality-mode"), "dpi", (v) => { state.dpi = Number(v); recropAll(); });
segmented($("sharpen-mode"), "sharpen", (v) => { state.sharpen = v === "on"; recropAll(); });
segmented($("colour-mode"), "colour", (v) => { state.colourAadhaar = v === "on"; recropAll(); });

// ---------- action bar, downloads, print ----------

function renderBar() {
  const item = state.active;
  $("doc-name").textContent = item ? item.name : "No document loaded";
  const px = item?.result;
  $("doc-size").textContent = px?.front ? `${px.width}×${px.height} px` : state.dpi === 600 ? "2022×1275 px" : "1011×638 px";
  const status = !item ? "Upload in Column 1"
    : item.status === "busy" ? "Opening…"
    : item.status === "locked" ? "Password required"
    : item.status === "error" ? "Could not open"
    : item.status === "cropped" ? (item.dirty ? "Edited · crop again" : "Cropped & Ready")
    : "Ready to Crop";
  $("doc-status").textContent = status;
  const targets = pendingCrops();
  const cropped = item?.status === "cropped";
  const btn = $("crop-btn");
  if (cropping) {
    btn.hidden = false;
    btn.disabled = true;
    btn.querySelector("span").textContent = cropProgress;
  } else {
    btn.disabled = !targets.length;
    btn.hidden = cropped && !item.dirty;
    btn.querySelector("span").textContent = targets.length > 1 ? `Crop ${targets.length} Cards` : "Crop Card";
  }
  $("dl-row").hidden = !cropped;
  $("print-btn").hidden = !cropped || item.dirty;
  $("dl-back").disabled = !item?.result?.back;
}

const hasAdj = (adj) => adj.brightness || adj.contrast || adj.saturation;

// Card side with the user's adjustments as a JPEG blob (rounded corners on
// white when `rounded`, for printing; export rounds on the server).
async function sideBlob(item, side, { rounded = false } = {}) {
  const blob = item.result[side];
  if (!blob) return null;
  if (!hasAdj(item.adj) && !rounded) return blob;
  const px = await getPixels(blob);
  const src = el("canvas", { width: px.width, height: px.height });
  src.getContext("2d").putImageData(adjusted(px, item.adj), 0, 0);
  let out = src;
  if (rounded) {
    out = el("canvas", { width: px.width, height: px.height });
    const x = out.getContext("2d");
    x.fillStyle = "#fff";
    x.fillRect(0, 0, out.width, out.height);
    x.beginPath();
    x.roundRect(0, 0, out.width, out.height, out.width * (3.18 / 85.6));
    x.clip();
    x.drawImage(src, 0, 0);
  }
  return new Promise((resolve) => out.toBlob(resolve, "image/jpeg", 0.95));
}

function saveBlob(blob, filename) {
  const a = el("a", { href: URL.createObjectURL(blob), download: filename });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

// Bottom-bar actions work on the card on screen; the queue's batch buttons
// work on every ticked card that is cropped and up to date.
const selectedCropped = () => state.items.filter((i) => i.checked && i.status === "cropped" && !i.dirty);

const EXPORT_BATCH = 40;  // cards per download request (server allows 100 images)

async function exportCards(items, sides, format) {
  const batches = [];
  for (let i = 0; i < items.length; i += EXPORT_BATCH) batches.push(items.slice(i, i + EXPORT_BATCH));
  for (const [n, batch] of batches.entries()) {
    if (batches.length > 1) toast(`Preparing download ${n + 1} of ${batches.length}…`);
    const body = new FormData();
    for (const item of batch) {
      for (const side of sides) {
        const blob = await sideBlob(item, side);
        if (blob) body.append("files", blob, `${item.stem}__${side}.jpg`);
      }
    }
    body.append("format", format);
    body.append("cmyk", state.color === "cmyk");
    const res = await api("/api/export", body);
    let name = /filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") || "")?.[1] || "card";
    if (batches.length > 1) name = name.replace(/(\.\w+)$/, `_part${n + 1}$1`);
    saveBlob(await res.blob(), name);
  }
}

async function withBusy(btn, fn) {
  btn.disabled = true;
  try { await fn(); } catch (e) { toast(e.message || "Download failed", "err"); } finally { btn.disabled = false; renderBar(); }
}

$("dl-all").addEventListener("click", (e) => withBusy(e.currentTarget, () => exportCards([state.active], SIDES, "zip")));
$("dl-selected").addEventListener("click", (e) => withBusy(e.currentTarget, () => exportCards(selectedCropped(), SIDES, "zip")));
$("dl-front").addEventListener("click", (e) => withBusy(e.currentTarget, () => exportCards([state.active], ["front"], "jpg")));
$("dl-back").addEventListener("click", (e) => withBusy(e.currentTarget, () => exportCards([state.active], ["back"], "jpg")));

$("print-btn").addEventListener("click", () => printCards([state.active]));
$("print-selected").addEventListener("click", () => printCards(selectedCropped()));

async function printCards(items) {
  const urls = [];
  for (const item of items) {
    for (const side of SIDES) {
      const blob = await sideBlob(item, side, { rounded: true });
      if (blob) urls.push(URL.createObjectURL(blob));
    }
  }
  const frame = $("print-frame");
  const doc = frame.contentDocument;
  doc.open();
  doc.write(`<!doctype html><html><head><title>PVC cards</title><style>
    @page { size: 85.6mm 53.98mm; margin: 0; }
    html, body { margin: 0; }
    img { display: block; width: 85.6mm; height: 53.98mm; break-after: page; }
  </style></head><body>${urls.map((u) => `<img src="${u}">`).join("")}</body></html>`);
  doc.close();
  await Promise.all([...doc.images].map((img) => img.decode().catch(() => {})));
  frame.contentWindow.focus();
  frame.contentWindow.print();
  setTimeout(() => urls.forEach(URL.revokeObjectURL), 60000);
}

renderQueue();
renderAll();
