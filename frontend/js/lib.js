import { t } from "./i18n.js";
// Utilidades compartidas: API, formato, UI (toasts, modales, paneles), gráficos.

export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

export async function api(path, { method = "GET", body, raw = false } = {}) {
  const opts = { method, headers: {}, credentials: "same-origin" };
  if (body !== undefined) {
    opts.headers["content-type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(`/api${path}`, opts);
  if (res.status === 401 || res.status === 428) {
    window.dispatchEvent(new CustomEvent("bipon:auth", { detail: res.status }));
  }
  if (!res.ok) {
    let msg = res.statusText;
    try { const j = await res.json(); msg = j.detail || msg; } catch {}
    throw new ApiError(res.status, typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  if (raw) return res;
  const ct = res.headers.get("content-type") || "";
  return ct.includes("json") ? res.json() : res.text();
}
api.get = (p) => api(p);
api.post = (p, body = {}) => api(p, { method: "POST", body });
api.put = (p, body) => api(p, { method: "PUT", body });
api.patch = (p, body) => api(p, { method: "PATCH", body });
api.del = (p) => api(p, { method: "DELETE" });

/** Lee un endpoint SSE (text/event-stream) y llama onEvent por cada mensaje. */
export async function stream(path, onEvent, signal) {
  const res = await fetch(`/api${path}`, { credentials: "same-origin", signal });
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch {}
    throw new ApiError(res.status, msg);
  }
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
      const line = chunk.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue;
      const ev = JSON.parse(line.slice(6));
      if (ev.end) return;
      onEvent(ev);
    }
  }
}

// ---------------------------------------------------------------- formato
const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ESC[c]);
const LOC = () => (document.documentElement.lang === "en" ? "en-US" : "es");
export const num = (n) => (n ?? 0).toLocaleString(LOC());
export const pct = (n) => `${(n ?? 0).toLocaleString(LOC(), { maximumFractionDigits: 1 })} %`;
export const ms = (n) => (n == null ? "—" : `${n < 10 ? n.toFixed(1) : Math.round(n)} ms`);
export const mbps = (n) => (n == null ? "—" : `${n >= 100 ? Math.round(n) : n.toFixed(1)} Mbps`);
export function bytes(b) {
  if (b == null) return "—";
  const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0;
  while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
  return `${b.toFixed(b >= 10 || i === 0 ? 0 : 1)} ${u[i]}`;
}
export function ago(ts) {
  if (!ts) return "nunca";
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return "ahora";
  if (s < 3600) return `hace ${Math.floor(s / 60)} min`;
  if (s < 86400) return `hace ${Math.floor(s / 3600)} h`;
  return `hace ${Math.floor(s / 86400)} d`;
}
export function duration(s) {
  s = Math.floor(s || 0);
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  if (d) return `${d} d ${h} h`;
  if (h) return `${h} h ${m} min`;
  return `${m} min`;
}
export const time = (ts) => new Date(ts * 1000).toLocaleTimeString(LOC(), { hour: "2-digit", minute: "2-digit", second: "2-digit" });
export const datetime = (ts) => new Date(ts * 1000).toLocaleString(LOC(), { dateStyle: "short", timeStyle: "short" });

// ---------------------------------------------------------------- DOM
export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
export function h(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}
/** Sustituye un contenedor por una copia vacía (elimina listeners acumulados). */
export function fresh(node) {
  const n = node.cloneNode(false);
  node.replaceWith(n);
  return n;
}

/** Delegación de eventos: on(root, "click", "[data-x]", fn) */
export function on(root, type, sel, fn) {
  root.addEventListener(type, (e) => {
    const el = e.target.closest(sel);
    if (el && root.contains(el)) fn(e, el);
  });
}

