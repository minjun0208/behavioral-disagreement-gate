/* ui.js — shared helpers. No framework. */
import { parsePy, canon, score } from "./scorer.js";

export const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;

// ---------------------------------------------------------------------------
// data
// ---------------------------------------------------------------------------
const cache = new Map();
export async function loadPy(url) {
  if (!cache.has(url)) cache.set(url, fetch(url).then(r => { if (!r.ok) throw new Error(`${url}: ${r.status}`); return r.text(); }).then(parsePy));
  return cache.get(url);
}
export const q = (sel, root = document) => root.querySelector(sel);
export const el = (tag, attrs = {}, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k === "html") e.innerHTML = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) e.setAttribute(k, v);
  }
  for (const k of kids.flat()) if (k !== null && k !== undefined) e.append(k.nodeType ? k : document.createTextNode(String(k)));
  return e;
};
export const esc = (s) => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
export const fmtInput = (fn, inp) => `${fn}(${Object.keys(inp).sort().map(k => `${k}=${canon(inp[k])}`).join(", ")})`;
export const short = (s, n = 8) => (s ?? "").slice(0, n);
export const modelShort = (m) => (m ?? "").split("/").pop();

// ---------------------------------------------------------------------------
// motion — critically damped spring (damping 1.0, response ~0.35s), interruptible:
// retargeting keeps the current position and velocity (never restarts from target).
// ---------------------------------------------------------------------------
export function createSpring(initial, onUpdate, { response = 0.35, damping = 1.0 } = {}) {
  const k = (2 * Math.PI / response) ** 2, c = 2 * damping * Math.sqrt(k);
  let x = initial, v = 0, target = initial, raf = null, last = 0;
  const step = (t) => {
    const dt = Math.min(1 / 30, (t - last) / 1000 || 1 / 60); last = t;
    const a = -k * (x - target) - c * v;
    v += a * dt; x += v * dt;
    if (Math.abs(v) < 0.001 && Math.abs(x - target) < 0.001) { x = target; v = 0; onUpdate(x); raf = null; return; }
    onUpdate(x); raf = requestAnimationFrame(step);
  };
  return {
    set(t) {
      target = t;
      if (REDUCED) { x = t; v = 0; onUpdate(x); return; }
      if (!raf) { last = performance.now(); raf = requestAnimationFrame(step); }
    },
    get value() { return x; },
  };
}

/** press feedback on pointer-down (response, not on click) */
export function pressable(btn) {
  const s = createSpring(1, (v) => { btn.style.transform = `scale(${v})`; }, { response: 0.25 });
  btn.addEventListener("pointerdown", () => s.set(0.97));
  for (const ev of ["pointerup", "pointercancel", "pointerleave"]) btn.addEventListener(ev, () => s.set(1));
}

/** two-state toggle with a spring-driven knob. onChange(bool). */
export function makeToggle(label, checked, onChange) {
  const knob = el("span", { class: "knob" });
  const root = el("button", { class: "toggle", role: "switch", "aria-checked": String(checked), type: "button" },
    el("span", { class: "track" }, knob), el("span", {}, label));
  const s = createSpring(checked ? 16 : 0, (v) => { knob.style.transform = `translateX(${v}px)`; });
  let state = checked;
  const apply = (next) => { state = next; root.setAttribute("aria-checked", String(state)); s.set(state ? 16 : 0); onChange(state); };
  root.addEventListener("pointerdown", (e) => { e.preventDefault(); apply(!state); });
  root.addEventListener("keydown", (e) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); apply(!state); } });
  root.set = apply;
  return root;
}

/** reveal a hash digit by digit (the one choreographed moment). */
export function revealHash(target, hex, { ms = 1100 } = {}) {
  if (REDUCED) { target.textContent = hex; return Promise.resolve(); }
  const n = hex.length; const t0 = performance.now();
  return new Promise((res) => {
    const tick = (t) => {
      const p = Math.min(1, (t - t0) / ms); const shown = Math.floor(p * n);
      target.innerHTML = esc(hex.slice(0, shown)) + `<span class="pending">${"·".repeat(n - shown)}</span>`;
      if (p < 1) requestAnimationFrame(tick); else { target.textContent = hex; res(); }
    };
    requestAnimationFrame(tick);
  });
}

