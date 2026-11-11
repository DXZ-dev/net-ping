import { api, esc, ico, bytes, duration, toast, busy, empty, on, confirmDialog, $$ } from "../lib.js";
import { t } from "../i18n.js";

export const title = "Infraestructura";
export const subtitle = "Servidores, máquinas virtuales, procesos y puertos en uso";

const pct = (a, b) => (b ? Math.round((a / b) * 100) : 0);
const bar = (p) => `<div class="progress" style="height:6px"><i style="width:${Math.min(100, p)}%;${p > 85 ? "background:var(--err)" : p > 65 ? "background:var(--warn)" : ""}"></i></div>`;

export async function render(el, app) {
  let tab = sessionStorage.getItem("np-infra-tab") || "proxmox";
  el.innerHTML = `<div class="tabs mb"><button data-tab="proxmox">${ico("server")}Proxmox</button><button data-tab="ports">${ico("network")}${t("Puertos y conexiones")}</button>
    <button data-tab="procs">${ico("activity")}${t("Procesos de Bipon")}</button></div><div id="pane"></div>`;
  let pane = el.querySelector("#pane");
  let timer;
  on(el, "click", "[data-tab]", (e, b) => { tab = b.dataset.tab; try { sessionStorage.setItem("np-infra-tab", tab); } catch {} show(); });
  async function show() {
    clearInterval(timer);
    $$("[data-tab]", el).forEach((x) => x.classList.toggle("active", x.dataset.tab === tab));
    const n = pane.cloneNode(false); pane.replaceWith(n); pane = n;
    pane.innerHTML = `<div class="skeleton" style="height:240px"></div>`;
    try { await V[tab](); } catch (e) { pane.innerHTML = `<div class="alert err">${ico("alert")}<div>${esc(e.message)}</div></div>`; }
  }

  const V = {
    async proxmox() {
      const r = await api.get("/infra");
      if (!r.proxmox) {
        pane.innerHTML = `<div class="card">${r.error ? `<div class="alert err mb">${ico("alert")}<div class="small">${esc(r.error)}</div></div>` : ""}${empty(t("Conecta Proxmox en Integraciones para ver tus servidores, VMs y contenedores."), "server")}
          <div class="center"><a class="btn primary" href="#/integrations">${ico("link")}${t("Conectar Proxmox")}</a></div></div>`;
        return;
      }
      const nodes = r.proxmox.filter((x) => x.type === "node");
      const guests = r.proxmox.filter((x) => (x.type === "qemu" || x.type === "lxc") && !x.template);
      const storage = r.proxmox.filter((x) => x.type === "storage");
      pane.innerHTML = `<div class="grid g-auto mb">${nodes.map((n) => `<div class="card"><header><h3>${ico("server")}${esc(n.node)}</h3><span class="badge ${n.status === "online" ? "ok" : "err"}">${esc(n.status)}</span></header>
          <div class="small">CPU ${Math.round((n.cpu || 0) * 100)} % · ${n.maxcpu} vCPU</div>${bar((n.cpu || 0) * 100)}
          <div class="small mt">RAM ${bytes(n.mem)} / ${bytes(n.maxmem)}</div>${bar(pct(n.mem, n.maxmem))}
          <div class="small mt">${t("Disco")} ${bytes(n.disk)} / ${bytes(n.maxdisk)}</div>${bar(pct(n.disk, n.maxdisk))}
          <div class="small muted mt">${t("Encendido")} ${duration(n.uptime)}</div></div>`).join("")}</div>
        <div class="card mb"><header><h2>${ico("devices")}${t("Máquinas virtuales y contenedores")} (${guests.length})</h2></header><div class="table-wrap"><table><thead><tr><th>${t("Nombre")}</th><th>ID</th><th>${t("Nodo")}</th><th>CPU</th><th>RAM</th><th>${t("Encendido")}</th><th></th></tr></thead><tbody>
        ${guests.sort((a, b) => (b.status === "running") - (a.status === "running") || a.vmid - b.vmid).map((g) => `<tr><td><b>${esc(g.name || "")}</b> <span class="badge">${g.type === "lxc" ? "LXC" : "VM"}</span></td><td>${g.vmid}</td><td>${esc(g.node)}</td>
          <td style="min-width:110px">${g.status === "running" ? `${Math.round((g.cpu || 0) * 100)} % ${bar((g.cpu || 0) * 100)}` : "—"}</td><td style="min-width:130px">${g.status === "running" ? `${bytes(g.mem)} / ${bytes(g.maxmem)} ${bar(pct(g.mem, g.maxmem))}` : bytes(g.maxmem)}</td>
          <td class="small">${g.status === "running" ? duration(g.uptime) : `<span class="badge">${esc(g.status)}</span>`}</td>
          <td class="actions">${g.status === "running" ? `<button class="btn sm" data-act="reboot" data-g="${esc(g.node)}|${g.type}|${g.vmid}">${ico("refresh")}</button><button class="btn sm" data-act="shutdown" data-g="${esc(g.node)}|${g.type}|${g.vmid}">${ico("power")}</button>`
            : `<button class="btn sm success" data-act="start" data-g="${esc(g.node)}|${g.type}|${g.vmid}">${ico("play")}${t("Arrancar")}</button>`}</td></tr>`).join("")}</tbody></table></div></div>
        ${storage.length ? `<div class="card"><header><h3>${ico("server")}${t("Almacenamiento")}</h3></header>${storage.map((s) => `<div class="small">${esc(s.storage)} @ ${esc(s.node)} · ${bytes(s.disk)} / ${bytes(s.maxdisk)}</div>${bar(pct(s.disk, s.maxdisk))}`).join("<div class='mt'></div>")}</div>` : ""}`;
      on(pane, "click", "[data-act]", async (e, b) => {
        const [node, kind, vmid] = b.dataset.g.split("|");
        const label = { start: t("arrancar"), shutdown: t("apagar"), reboot: t("reiniciar") }[b.dataset.act];
        if (b.dataset.act !== "start" && !(await confirmDialog(t("Confirmar"), `${t("¿Seguro que quieres")} ${label} ${vmid}?`))) return;
        busy(b, () => api.post(`/infra/proxmox/${node}/${kind}/${vmid}/${b.dataset.act}`)).then(() => { toast(t("Orden enviada a Proxmox")); setTimeout(show, 3000); }).catch(() => {});
      });
      timer = setInterval(show, 30000);
    },
    async ports() {
      const r = await api.get("/ports");
      pane.innerHTML = `${r.notes.map((n) => `<div class="alert info small mb">${ico("info")}<div>${esc(t(n))}</div></div>`).join("")}
        <div class="card mb"><header><h2>${ico("network")}${t("Puertos en escucha en este servidor")} (${r.listening.length})</h2><input id="pf" placeholder="${t("Filtrar…")}" style="max-width:200px"></header>
        <div class="table-wrap"><table><thead><tr><th>${t("Protocolo")}</th><th>${t("Puerto")}</th><th>${t("Servicio")}</th><th>${t("Aplicación (proceso)")}</th><th>${t("Dirección")}</th><th>${t("Expuesto")}</th></tr></thead><tbody id="lt">
        ${r.listening.map((p) => `<tr data-row="${esc(`${p.port} ${p.service} ${p.process}`.toLowerCase())}"><td><span class="badge ${p.proto === "TCP" ? "info" : "violet"}">${p.proto}</span></td><td class="mono"><b>${p.port}</b></td><td>${esc(t(p.service) || "—")}</td>
          <td>${esc(p.process)}${p.pids.length ? ` <span class="muted small">PID ${p.pids.join(", ")}</span>` : ""}</td><td class="mono small">${esc(p.address)}</td><td>${p.exposed ? `<span class="badge warn">${t("red")}</span>` : `<span class="badge ok">${t("sólo local")}</span>`}</td></tr>`).join("")}</tbody></table></div></div>
        <div class="card mb"><header><h3>${ico("activity")}${t("Conexiones establecidas")} (${r.connections.length})</h3></header>${r.connections.length ? `<div class="table-wrap"><table><thead><tr><th>${t("Local")}</th><th>${t("Remoto")}</th><th>${t("Servicio")}</th><th>${t("Aplicación")}</th></tr></thead><tbody>
          ${r.connections.slice(0, 150).map((c) => `<tr><td class="mono">${c.local_port}</td><td class="mono">${esc(c.remote)}:${c.remote_port} ${c.remote_name ? `<span class="small muted">${esc(c.remote_name)}</span>` : ""}</td><td>${esc(t(c.service) || "—")}</td><td>${esc(c.process)}</td></tr>`).join("")}</tbody></table></div>` : `<p class="muted small">—</p>`}</div>
        <div class="card"><header><h3>${ico("devices")}${t("Puertos que usan los dispositivos de la red")}</h3></header>${r.flows.length ? `<div class="table-wrap"><table><thead><tr><th>${t("Dispositivo")}</th><th>${t("Protocolo")}</th><th>${t("Puerto destino")}</th><th>${t("Servicio")}</th><th class="right">${t("Conexiones")}</th><th>${t("Destinos")}</th></tr></thead><tbody>
          ${r.flows.map((f) => `<tr><td><b>${esc(f.device)}</b><div class="small muted mono">${esc(f.ip)}</div></td><td>${f.proto}</td><td class="mono">${f.port}</td><td>${esc(t(f.service) || "—")}</td><td class="right">${f.connections}</td><td class="mono small">${esc(f.destinations.join(", "))}</td></tr>`).join("")}</tbody></table></div>`
          : `<p class="muted small">${t("Para ver los puertos abiertos de un equipo concreto, abre su ficha en Dispositivos → Puertos.")}</p>`}</div>`;
      pane.querySelector("#pf").oninput = (e) => { const q = e.target.value.toLowerCase(); $$("#lt tr", pane).forEach((tr) => { tr.style.display = tr.dataset.row.includes(q) ? "" : "none"; }); };
    },
    async procs() {
      const r = await api.get("/infra");
      const b = r.bipon;
      pane.innerHTML = `<div class="grid g3 mb"><div class="card stat"><div class="ic tint-blue">${ico("server")}</div><div><div class="v">${bytes(b.rss)}</div><div class="l">${t("Memoria de Bipon")} · PID ${b.pid}</div></div></div>
        <div class="card stat"><div class="ic tint-green">${ico("activity")}</div><div><div class="v">${b.tasks.filter((x) => x.running).length}/${b.tasks.length}</div><div class="l">${t("Servicios internos activos")}</div></div></div>
        <div class="card stat"><div class="ic tint-violet">${ico("list")}</div><div><div class="v">${b.children.length}</div><div class="l">${t("Procesos auxiliares")}</div></div></div></div>
        <div class="grid g2"><div class="card"><header><h3>${ico("activity")}${t("Servicios internos")}</h3></header>${b.tasks.map((x) => `<div class="row between small" style="padding:4px 0"><span>${esc(t(x.name))}</span>
          ${x.running ? `<span class="badge ok">${t("en marcha")}</span>` : `<span class="badge err" title="${esc(x.error || "")}">${t("detenido")}</span>`}</div>`).join("")}</div>
        <div class="card"><header><h3>${ico("list")}${t("Procesos auxiliares")}</h3><a class="btn sm ghost" href="#/unbound">Unbound</a></header>${b.children.length ? `<div class="table-wrap"><table><tbody>${b.children.map((p) => `<tr><td class="mono">${p.pid}</td><td class="mono small" style="word-break:break-all">${esc(p.cmd)}</td><td class="right">${bytes(p.rss)}</td></tr>`).join("")}</tbody></table></div>` : `<p class="muted small">—</p>`}</div></div>`;
    },
  };
  show();
  return () => clearInterval(timer);
}