// ---------------------------------------------------------------- toasts
export function toast(msg, kind = "ok", timeout = 3500) {
  let box = $("#toasts");
  if (!box) { box = h('<div id="toasts" aria-live="polite"></div>'); document.body.append(box); }
  const icon = { ok: "check", error: "alert", info: "info", warn: "alert" }[kind] || "info";
  const t = h(`<div class="toast toast-${kind}">${ico(icon)}<span>${esc(msg)}</span></div>`);
  box.append(t);
  setTimeout(() => { t.classList.add("out"); setTimeout(() => t.remove(), 300); }, timeout);
}
export const toastErr = (e) => toast(e?.message || String(e), "error", 6000);

/** Ejecuta una acción asíncrona mostrando estado de carga en el botón. */
export async function busy(btn, fn) {
  if (btn) { btn.disabled = true; btn.classList.add("loading"); }
  try { return await fn(); }
  catch (e) { toastErr(e); throw e; }
  finally { if (btn) { btn.disabled = false; btn.classList.remove("loading"); } }
}

// ---------------------------------------------------------------- modal / drawer
export function modal({ title, body, actions = [], wide = false, onClose }) {
  const m = h(`<div class="overlay"><div class="modal ${wide ? "wide" : ""}" role="dialog" aria-modal="true">
    <header><h3>${esc(title)}</h3><button class="icon-btn" data-close aria-label="Cerrar">${ico("x")}</button></header>
    <div class="modal-body"></div><footer></footer></div></div>`);
  const bodyEl = $(".modal-body", m);
  if (typeof body === "string") bodyEl.innerHTML = body; else if (body) bodyEl.append(body);
  const foot = $("footer", m);
  if (!actions.length) foot.remove();
  const close = () => { m.classList.add("out"); setTimeout(() => m.remove(), 180); onClose?.(); document.removeEventListener("keydown", key); };
  const key = (e) => e.key === "Escape" && close();
  document.addEventListener("keydown", key);
  for (const a of actions) {
    const b = h(`<button class="btn ${a.kind || ""}">${a.icon ? ico(a.icon) : ""}${esc(a.label)}</button>`);
    b.onclick = async () => {
      if (!a.onClick) return close();
      try { const r = await busy(b, () => a.onClick(m)); if (r !== false) close(); } catch {}
    };
    foot.append(b);
  }
  m.addEventListener("mousedown", (e) => { if (e.target === m) close(); });
  $("[data-close]", m).onclick = close;
  document.body.append(m);
  setTimeout(() => $("input,select,textarea", bodyEl)?.focus(), 50);
  return { el: m, body: bodyEl, close };
}

export function confirmDialog(title, text, { danger = true, label = "Confirmar" } = {}) {
  return new Promise((resolve) => {
    let ok = false;
    modal({ title, body: `<p>${esc(text)}</p>`, onClose: () => resolve(ok),
      actions: [{ label: "Cancelar" }, { label, kind: danger ? "danger" : "primary", onClick: () => { ok = true; } }] });
  });
}

export function drawer({ title, body, onClose }) {
  $(".drawer-wrap")?.remove();
  const d = h(`<div class="drawer-wrap"><aside class="drawer"><header><h3>${esc(title)}</h3>
    <button class="icon-btn" data-close aria-label="Cerrar">${ico("x")}</button></header><div class="drawer-body"></div></aside></div>`);
  const b = $(".drawer-body", d);
  if (typeof body === "string") b.innerHTML = body; else if (body) b.append(body);
  const key = (e) => { if (e.key === "Escape" && !document.querySelector(".overlay")) close(); };
  const close = () => { d.classList.add("out"); setTimeout(() => d.remove(), 200); document.removeEventListener("keydown", key); onClose?.(); };
  document.addEventListener("keydown", key);
  d.addEventListener("mousedown", (e) => { if (e.target === d) close(); });
  $("[data-close]", d).onclick = close;
  document.body.append(d);
  return { el: d, body: b, close };
}