/** open an element from a trigger's position (spatial consistency) */
export function openFrom(elm, trigger) {
  if (REDUCED) return;
  const a = trigger.getBoundingClientRect(), b = elm.getBoundingClientRect();
  elm.style.transformOrigin = `${a.left - b.left + a.width / 2}px ${a.top - b.top}px`;
  const s = createSpring(0, (v) => { elm.style.transform = `scale(${0.96 + 0.04 * v})`; elm.style.opacity = String(0.2 + 0.8 * v); }, { response: 0.32 });
  s.set(1);
}

// ---------------------------------------------------------------------------
// header
// ---------------------------------------------------------------------------
export function header(current) {
  const pages = [["index.html", "overview"], ["session.html", "sessions"], ["evidence.html", "evidence"], ["provenance.html", "provenance"]];
  const nav = el("nav", {}, ...pages.map(([href, name]) => el("a", { href, ...(name === current ? { "aria-current": "page" } : {}) }, name)));
  return el("header", { class: "top" }, el("span", { class: "brand" }, "behavioral-disagreement-gate"), nav);
}

// ---------------------------------------------------------------------------
// verdict rendering (shared by session + evidence)
// ---------------------------------------------------------------------------
const STATUS_TEXT = {
  PASS: "no counterexample found in the searched range",
  NEEDS_CLARIFICATION: "candidates disagree — ask the user",
  CODE_INCOMPLETE: "a confirmed behavior was violated",
  UNVERIFIABLE: "could not verify — not approved",
};
export function renderVerdict(v, { fn = "f", pyHash = null, showHash = true } = {}) {
  const dl = el("dl");
  const add = (k, val, cls) => { dl.append(el("dt", {}, k), el("dd", { class: cls ?? "" }, val)); };
  if (v.g3_search) {
    add("sampled domain", el("span", { class: "measured" }, `${v.g3_search.domain ?? "?"}`, ` · ${v.g3_search.n_valid_probes} probes checked`));
    add("valid probes", `${v.g3_search.n_valid_probes} · candidates ${v.g3_search.n_eff_candidates} · channel ${(v.g3_search.observation_channels ?? []).join(", ")}`);
    add("outside range", el("span", { class: "unmeasured" }, "not searched"));
  }
  if (v.g4) add("regression (G4)", `${v.g4.passed} passed · ${v.g4.failed} failed` + (v.g4.failing_candidates?.length ? ` · dropped ${v.g4.failing_candidates.join(", ")}` : ""));
  if (v.g2 && Object.keys(v.g2).length) {
    const s = Object.entries(v.g2).map(([c, m]) => `${c} k_unique=${m.k_unique}/${m.applicable}`).join("  ");
    add("mutation (G2)", el("span", { class: Object.values(v.g2).every(m => m.k_unique === 0) ? "unmeasured" : "" }, s + "  advisory"));
  }
  if (v.n_eff_candidates !== undefined) add("distinct candidates", String(v.n_eff_candidates));
  if (v.completeness_problems) add("completeness", v.completeness_problems.join(" · "));
  const wit = v.witness ? el("div", { class: "stack" },
    el("div", { class: "mono", style: "margin-top:.6rem" }, el("span", { class: "measured" }, fmtInput(fn, v.witness.input))),
    el("table", {}, el("tbody", {}, ...Object.entries(v.witness.outputs).map(([c, o]) => el("tr", {}, el("td", {}, c), el("td", {}, o)))))) : null;
  const hash = showHash ? el("div", { class: "hash fine", style: "margin-top:.8rem" },
    "decision_core ", el("span", { class: "hash" }, v.decision_core_sha256),
    pyHash ? el("span", { class: pyHash === v.decision_core_sha256 ? " measured" : " unmeasured", style: "margin-left:.6rem" }, pyHash === v.decision_core_sha256 ? "matches Python" : "differs from Python") : null) : null;
  return el("div", { class: "verdict" },
    el("div", { class: "status" }, v.status),
    el("div", { class: "reasons" }, (v.reason_codes ?? []).length ? (v.reason_codes.join(" · ") + "  —  ") : "", el("span", { class: "muted", style: "font-family:var(--serif)" }, STATUS_TEXT[v.status] ?? "")),
    dl, wit, hash);
}

export { parsePy, canon, score };
