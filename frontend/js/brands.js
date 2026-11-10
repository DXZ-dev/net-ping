// Iconos oficiales de apps y marcas (Simple Icons, CC0) con alternativa de iniciales.
import { BRAND_ICONS, BRAND_FALLBACK } from "./brand-icons.js";
import { ico, KIND_ICON, esc } from "./lib.js";

// servicio de la lista de apps → clave de icono
const SERVICE_ALIAS = { twitter: "twitter", leagueoflegends: "leagueoflegends", vpn_proxy: "vpn_proxy", public_doh: "public_doh" };

function luminance(hex) {
  const n = parseInt(hex, 16);
  const r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
  return (0.299 * r + 0.587 * g + 0.114 * b) / 255;
}

/** Icono de una app o marca. size: "" | "lg" */
export function logo(key, size = "", title = "") {
  const k = SERVICE_ALIAS[key] || key;
  const ic = BRAND_ICONS[k];
  if (ic) {
    const [hex, path] = ic;
    // marcas muy oscuras sobre fondo oscuro: se invierte (icono oscuro sobre fondo claro)
    const dark = luminance(hex) < 0.18;
    const bg = dark ? "#f4f4f5" : `#${hex}`;
    const fg = dark ? `#${hex === "000000" ? "111111" : hex}` : luminance(hex) > 0.75 ? "#111" : "#fff";
    return `<span class="brand-ic ${size}" style="background:${bg};color:${fg}" title="${esc(title)}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="${path}"/></svg></span>`;
  }
  const fb = BRAND_FALLBACK[k];
  if (fb) return `<span class="brand-ic ${size}" style="background:#${fb[0]}" title="${esc(title)}">${esc(fb[1])}</span>`;
  return null;
}

/** Icono para un dispositivo: marca si se conoce; si no, el tipo de dispositivo. */
export function brandIcon(brand, kind, size = "") {
  const l = brand && logo(brand, size, brand);
  if (l) return l;
  return `<span class="brand-ic ${size}" style="background:var(--card-2);color:var(--text)">${ico(KIND_ICON[kind] || "wifi")}</span>`;
}

/** Icono de app (servicio bloqueable) con alternativa de inicial coloreada. */
export function appIcon(id, name, size = "") {
  return logo(id, size, name) || `<span class="brand-ic ${size}" style="background:hsl(${[...id].reduce((a, c) => a + c.charCodeAt(0), 0) % 360} 55% 45%)">${esc((name || id)[0])}</span>`;
}