export function download(name, content, type = "text/plain") {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const a = h(`<a download="${esc(name)}" href="${url}"></a>`);
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export async function copy(text) {
  try { await navigator.clipboard.writeText(text); toast("Copiado al portapapeles"); }
  catch { toast("No se pudo copiar", "error"); }
}

// ---------------------------------------------------------------- componentes
export const toggle = (name, checked, attrs = "") =>
  `<label class="switch"><input type="checkbox" name="${esc(name)}" ${checked ? "checked" : ""} ${attrs}><span></span></label>`;

export const badge = (text, kind = "") => `<span class="badge ${kind}">${esc(text)}</span>`;

export function statusDot(state) {
  return `<span class="dot dot-${state}"></span>`;
}

export function formData(form) {
  const out = {};
  for (const el of form.elements) {
    if (!el.name) continue;
    if (el.type === "checkbox") out[el.name] = el.checked;
    else if (el.type === "number") out[el.name] = el.value === "" ? null : Number(el.value);
    else out[el.name] = el.value;
  }
  return out;
}

export function empty(text, icon = "info") {
  return `<div class="empty">${ico(icon)}<p>${esc(text)}</p></div>`;
}

export function help(text) {
  return `<span class="help" tabindex="0" data-tip="${esc(text)}">?</span>`;
}

// ---------------------------------------------------------------- iconos (SVG en línea, estilo trazo)
const P = {
  home: "M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z",
  list: "M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01",
  shield: "M12 3 4 6v6c0 5 3.4 8.5 8 9 4.6-.5 8-4 8-9V6z",
  devices: "M4 5h12v9H4zM2 18h16M19 8h3v11h-3z",
  users: "M16 19v-1a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v1M9 10a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM22 19v-1a4 4 0 0 0-3-3.9M16 4.1a3 3 0 0 1 0 5.8",
  globe: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18",
  server: "M4 4h16v6H4zM4 14h16v6H4zM8 7h.01M8 17h.01",
  gauge: "M12 14l4-4M3.3 17a9 9 0 1 1 17.4 0",
  network: "M12 3v6M5 21v-4h14v4M12 9a3 3 0 1 0 0 .01M12 12v5M5 17v-2h14v2",
  lock: "M6 11h12v10H6zM8 11V7a4 4 0 0 1 8 0v4",
  tool: "M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.5 2.5-2.4-.6-.6-2.4z",
  activity: "M3 12h4l3-8 4 16 3-8h4",
  settings: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z",
  check: "M5 12l5 5L20 7",
  x: "M6 6l12 12M18 6 6 18",
  alert: "M12 9v4M12 17h.01M10.3 3.9 2 18a2 2 0 0 0 1.7 3h16.6a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z",
  info: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 16v-4M12 8h.01",
  plus: "M12 5v14M5 12h14",
  trash: "M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14",
  edit: "M4 20h4L19 9l-4-4L4 16zM14 6l4 4",
  refresh: "M20 11a8 8 0 0 0-14.9-3.5M4 4v4h4M4 13a8 8 0 0 0 14.9 3.5M20 20v-4h-4",
  play: "M7 4v16l13-8z",
  pause: "M7 4h4v16H7zM14 4h4v16h-4z",
  power: "M12 3v9M6.3 6.3a8 8 0 1 0 11.4 0",
  search: "M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.3-4.3",
  download: "M12 3v12M7 10l5 5 5-5M4 21h16",
  upload: "M12 21V9M7 14l5-5 5 5M4 3h16",
  copy: "M8 8h12v12H8zM4 16V4h12",
  zap: "M13 2 3 14h9l-1 8 10-12h-9z",
  wifi: "M5 12.5a10 10 0 0 1 14 0M8.5 16a5 5 0 0 1 7 0M2 9a15 15 0 0 1 20 0M12 20h.01",
  router: "M4 14h16v6H4zM8 17h.01M12 17h.01M7 14l-2-8M17 14l2-8",
  phone: "M7 2h10v20H7zM11 18h2",
  tv: "M3 5h18v12H3zM8 21h8",
  laptop: "M4 5h16v11H4zM2 19h20",
  console: "M6 9h12a4 4 0 0 1 0 8h-1l-2-2H9l-2 2H6a4 4 0 0 1 0-8zM8 12v2M7 13h2M16 12h.01M18 14h.01",
  printer: "M6 9V3h12v6M6 18H4v-7h16v7h-2M6 14h12v7H6z",
  camera: "M3 7h4l2-3h6l2 3h4v13H3zM12 17a4 4 0 1 0 0-8 4 4 0 0 0 0 8z",
  speaker: "M6 2h12v20H6zM12 18a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM12 6h.01",
  chip: "M7 7h10v10H7zM9 3v4M15 3v4M9 17v4M15 17v4M3 9h4M3 15h4M17 9h4M17 15h4",
  tablet: "M5 2h14v20H5zM11 18h2",
  help: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM9.1 9a3 3 0 0 1 5.8 1c0 2-3 2-3 4M12 17h.01",
  logout: "M9 21H5V3h4M16 17l5-5-5-5M21 12H9",
  menu: "M4 6h16M4 12h16M4 18h16",
  clock: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7v5l3 3",
  moon: "M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z",
  sun: "M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10zM12 1v2M12 21v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1 12h2M21 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4",
  key: "M15 7a4 4 0 1 1-3.9 5H9v2H7v2H4v-3l5.1-5.1A4 4 0 0 1 15 7zM16 8h.01",
  link: "M10 14a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1M14 10a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1",
  qr: "M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h3v3h-3zM18 18h3v3h-3zM14 20h1M20 14h1",
  external: "M14 3h7v7M21 3l-9 9M19 14v7H3V5h7",
  target: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10zM12 13a1 1 0 1 0 0-2 1 1 0 0 0 0 2z",
};
export function ico(name, cls = "") {
  return `<svg class="ico ${cls}" viewBox="0 0 24 24" aria-hidden="true"><path d="${P[name] || P.info}"/></svg>`;
}

export const KIND_ICON = { router: "router", phone: "phone", tablet: "tablet", tv: "tv", console: "console", printer: "printer",
  speaker: "speaker", camera: "camera", iot: "chip", server: "server", computer: "laptop", unknown: "wifi" };
export const KIND_LABEL = { router: "Router", phone: "Teléfono", tablet: "Tablet", tv: "TV / Streaming", console: "Consola",
  printer: "Impresora", speaker: "Altavoz", camera: "Cámara", iot: "Domótica / IoT", server: "Servidor / NAS", computer: "Ordenador", unknown: "Desconocido" };

// ---------------------------------------------------------------- gráficos (canvas, sin dependencias)
function css(v) { return getComputedStyle(document.documentElement).getPropertyValue(v).trim(); }

function setupCanvas(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.max(1, rect.width * dpr);
  canvas.height = Math.max(1, rect.height * dpr);
  const g = canvas.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { g, w: rect.width, h: rect.height };
}

/**
 * Gráfico de líneas/áreas apiladas.
 * series: [{name, color, values:[...], fill?:bool}], labels: [...] (timestamps)
 */
export function lineChart(canvas, { labels, series, yFmt = (v) => num(Math.round(v)), xFmt, stacked = false, minY = 0 }) {
  const draw = () => {
    const { g, w, h: H } = setupCanvas(canvas);
    const pad = { l: 44, r: 10, t: 10, b: 22 };
    const cw = w - pad.l - pad.r, ch = H - pad.t - pad.b;
    g.clearRect(0, 0, w, H);
    const n = labels.length;
    if (!n) {
      g.fillStyle = css("--muted"); g.font = "13px system-ui"; g.textAlign = "center";
      g.fillText(t("Sin datos todavía"), w / 2, H / 2); return;
    }
    let maxY = 0;
    const stack = new Array(n).fill(0);
    const pts = series.map((s) => s.values.map((v, i) => {
      const base = stacked ? stack[i] : 0;
      const val = (v ?? 0) + base;
      if (stacked) stack[i] = val;
      maxY = Math.max(maxY, val);
      return v == null ? null : { v: val, base };
    }));
    maxY = Math.max(maxY * 1.1, minY || 1);
    const x = (i) => pad.l + (n === 1 ? cw / 2 : (i / (n - 1)) * cw);
    const y = (v) => pad.t + ch - (v / maxY) * ch;
    // rejilla
    g.strokeStyle = css("--grid"); g.lineWidth = 1; g.fillStyle = css("--muted"); g.font = "11px system-ui"; g.textAlign = "right";
    for (let i = 0; i <= 4; i++) {
      const v = (maxY / 4) * i, yy = y(v);
      g.beginPath(); g.moveTo(pad.l, yy); g.lineTo(w - pad.r, yy); g.stroke();
      g.fillText(yFmt(v), pad.l - 6, yy + 4);
    }
    g.textAlign = "center";
    const fmt = xFmt || ((t) => new Date(t * 1000).toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit" }));
    const step = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(cw / 70))));
    for (let i = 0; i < n; i += step) g.fillText(fmt(labels[i]), x(i), H - 6);
    // series (en orden inverso para que la primera quede delante si están apiladas)
    const order = stacked ? [...series.keys()].reverse() : [...series.keys()];
    for (const si of order) {
      const s = series[si], p = pts[si];
      g.strokeStyle = s.color; g.lineWidth = 2; g.lineJoin = "round";
      g.beginPath(); let started = false;
      p.forEach((pt, i) => { if (!pt) { started = false; return; } if (!started) { g.moveTo(x(i), y(pt.v)); started = true; } else g.lineTo(x(i), y(pt.v)); });
      g.stroke();
      if (s.fill !== false) {
        const grad = g.createLinearGradient(0, pad.t, 0, pad.t + ch);
        grad.addColorStop(0, s.color + "55"); grad.addColorStop(1, s.color + "05");
        g.fillStyle = grad; g.beginPath();
        let first = -1, last = -1;
        p.forEach((pt, i) => { if (pt) { if (first < 0) { first = i; g.moveTo(x(i), y(pt.v)); } else g.lineTo(x(i), y(pt.v)); last = i; } });
        if (first >= 0) {
          for (let i = last; i >= first; i--) { const pt = p[i]; if (pt) g.lineTo(x(i), y(stacked ? pt.base : 0)); }
          g.closePath(); g.fill();
        }
      }
    }
  };
  draw();
  attachHover(canvas, labels, series, yFmt, xFmt);
  const ro = new ResizeObserver(draw); ro.observe(canvas);
  return { redraw: draw, destroy: () => ro.disconnect() };
}

