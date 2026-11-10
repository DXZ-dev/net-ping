import { api, esc, ico, ms, toast, toastErr, busy, on, modal, empty, fresh, $ } from "../lib.js";

export const title = "NAT, CGNAT y acceso remoto";
export const subtitle = "Descubre cómo te conecta tu proveedor y la mejor solución para tu caso";

const VERDICT_ICON = { ok: ["check", "green"], warn: ["alert", "amber"], info: ["info", "blue"] };

export async function render(el) {
  el.innerHTML = `
    <div class="card mb"><div class="row" style="flex-wrap:nowrap;gap:18px">
      <div class="stat"><div class="ic tint-cyan" style="width:56px;height:56px">${ico("network")}</div></div>
      <div class="grow"><h2>Análisis de conectividad</h2>
        <p class="muted small">Detecta si estás detrás de <b>CGNAT</b> (IP compartida con otros clientes del ISP), <b>doble NAT</b>, el <b>tipo de NAT</b> para juegos/consolas, si el router admite <b>UPnP</b>, tu <b>MTU</b> óptimo e <b>IPv6</b>. Después recomienda cómo mantener ping bajo y acceso remoto seguro.</p></div>
      <button class="btn primary lg" id="run">${ico("search")}Analizar</button></div></div>
    <div id="res"></div>`;
  el.querySelector("#run").onclick = (e) => busy(e.currentTarget, run).catch(() => {});

  async function run() {
    const res = fresh(el.querySelector("#res"));
    res.innerHTML = `<div class="card">${empty("Analizando: STUN, UPnP, traceroute, MTU… (≈15-25 s)", "network")}</div>`;
    const r = await api.post("/nat/analyze");
    const [vi, vt] = VERDICT_ICON[r.status] || VERDICT_ICON.info;
    const natCls = { open: "ok", cone_preserving: "ok", cone: "info", symmetric: "warn", blocked: "err" }[r.nat.type] || "";
    res.innerHTML = `
      <div class="grid g3">
        <div class="card span2"><div class="verdict"><div class="vi tint-${vt}">${ico(vi)}</div>
          <div><h2>${esc(r.label)}</h2><p>${esc(r.summary)}</p>${r.evidence.map((x) => `<p class="small muted">• ${esc(x)}</p>`).join("")}</div></div>
          <div class="grid g4 mt">
            ${kv("IP pública", r.public.ip || "—", r.public.isp || "")}
            ${kv("IP WAN del router", r.wan_ip || "—", r.upnp.available ? "vía UPnP" : "UPnP no disponible")}
            ${kv("Ubicación", [r.public.city, r.public.country].filter(Boolean).join(", ") || "—", r.public.colo ? `nodo Cloudflare ${r.public.colo}` : "")}
            ${kv("IP local", r.local_ip || "—", `router ${r.gateway || "—"}`)}
          </div></div>
        <div class="card"><header><h3>${ico("console")}Tipo de NAT</h3></header>
          <span class="badge ${{ ok: "ok", warn: "warn", err: "err" }[r.nat_class?.level] || ""}" style="font-size:14px;padding:5px 12px">${esc(r.nat_class?.classic || r.nat.label)}</span>
          <dl class="kv mt small"><dt>PlayStation</dt><dd><b>NAT ${esc(r.nat_class?.playstation || "—")}</b></dd><dt>Xbox</dt><dd><b>${esc(r.nat_class?.xbox || "—")}</b></dd>
            <dt>Nintendo Switch</dt><dd><b>${esc(r.nat_class?.nintendo || "—")}</b></dd></dl>
          <p class="small mt muted">${esc(r.nat.detail || "")}</p>
          ${r.behavior?.mapped ? `<div class="small muted mono">${esc(r.behavior.server)} → ${esc(r.behavior.mapped.join(":"))}</div>` : ""}
          ${r.behavior?.error ? `<p class="small warn">${esc(r.behavior.error)}</p>` : ""}</div>
      </div>

      <div class="grid g2 mt">
        <div class="card"><header><h3>${ico("list")}Estados del NAT</h3></header>
          <div class="table-wrap"><table><tbody>${(r.states || []).map((x) => `<tr><td><span class="dot dot-${x.status === "ok" ? "ok" : x.status === "warn" ? "warn" : x.status === "err" ? "err" : "off"}"></span> ${esc(x.name)}</td><td class="right"><b>${esc(x.value)}</b></td></tr>`).join("")}</tbody></table></div></div>
        <div class="card"><header><h3>${ico("activity")}Sesiones NAT (tabla de conexiones)</h3></header>
          ${r.sessions?.available ? `<div class="grid g3 mb">
              <div class="big-metric"><div class="v" style="font-size:22px">${(r.sessions.count ?? 0).toLocaleString()}</div><div class="l">activas</div></div>
              <div class="big-metric"><div class="v" style="font-size:22px">${(r.sessions.translated ?? 0).toLocaleString()}</div><div class="l">traducidas (NAT)</div></div>
              <div class="big-metric"><div class="v" style="font-size:22px">${r.sessions.usage_pct ?? 0} %</div><div class="l">de la tabla</div></div></div>
            ${r.sessions.max ? `<div class="progress mb"><i style="width:${Math.min(100, r.sessions.usage_pct || 0)}%"></i></div>` : ""}
            <div class="col small">${Object.entries(r.sessions.states || {}).map(([k, v]) => `<div class="row between"><span>${esc(k)}</span><b>${v.toLocaleString()}</b></div>`).join("")}</div>
            <p class="small muted mt">Tiempos de espera: TCP establecida ${r.sessions.tcp_established_timeout ?? "—"} s · UDP ${r.sessions.udp_timeout ?? "—"} s / flujo ${r.sessions.udp_stream_timeout ?? "—"} s.
              Estas sesiones son las de este equipo; reflejan toda la red cuando Bipon hace de router.</p>`
            : `<p class="muted small">El seguimiento de conexiones no está disponible en este equipo (se activa al usar el modo router o el firewall).</p>`}</div>
      </div>

      <div class="grid g3 mt">
        <div class="card"><header><h3>${ico("globe")}IPv6</h3></header>
          ${r.ipv6.available ? `<span class="badge ok">Disponible</span><p class="small mt">Latencia ${ms(r.ipv6.ms)}. Con IPv6 cada dispositivo tiene dirección pública: el CGNAT no le afecta.</p>` : `<span class="badge">No disponible</span><p class="small mt muted">Tu conexión no tiene IPv6 activo.</p>`}</div>
        <div class="card"><header><h3>${ico("activity")}MTU</h3></header>
          <div class="v" style="font-size:22px;font-weight:700">${r.mtu.mtu || "—"}</div><p class="small">${esc(r.mtu.detail || "")}</p>${r.mtu.mss ? `<p class="small muted">MSS recomendado: ${r.mtu.mss}</p>` : ""}</div>
        <div class="card"><header><h3>${ico("router")}Ruta hacia Internet</h3></header>
          <div class="hops">${r.hops.map((h, i) => `${i ? "→" : ""}<span class="hop ${h.class}" title="${esc(h.class)}">${i + 1}. ${esc(h.ip)}</span>`).join("") || `<span class="muted small">traceroute no disponible</span>`}</div>
          <p class="small muted mt">Naranja = red CGNAT (100.64.0.0/10) · Azul = privada · Verde = pública</p></div>
      </div>

      <div class="card mt"><header><h2>${ico("zap")}Soluciones recomendadas para tu red</h2></header>
        <div class="grid g2">${r.recommendations.map((x, i) => `<div class="rec">
          <div class="row"><b>${i + 1}. ${esc(x.title)}</b><span class="badge ${x.fit === "excelente" ? "ok" : x.fit === "buena" || x.fit === "definitiva" ? "info" : ""}">${esc(x.fit)}</span></div>
          <p class="small">${esc(x.why)}</p><ol class="small">${x.how.map((s) => `<li>${esc(s)}</li>`).join("")}</ol>
          ${x.action ? `<a class="btn sm mt" href="#/${x.action.split("/")[0]}">${ico("external")}Configurar</a>` : ""}</div>`).join("")}</div></div>

      <div class="card mt"><header><h2>${ico("router")}Puertos del router (UPnP)</h2>
        ${r.upnp.available ? `<button class="btn primary" id="add-map">${ico("plus")}Abrir puerto</button>` : ""}</header>
        ${r.upnp.available ? `<p class="muted small mb">Router: ${esc(r.upnp.name)}. ${r.verdict.startsWith("cgnat") ? "<b>Atención:</b> con CGNAT los puertos abiertos aquí no serán accesibles desde Internet." : ""}</p>
          <div class="table-wrap"><table><thead><tr><th>Externo</th><th>Proto</th><th>Destino</th><th>Descripción</th><th></th></tr></thead><tbody>
          ${r.upnp.mappings.map((p) => `<tr><td class="mono">${p.external_port}</td><td>${esc(p.protocol)}</td><td class="mono">${esc(p.internal_ip)}:${p.internal_port}</td><td>${esc(p.description)}</td>
            <td class="actions"><button class="icon-btn" data-del="${p.external_port}" data-proto="${esc(p.protocol)}">${ico("trash")}</button></td></tr>`).join("") || `<tr><td colspan="5" class="muted">Sin redirecciones activas</td></tr>`}</tbody></table></div>`
        : `<p class="muted">El router no respondió a UPnP-IGD. Actívalo en la configuración del router para gestionar puertos desde aquí (y que las consolas tengan NAT abierto).</p>`}</div>`;

    const add = res.querySelector("#add-map");
    if (add) add.onclick = () => modal({ title: "Abrir puerto (UPnP)", body: `<div class="form-grid">
        <label class="field">Puerto externo<input type="number" name="ep" min="1" max="65535" required></label>
        <label class="field">Protocolo<select name="pr"><option>TCP</option><option>UDP</option></select></label>
        <label class="field">IP destino<input name="ip" value="${esc(r.local_ip || "")}"></label>
        <label class="field">Puerto interno<input type="number" name="ip_port" min="1" max="65535"></label>
        <label class="field">Descripción<input name="desc" value="Bipon Net"></label></div>`,
      actions: [{ label: "Cancelar" }, { label: "Abrir", kind: "primary", onClick: async (m) => {
        await api.post("/nat/upnp", { action: "add", external_port: Number($("[name=ep]", m).value), protocol: $("[name=pr]", m).value,
          internal_ip: $("[name=ip]", m).value, internal_port: Number($("[name=ip_port]", m).value) || undefined, description: $("[name=desc]", m).value });
        toast("Puerto abierto"); run();
      } }] });
    on(res, "click", "[data-del]", async (e, b) => {
      try { await api.post("/nat/upnp", { action: "delete", external_port: Number(b.dataset.del), protocol: b.dataset.proto }); toast("Puerto cerrado"); run(); } catch (err) { toastErr(err); }
    });
  }
}

const kv = (l, v, s) => `<div><div class="muted small">${esc(l)}</div><div class="mono" style="font-weight:600">${esc(v)}</div><div class="muted small">${esc(s)}</div></div>`;
