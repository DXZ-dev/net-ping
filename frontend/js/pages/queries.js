import { api, esc, ico, time, toast, toastErr, on, empty } from "../lib.js";

export const title = "Registro de consultas";
export const subtitle = "Cada consulta DNS de tu red, en tiempo real";

const STATUS_BADGE = { 0: "info", 1: "ok", 10: "ok", 2: "err", 3: "err", 4: "warn", 5: "violet", 6: "", 7: "ok", 8: "err", 9: "err" };

export async function render(el) {
  let pre = "";
  try { pre = sessionStorage.getItem("np-q-client") || ""; sessionStorage.removeItem("np-q-client"); } catch {}
  const f = { status: "", client: pre, domain: "" };
  let live = true;
  let rows = [];
  el.innerHTML = `
    <div class="card">
      <div class="row mb">
        <input id="q-domain" placeholder="Buscar dominio…" style="max-width:280px">
        <select id="q-status" style="max-width:200px">
          <option value="">Todas</option><option value="blocked">Bloqueadas</option><option value="allowed">Permitidas</option><option value="cached">Desde caché</option>
          <option value="4">Apps bloqueadas</option><option value="5">Control parental</option><option value="7">Búsqueda segura</option><option value="8">Errores</option>
        </select>
        <select id="q-client" style="max-width:240px"><option value="">Todos los dispositivos</option></select>
        <span class="grow"></span>
        <label class="row small">${`<label class="switch"><input type="checkbox" id="q-live" checked><span></span></label>`} En vivo</label>
        <button class="btn sm" id="q-refresh">${ico("refresh")}</button>
      </div>
      <div class="table-wrap"><table>
        <thead><tr><th>Hora</th><th>Dispositivo</th><th>Dominio</th><th>Tipo</th><th>Estado</th><th class="right">Tiempo</th><th></th></tr></thead>
        <tbody id="q-body"></tbody></table></div>
      <div class="center mt"><button class="btn sm" id="q-more">Cargar más</button></div>
    </div>`;
  const body = el.querySelector("#q-body");

  const devs = await api.get("/devices").catch(() => ({ devices: [] }));
  el.querySelector("#q-client").innerHTML += devs.devices.filter((d) => d.ip).map((d) =>
    `<option value="${esc(d.ip)}" ${d.ip === pre ? "selected" : ""}>${esc(d.name || d.hostname || d.ip)} (${esc(d.ip)})</option>`).join("");

  const qs = (extra = "") => `/queries?limit=150&status=${encodeURIComponent(f.status)}&client=${encodeURIComponent(f.client)}&domain=${encodeURIComponent(f.domain)}${extra}`;

  function rowHtml(r) {
    return `<tr>
      <td class="nowrap muted">${time(r.ts)}</td>
      <td class="truncate" style="max-width:180px" title="${esc(r.client)}">${esc(r.client_name || r.client)}</td>
      <td style="word-break:break-all"><b>${esc(r.domain)}</b>${r.reason ? `<div class="muted small">${esc(r.reason)}</div>` : ""}</td>
      <td class="muted">${esc(r.qtype)}</td>
      <td><span class="badge ${STATUS_BADGE[r.status] ?? ""}">${esc(r.status_name)}</span></td>
      <td class="right nowrap">${r.rtt < 1 ? `<span class="ok">${r.rtt.toFixed(2)} ms</span>` : `${r.rtt.toFixed(1)} ms`}<div class="muted small truncate" style="max-width:150px">${esc(r.upstream || "")}</div></td>
      <td class="actions">${r.blocked
        ? `<button class="btn sm success" data-allow="${esc(r.domain)}" title="Permitir siempre">${ico("check")}Permitir</button>`
        : `<button class="btn sm danger" data-deny="${esc(r.domain)}" title="Bloquear siempre">${ico("x")}Bloquear</button>`}</td></tr>`;
  }

  async function load(append = false) {
    const extra = append && rows.length ? `&offset=${rows.length}` : "";
    const data = await api.get(qs(extra));
    rows = append ? rows.concat(data) : data;
    body.innerHTML = rows.length ? rows.map(rowHtml).join("") : `<tr><td colspan="7">${empty("Sin consultas que coincidan")}</td></tr>`;
  }

  async function poll() {
    if (!live || f.domain || !rows.length) return;
    const data = await api.get(qs(`&since=${rows[0].ts}`)).catch(() => []);
    if (data.length) {
      rows = data.concat(rows).slice(0, 500);
      body.insertAdjacentHTML("afterbegin", data.map(rowHtml).join(""));
      while (body.children.length > 500) body.lastElementChild.remove();
    }
  }

  let t;
  el.querySelector("#q-domain").oninput = (e) => { clearTimeout(t); t = setTimeout(() => { f.domain = e.target.value.trim(); load(); }, 300); };
  el.querySelector("#q-status").onchange = (e) => { f.status = e.target.value; load(); };
  el.querySelector("#q-client").onchange = (e) => { f.client = e.target.value; load(); };
  el.querySelector("#q-live").onchange = (e) => { live = e.target.checked; };
  el.querySelector("#q-refresh").onclick = () => load();
  el.querySelector("#q-more").onclick = () => load(true);
  on(el, "click", "[data-allow]", async (e, b) => {
    try { await api.post("/rules", { kind: "allow", value: b.dataset.allow }); toast(`${b.dataset.allow} permitido`); } catch (err) { toastErr(err); }
  });
  on(el, "click", "[data-deny]", async (e, b) => {
    try { await api.post("/rules", { kind: "deny", value: b.dataset.deny }); toast(`${b.dataset.deny} bloqueado`); } catch (err) { toastErr(err); }
  });

  await load();
  const timer = setInterval(poll, 2000);
  return () => clearInterval(timer);
}