function attachHover(canvas, labels, series, yFmt, xFmt) {
  const parent = canvas.parentElement;
  parent.style.position = "relative";
  let tip = $(".chart-tip", parent);
  if (!tip) { tip = h('<div class="chart-tip"></div>'); parent.append(tip); }
  canvas.onmousemove = (e) => {
    const r = canvas.getBoundingClientRect();
    const n = labels.length; if (!n) return;
    const cw = r.width - 54;
    const i = Math.min(n - 1, Math.max(0, Math.round(((e.clientX - r.left - 44) / cw) * (n - 1))));
    const t = xFmt ? xFmt(labels[i]) : new Date(labels[i] * 1000).toLocaleString("es", { dateStyle: "short", timeStyle: "short" });
    tip.innerHTML = `<b>${esc(t)}</b>` + series.map((s) => `<div><i style="background:${s.color}"></i>${esc(s.name)}: <b>${s.values[i] == null ? "—" : esc(yFmt(s.values[i]))}</b></div>`).join("");
    tip.style.display = "block";
    const left = Math.min(e.clientX - r.left + 12, r.width - tip.offsetWidth - 4);
    tip.style.left = `${Math.max(0, left)}px`; tip.style.top = "8px";
  };
  canvas.onmouseleave = () => { tip.style.display = "none"; };
}

