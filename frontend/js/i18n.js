// Idiomas: español (texto fuente) e inglés.
// Las páginas escriben en español; con el inglés activo, un traductor del DOM
// sustituye textos y atributos usando el diccionario (frases exactas y patrones).
import { EN, EN_PATTERNS } from "./i18n-en.js";

let lang = "es";
try { lang = localStorage.getItem("bipon-lang") || (navigator.language || "es").slice(0, 2); } catch {}
if (!["es", "en"].includes(lang)) lang = "en";

export const getLang = () => lang;
export function setLang(l) {
  lang = l === "en" ? "en" : "es";
  try { localStorage.setItem("bipon-lang", lang); } catch {}
  document.documentElement.lang = lang;
}

/** Traducción explícita (para textos generados en JS: toasts, confirmaciones…). */
export function t(s, vars) {
  let out = lang === "en" ? translate(s) : s;
  if (vars) for (const [k, v] of Object.entries(vars)) out = out.replaceAll(`{${k}}`, v);
  return out;
}

function translate(s) {
  if (!s) return s;
  const key = s.trim();
  if (!key) return s;
  const hit = EN[key];
  if (hit !== undefined) return s.replace(key, hit);
  for (const [re, rep] of EN_PATTERNS) {
    if (re.test(key)) return s.replace(key, key.replace(re, rep));
  }
  return s;
}

const ATTRS = ["placeholder", "title", "aria-label", "data-tip"];
const SKIP = new Set(["SCRIPT", "STYLE", "CODE", "PRE", "TEXTAREA"]);

function translateNode(node) {
  if (node.nodeType === 3) {
    const p = node.parentElement;
    if (!p || SKIP.has(p.tagName) || p.closest("[data-notr], .mono, .terminal")) return;
    const v = node.nodeValue;
    if (v && /[A-Za-zÁÉÍÓÚáéíóúñÑ]/.test(v)) {
      const tr = translate(v);
      if (tr !== v) node.nodeValue = tr;
    }
    return;
  }
  if (node.nodeType !== 1 || node.hasAttribute("data-notr")) return;
  for (const a of ATTRS) {
    const v = node.getAttribute(a);
    if (v) {
      const tr = translate(v);
      if (tr !== v) node.setAttribute(a, tr);
    }
  }
  if (SKIP.has(node.tagName)) return;
  if (node.tagName === "OPTION" || node.tagName === "BUTTON" || node.childNodes.length) {
    for (const c of node.childNodes) translateNode(c);
  }
}

let observer = null;
export function startTranslator(root = document.body) {
  observer?.disconnect();
  if (lang !== "en") return;
  translateNode(root);
  observer = new MutationObserver((muts) => {
    for (const m of muts) {
      if (m.type === "characterData") translateNode(m.target);
      else if (m.type === "attributes") translateNode(m.target);
      else m.addedNodes.forEach(translateNode);
    }
  });
  observer.observe(root, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS });
}

/** Nombre de país en el idioma activo (códigos ISO; «OTHER» u otros se traducen por diccionario). */
export function countryName(code, fallback) {
  if (lang === "en" && /^[A-Z]{2}$/.test(code || "")) {
    try { return new Intl.DisplayNames(["en"], { type: "region" }).of(code) || fallback; } catch {}
  }
  return lang === "en" ? translate(fallback) : fallback;
}

export function toLocale() { return lang === "en" ? "en-US" : "es"; }
