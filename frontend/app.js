"use strict";
/* Frontend for the watermark-remover clone.
   Images: up to 10 files, 20 MB each -> POST /api/remove/image (per file)
                                        or /api/remove/images (ZIP of all)
   Video:  exactly 1 file, 50 MB max  -> POST /api/remove/video          */

const LIMITS = {
  image: { maxFiles: 10, maxMB: 20, accept: ["image/png", "image/jpeg", "image/webp", "image/bmp"], exts: [".png", ".jpg", ".jpeg", ".webp", ".bmp"] },
  video: { maxFiles: 1, maxMB: 50, accept: ["video/mp4", "video/quicktime", "video/webm", "video/x-matroska"], exts: [".mp4", ".mov", ".webm", ".mkv"] },
};

let mode = "image";
let items = []; // {file, beforeUrl, afterUrl, status, el}

const $ = (id) => document.getElementById(id);
const dz = $("dropzone"), input = $("fileInput"), list = $("fileList"),
      msg = $("messages"), processBtn = $("processBtn"),
      downloadAllBtn = $("downloadAllBtn"), clearBtn = $("clearBtn"),
      progress = $("progress");

function setMode(next) {
  mode = next;
  items = []; render();
  $("tabImages").classList.toggle("active", mode === "image");
  $("tabVideo").classList.toggle("active", mode === "video");
  input.multiple = mode === "image";
  input.accept = mode === "image" ? "image/*" : "video/*";
  $("dzLimits").textContent = mode === "image"
    ? "Up to 10 images · 20 MB each · PNG, JPG, WebP, BMP"
    : "Single video · up to 50 MB · MP4, MOV, WebM, MKV";
  msg.innerHTML = "";
}
$("tabImages").onclick = () => setMode("image");
$("tabVideo").onclick = () => setMode("video");

function say(text, cls = "") {
  msg.innerHTML = text ? `<div class="${cls}">${text}</div>` : "";
}

function validate(files) {
  const L = LIMITS[mode];
  const errors = [];
  if (files.length > L.maxFiles)
    errors.push(`Too many files: max ${L.maxFiles} for ${mode} mode.`);
  for (const f of files) {
    const ext = "." + f.name.split(".").pop().toLowerCase();
    if (!L.exts.includes(ext)) errors.push(`${f.name}: unsupported type (${ext}).`);
    if (f.size > L.maxMB * 1024 * 1024) errors.push(`${f.name}: over ${L.maxMB} MB.`);
  }
  return errors;
}

function addFiles(fileList) {
  const files = [...fileList];
  const errors = validate(mode === "image" ? [...items.map(i => i.file), ...files] : files);
  if (errors.length) { say(errors.join("<br>"), "error"); return; }
  say("");
  const fresh = (mode === "video") ? files.slice(0, 1) : files;
  if (mode === "video") items = [];
  for (const file of fresh)
    items.push({ file, beforeUrl: URL.createObjectURL(file), afterUrl: null, status: "ready" });
  render();
}

dz.addEventListener("click", () => input.click());
dz.addEventListener("keydown", (e) => { if (e.key === "Enter") input.click(); });
input.addEventListener("change", () => { addFiles(input.files); input.value = ""; });
for (const ev of ["dragenter", "dragover"]) dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("drag"); });
for (const ev of ["dragleave", "drop"]) dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("drag"); });
dz.addEventListener("drop", (e) => addFiles(e.dataTransfer.files));

function previewHTML(item) {
  const tag = mode === "image"
    ? `<img src="${item.beforeUrl}" alt="">`
    : `<video src="${item.beforeUrl}" controls muted></video>`;
  if (!item.afterUrl)
    return `<div class="single-preview">${tag}</div>`;
  const afterTag = mode === "image"
    ? `<img src="${item.afterUrl}" alt="">`
    : `<video src="${item.afterUrl}" controls muted></video>`;
  return `<div class="compare">
      ${tag}<div class="after">${afterTag}</div>
      <span class="lbl b">Before</span><span class="lbl a">After · clean</span>
      <input type="range" min="0" max="100" value="50"
             oninput="this.parentElement.style.setProperty('--pos', this.value + '%')">
    </div>`;
}

function render() {
  list.innerHTML = "";
  for (const item of items) {
    const el = document.createElement("div");
    el.className = "file-card";
    el.innerHTML = `
      <div class="file-head">
        <span>${item.file.name} <span class="size">(${(item.file.size / 1048576).toFixed(2)} MB)</span></span>
        <span class="status ${item.status === "done" ? "done" : item.status === "working" ? "working" : ""}">${item.status}</span>
      </div>
      ${previewHTML(item)}
      ${item.afterUrl ? `<div class="file-actions">
        <button class="primary dl">⬇ Download clean file</button></div>` : ""}`;
    const dl = el.querySelector(".dl");
    if (dl) dl.onclick = () => download(item.afterUrl, "clean_" + item.file.name);
    list.appendChild(el);
    item.el = el;
  }
  const any = items.length > 0;
  processBtn.disabled = !any || items.every(i => i.status === "done");
  clearBtn.disabled = !any;
  downloadAllBtn.disabled = !(mode === "image" && items.some(i => i.status === "done"));
}

function download(url, name) {
  const a = document.createElement("a");
  a.href = url; a.download = name; a.click();
}

async function postFile(url, file) {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch(url, { method: "POST", body: fd });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).error || detail; } catch {}
    throw new Error(detail);
  }
  return URL.createObjectURL(await res.blob());
}

processBtn.onclick = async () => {
  say(""); progress.classList.remove("hidden");
  processBtn.disabled = true;
  const endpoint = mode === "image" ? "/api/remove/image" : "/api/remove/video";
  for (const item of items) {
    if (item.status === "done") continue;
    item.status = "working"; render();
    try {
      item.afterUrl = await postFile(endpoint, item.file);
      item.status = "done";
    } catch (e) {
      item.status = "ready";
      say(`⚠ ${item.file.name}: ${e.message}`, "error");
    }
    render();
  }
  progress.classList.add("hidden");
  say("Done — drag the slider on each file to compare Before / After.", "ok");
  render();
};

downloadAllBtn.onclick = async () => {
  const fd = new FormData();
  for (const i of items) fd.append("files", i.file);
  const res = await fetch("/api/remove/images", { method: "POST", body: fd });
  if (!res.ok) { say("ZIP download failed.", "error"); return; }
  download(URL.createObjectURL(await res.blob()), "cleaned_images.zip");
};

clearBtn.onclick = () => { items = []; say(""); render(); };
render();