export function donut(canvas, items, { center } = {}) {
  const draw = () => {
    const { g, w, h: H } = setupCanvas(canvas);
    const r = Math.min(w, H) / 2 - 4, cx = w / 2, cy = H / 2;
    g.clearRect(0, 0, w, H);
    const total = items.reduce((a, b) => a + b.value, 0);
    let a0 = -Math.PI / 2;
    if (!total) { g.strokeStyle = css("--grid"); g.lineWidth = r * 0.28; g.beginPath(); g.arc(cx, cy, r * 0.82, 0, Math.PI * 2); g.stroke(); }
    for (const it of items) {
      const a1 = a0 + (it.value / (total || 1)) * Math.PI * 2;
      g.strokeStyle = it.color; g.lineWidth = r * 0.28; g.beginPath(); g.arc(cx, cy, r * 0.82, a0, a1); g.stroke();
      a0 = a1;
    }
    if (center) {
      g.fillStyle = css("--text"); g.textAlign = "center"; g.font = `600 ${Math.round(r * 0.32)}px system-ui`;
      g.fillText(center[0], cx, cy + r * 0.08);
      g.fillStyle = css("--muted"); g.font = `${Math.round(r * 0.15)}px system-ui`;
      g.fillText(center[1] || "", cx, cy + r * 0.3);
    }
  };
  draw();
  const ro = new ResizeObserver(draw); ro.observe(canvas);
  return { destroy: () => ro.disconnect() };
}

/** Medidor semicircular para el test de velocidad. */
export function gauge(canvas, value, max, { label = "", color } = {}) {
  const { g, w, h: H } = setupCanvas(canvas);
  const cx = w / 2, cy = H * 0.78, r = Math.min(w / 2, H * 0.75) - 10;
  g.clearRect(0, 0, w, H);
  g.lineCap = "round"; g.lineWidth = 14;
  g.strokeStyle = css("--grid"); g.beginPath(); g.arc(cx, cy, r, Math.PI, 0); g.stroke();
  // escala logarítmica suave
  const frac = Math.min(1, Math.log10(1 + (value || 0)) / Math.log10(1 + max));
  const grad = g.createLinearGradient(cx - r, 0, cx + r, 0);
  grad.addColorStop(0, css("--accent")); grad.addColorStop(1, color || css("--accent-2"));
  g.strokeStyle = grad; g.beginPath(); g.arc(cx, cy, r, Math.PI, Math.PI + Math.PI * frac); g.stroke();
  g.fillStyle = css("--text"); g.textAlign = "center"; g.font = `700 ${Math.round(r * 0.36)}px system-ui`;
  g.fillText(value == null ? "—" : value >= 100 ? Math.round(value) : value.toFixed(1), cx, cy - r * 0.12);
  g.fillStyle = css("--muted"); g.font = `${Math.round(r * 0.13)}px system-ui`;
  g.fillText(label, cx, cy + r * 0.1);
  g.font = "11px system-ui";
  [0, 10, 50, 100, 250, 500, 1000].filter((v) => v <= max).forEach((v) => {
    const f = Math.log10(1 + v) / Math.log10(1 + max), ang = Math.PI + Math.PI * f;
    g.fillText(String(v), cx + Math.cos(ang) * (r - 26), cy + Math.sin(ang) * (r - 26) + 4);
  });
}

export const COLORS = { blue: "#3b82f6", red: "#ef4444", green: "#22c55e", amber: "#f59e0b", violet: "#8b5cf6", cyan: "#06b6d4", pink: "#ec4899", slate: "#94a3b8" };
export const PALETTE = Object.values(COLORS);
