/*
 * scorer.js — Behavioral Disagreement Gate / Scorer, JavaScript port  (v0.2.1-js)
 *
 * scorer.py 와 같은 trace 에 대해 같은 verdict 를 내고, 특히 decision_core_sha256 가
 * Python 과 바이트 단위로 일치해야 한다. 그러려면 Python 의 직렬화 규칙을 그대로 재현해야 한다:
 *   - json.dumps(sort_keys=True, separators=(",",":"), ensure_ascii=False)
 *   - float repr  (2.0 → "2.0", 1e-9 → "1e-09", 1e16 → "1e+16", -0.0 → "-0.0")
 *   - int / float 구분 유지 → JSON 파싱을 직접 한다 (JSON.parse 는 2.0 을 2 로 만든다)
 *   - round() = ties-to-even
 *   - repr(str) (witness 의 예외 표시, completeness 메시지 안의 리스트)
 *
 * 순수 함수. 시간·난수·네트워크 없음. 브라우저와 Node 모두에서 동작 (ES module).
 */

export const SCORER_VERSION = "0.2.1";           // Python scorer 와 동일해야 함 (핵심 로직 버전)
export const SCORER_JS_VERSION = "0.1.0";

const FORBIDDEN_OBS_FIELDS = new Set(["disagree", "mismatch", "pass", "fail", "verdict", "normalized_output", "is_correct"]);
const DEFAULT_CFG = {
  gates: { G2: true, G3: true, G4: true },
  normalizer: { float_abs_tol: 1e-9, strip_whitespace: true, json_key_order_insensitive: true, exception_compare: "type" },
  g2_role: "advisory",
  min_candidates: 2,
};
const DECISION_CORE_KEYS = ["status", "reason_codes", "witness", "g2", "g3_search", "g4",
                            "n_eff_candidates", "completeness_problems", "nondeterministic_pairs"];

// ---------------------------------------------------------------------------
// Python 호환 값 표현
// ---------------------------------------------------------------------------
export class PyFloat { constructor(v) { this.v = v; } }

/** Python repr(float) — 최단 왕복 자릿수 + Python 의 고정/지수 전환 규칙 */
export function pyFloatRepr(v) {
  if (Number.isNaN(v)) return "NaN";                     // json.dumps 표기
  if (v === Infinity) return "Infinity";
  if (v === -Infinity) return "-Infinity";
  if (v === 0) return Object.is(v, -0) ? "-0.0" : "0.0";
  const neg = v < 0; const a = Math.abs(v);
  const [mant, e] = a.toExponential().split("e");        // 최단 왕복 자릿수
  const digits = mant.replace(".", "");
  const exp = parseInt(e, 10);
  const decpt = exp + 1;
  let out;
  if (decpt <= -4 || decpt > 16) {
    const rest = digits.slice(1);
    const ex = Math.abs(exp);
    out = digits[0] + (rest ? "." + rest : "") + "e" + (exp < 0 ? "-" : "+") + (ex < 10 ? "0" + ex : String(ex));
  } else if (decpt <= 0) {
    out = "0." + "0".repeat(-decpt) + digits;
  } else if (decpt >= digits.length) {
    out = digits + "0".repeat(decpt - digits.length) + ".0";
  } else {
    out = digits.slice(0, decpt) + "." + digits.slice(decpt);
  }
  return neg ? "-" + out : out;
}

/** Python json.dumps 의 문자열 이스케이프 (ensure_ascii=False) */
function pyJsonStr(s) {
  let o = '"';
  for (const ch of s) {
    const c = ch.codePointAt(0);
    if (ch === '"') o += '\\"';
    else if (ch === "\\") o += "\\\\";
    else if (ch === "\n") o += "\\n";
    else if (ch === "\r") o += "\\r";
    else if (ch === "\t") o += "\\t";
    else if (ch === "\b") o += "\\b";
    else if (ch === "\f") o += "\\f";
    else if (c < 0x20) o += "\\u" + c.toString(16).padStart(4, "0");
    else o += ch;
  }
  return o + '"';
}

/** Python json.dumps(obj, sort_keys=True, separators=(",",":"), ensure_ascii=False) */
export function canon(v) {
  if (v === null || v === undefined) return "null";
  if (v === true) return "true";
  if (v === false) return "false";
  if (v instanceof PyFloat) return pyFloatRepr(v.v);
  if (typeof v === "number") return Number.isInteger(v) && !Object.is(v, -0) ? String(v) : pyFloatRepr(v);
  if (typeof v === "bigint") return v.toString();
  if (typeof v === "string") return pyJsonStr(v);
  if (Array.isArray(v)) return "[" + v.map(canon).join(",") + "]";
  const keys = Object.keys(v).sort();
  return "{" + keys.map(k => pyJsonStr(k) + ":" + canon(v[k])).join(",") + "}";
}

function pyIsPrintable(c) {
  if (c < 0x20 || (c >= 0x7f && c <= 0xa0) || c === 0xad) return false;
  if (c === 0x2028 || c === 0x2029 || c === 0xfeff) return false;
  if ((c >= 0x200b && c <= 0x200f) || (c >= 0x2060 && c <= 0x2064)) return false;
  if (c >= 0xd800 && c <= 0xdfff) return false;
  return true;
}

/** Python repr(str) */
export function pyStrRepr(s) {
  const hasSq = s.includes("'"), hasDq = s.includes('"');
  const q = (hasSq && !hasDq) ? '"' : "'";
  let o = q;
  for (const ch of s) {
    const c = ch.codePointAt(0);
    if (ch === q || ch === "\\") o += "\\" + ch;
    else if (ch === "\n") o += "\\n";
    else if (ch === "\r") o += "\\r";
    else if (ch === "\t") o += "\\t";
    else if (!pyIsPrintable(c)) {
      o += c < 0x100 ? "\\x" + c.toString(16).padStart(2, "0")
         : c < 0x10000 ? "\\u" + c.toString(16).padStart(4, "0")
         : "\\U" + c.toString(16).padStart(8, "0");
    } else o += ch;
  }
  return o + q;
}
const pyListRepr = (xs) => "[" + xs.map(pyStrRepr).join(", ") + "]";

/** Python round(x) — ties to even */
export function pyRound(x) {
  if (!Number.isFinite(x)) return x;
  const f = Math.floor(x), d = x - f;
  if (d < 0.5) return f;
  if (d > 0.5) return f + 1;
  return (f % 2 === 0) ? f : f + 1;
}

// ---------------------------------------------------------------------------
// JSON 파서 — int/float 구분 보존, NaN/Infinity 허용 (Python json.loads 와 동일)
// ---------------------------------------------------------------------------
export function parsePy(text) {
  let i = 0;
  const ws = () => { while (i < text.length && " \t\n\r".includes(text[i])) i++; };
  const err = (m) => { throw new SyntaxError(`parsePy: ${m} at ${i}`); };
  function val() {
    ws();
    const c = text[i];
    if (c === "{") {
      i++; const o = {}; ws();
      if (text[i] === "}") { i++; return o; }
      for (;;) {
        ws(); if (text[i] !== '"') err("key"); const k = str(); ws();
        if (text[i] !== ":") err(":"); i++; o[k] = val(); ws();
        if (text[i] === ",") { i++; continue; }
        if (text[i] === "}") { i++; return o; }
        err("object");
      }
    }
    if (c === "[") {
      i++; const a = []; ws();
      if (text[i] === "]") { i++; return a; }
      for (;;) {
        a.push(val()); ws();
        if (text[i] === ",") { i++; continue; }
        if (text[i] === "]") { i++; return a; }
        err("array");
      }
    }
    if (c === '"') return str();
    if (text.startsWith("true", i)) { i += 4; return true; }
    if (text.startsWith("false", i)) { i += 5; return false; }
    if (text.startsWith("null", i)) { i += 4; return null; }
    if (text.startsWith("NaN", i)) { i += 3; return new PyFloat(NaN); }
    if (text.startsWith("Infinity", i)) { i += 8; return new PyFloat(Infinity); }
    if (text.startsWith("-Infinity", i)) { i += 9; return new PyFloat(-Infinity); }
    const m = /^-?(0|[1-9]\d*)(\.\d+)?([eE][+-]?\d+)?/.exec(text.slice(i, i + 64));
    if (!m) err("value");
    i += m[0].length;
    if (m[2] !== undefined || m[3] !== undefined) return new PyFloat(Number(m[0]));
    const n = Number(m[0]);
    return Number.isSafeInteger(n) ? n : BigInt(m[0]);
  }
  function str() {
    i++; let o = "";
    for (;;) {
      const c = text[i++];
      if (c === undefined) err("unterminated string");
      if (c === '"') return o;
      if (c === "\\") {
        const e = text[i++];
        if (e === "u") { o += String.fromCharCode(parseInt(text.slice(i, i + 4), 16)); i += 4; }
        else o += ({ '"': '"', "\\": "\\", "/": "/", b: "\b", f: "\f", n: "\n", r: "\r", t: "\t" })[e] ?? err("escape");
      } else o += c;
    }
  }
  const v = val(); ws();
  if (i !== text.length) err("trailing");
  return v;
}

// ---------------------------------------------------------------------------
// SHA-256 (동기, 순수 JS) — 브라우저 crypto.subtle 은 비동기라 score() 를 동기로 유지하려고 내장
// ---------------------------------------------------------------------------
const K = [0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2];
export function sha256Hex(str) {
  const bytes = new TextEncoder().encode(str);
  const l = bytes.length, bitLen = l * 8;
  const padded = new Uint8Array(((l + 9 + 63) >> 6) << 6);
  padded.set(bytes); padded[l] = 0x80;
  const dv = new DataView(padded.buffer);
  dv.setUint32(padded.length - 4, bitLen >>> 0); dv.setUint32(padded.length - 8, Math.floor(bitLen / 2 ** 32));
  let h = [0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19];
  const w = new Uint32Array(64);
  const rotr = (x, n) => (x >>> n) | (x << (32 - n));
  for (let off = 0; off < padded.length; off += 64) {
    for (let t = 0; t < 16; t++) w[t] = dv.getUint32(off + t * 4);
    for (let t = 16; t < 64; t++) {
      const s0 = rotr(w[t-15], 7) ^ rotr(w[t-15], 18) ^ (w[t-15] >>> 3);
      const s1 = rotr(w[t-2], 17) ^ rotr(w[t-2], 19) ^ (w[t-2] >>> 10);
      w[t] = (w[t-16] + s0 + w[t-7] + s1) >>> 0;
    }
    let [a, b, c, d, e, f, g, hh] = h;
    for (let t = 0; t < 64; t++) {
      const S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
      const ch = (e & f) ^ (~e & g);
      const t1 = (hh + S1 + ch + K[t] + w[t]) >>> 0;
      const S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
      const maj = (a & b) ^ (a & c) ^ (b & c);
      const t2 = (S0 + maj) >>> 0;
      hh = g; g = f; f = e; e = (d + t1) >>> 0; d = c; c = b; b = a; a = (t1 + t2) >>> 0;
    }
    h = h.map((x, k) => (x + [a, b, c, d, e, f, g, hh][k]) >>> 0);
  }
  return h.map(x => x.toString(16).padStart(8, "0")).join("");
}

// ---------------------------------------------------------------------------
// 정규화 / 비교 키  (scorer.py 의 normalize / obs_key)
// ---------------------------------------------------------------------------
const FLOAT_RE = /^[+-]?(?:(?:\d(?:_?\d)*)(?:\.(?:\d(?:_?\d)*)?)?|\.\d(?:_?\d)*)(?:[eE][+-]?\d(?:_?\d)*)?$/;
const SPECIAL_RE = /^[+-]?(?:inf|infinity|nan)$/i;

/** Python float(s) — 성공 시 number, 실패 시 null */
function pyFloat(s) {
  const t = s.trim();
  if (SPECIAL_RE.test(t)) {
    const neg = t[0] === "-"; const body = t.replace(/^[+-]/, "").toLowerCase();
    if (body === "nan") return NaN;
    return neg ? -Infinity : Infinity;
  }
  if (!FLOAT_RE.test(t) || !/\d/.test(t)) return null;
  return Number(t.replace(/_/g, ""));
}

export function normalize(raw, ncfg) {
  const s = (ncfg.strip_whitespace ?? true) ? raw.trim() : raw;
  const v = pyFloat(s);
  if (v !== null) {
    if (Number.isNaN(v)) return ["num_nan", "nan"];
    if (!Number.isFinite(v)) return ["num", v];
    const tol = ncfg.float_abs_tol ?? 0.0;
    if (tol > 0) return ["num", pyRound(v / tol) * tol];
    return ["num", v];
  }
  try {
    const obj = parsePy(s);
    if (ncfg.json_key_order_insensitive ?? true) return ["json", canon(obj)];
    return ["json", s];
  } catch (e) { /* not json */ }
  return ["str", s];
}

/** 비교용 키 문자열 (Python 튜플 동등성과 같은 동치류) */
function keyStr(k) {
  if (k[0] === "num") return "num:" + String(k[1]);        // String(-0) === "0" → 0.0 == -0.0 와 일치
  return k[0] + ":" + canon(k.slice(1));
}

export function obsKey(o, ncfg, schemaVersion) {
  if (o.exit_code !== 0) return ["exit", o.exit_code];
  if (schemaVersion >= 2 && o.exception_type !== null && o.exception_type !== undefined) {
    if ((ncfg.exception_compare ?? "type") === "type_message") return ["exc", o.exception_type, o.exception_message_raw ?? null];
    return ["exc", o.exception_type];
  }
  return normalize(o.return_value_raw, ncfg);
}

function checkCompleteness(byKind) {
  const plans = byKind.job_plan ?? [];
  if (plans.length !== 1) return [`job_plan must appear exactly once, got ${plans.length}`];
  const plan = plans[0];
  const problems = [];
  const seen = [];
  const expectedId = {
    observation: (r) => `obs:${r.probe_id}:${r.candidate_id}:${r.repeat_idx}`,
    mutant_test_result: (r) => `mut:${r.mutant_id}:${r.suite}`,
    regression_result: (r) => `reg:${r.candidate_id}:${r.test_id}`,
  };
  for (const [kind, nExp] of Object.entries(plan.planned_counts)) {
    const recs = byKind[kind] ?? [];
    const ids = recs.map(r => r.job_id ?? null);
    if (ids.some(i => i === null)) problems.push(`${kind}: record without job_id`);
    const bad = recs.filter(r => r.job_id && expectedId[kind] && expectedId[kind](r) !== r.job_id).map(r => r.job_id);
    if (bad.length) problems.push(`${kind}: job_id inconsistent with record content (${bad.length}): ${pyListRepr(bad.slice(0, 2))}`);
    if (recs.length !== nExp) problems.push(`${kind}: planned ${nExp}, actual ${recs.length}`);
    if (new Set(ids).size !== ids.length) problems.push(`${kind}: duplicate job_id`);
    for (const i of ids) if (i !== null) seen.push(i);
  }
  if (seen.length !== plan.n_jobs) problems.push(`total jobs planned ${plan.n_jobs}, actual ${seen.length}`);
  const got = sha256Hex([...seen].sort().join("\n"));
  if (got !== plan.job_manifest_sha256) problems.push("job manifest hash mismatch (missing/extra/swapped job ids)");
  return problems;
}

function merge(base, over) {
  const out = JSON.parse(JSON.stringify(base));
  for (const [k, v] of Object.entries(over ?? {})) {
    if (v && typeof v === "object" && !Array.isArray(v) && out[k] && typeof out[k] === "object") Object.assign(out[k], v);
    else out[k] = v;
  }
  return out;
}

function g2Metrics(byKind) {
  const per = {};
  for (const m of byKind.mutant ?? []) {
    if (m.in_changed_region) (per[m.candidate_id] ??= {})[m.mutant_id] = { base: null, agent: null };
  }
  for (const r of byKind.mutant_test_result ?? []) {
    const d = per[r.candidate_id]?.[r.mutant_id];
    if (d) d[r.suite] = r.outcome;
  }
  const out = {};
  for (const [cid, muts] of Object.entries(per)) {
    const vals = Object.values(muts);
    const kBase = vals.filter(d => d.base === "failed").length;
    const kFull = vals.filter(d => d.base === "failed" || d.agent === "failed").length;
    const kAgent = vals.filter(d => d.agent === "failed").length;
    out[cid] = { applicable: vals.length, k_base: kBase, k_agent: kAgent, k_unique: kFull - kBase, role: "advisory" };
  }
  return out;
}

function verdict(runId, cfg, status, reasons, warnings, extra) {
  const v = {
    run_id: runId, gate_cfg: cfg, gate_cfg_hash: sha256Hex(canon(cfg)),
    status, reason_codes: [...new Set(reasons)].sort(), warnings: [...new Set(warnings)].sort(),
    scorer_version: SCORER_VERSION,
  };
  for (const [k, val] of Object.entries(extra ?? {})) if (val !== null && val !== undefined) v[k] = val;
  const core = {};
  for (const k of DECISION_CORE_KEYS) if (k in v) core[k] = v[k];
  v.decision_core_canon = canon(core);
  v.decision_core_sha256 = sha256Hex(v.decision_core_canon);
  return v;
}

// ---------------------------------------------------------------------------
// score — scorer.py 의 score() 와 1:1
// ---------------------------------------------------------------------------
export function score(trace, cfgIn) {
  const cfg = merge(DEFAULT_CFG, cfgIn ?? {});
  const gates = cfg.gates, ncfg = cfg.normalizer;
  const warnings = [], reasons = [];
  const byKind = {};
  for (const r of trace) (byKind[r.kind] ??= []).push(r);

  const headers = byKind.run_header ?? [], footers = byKind.run_footer ?? [];
  if (headers.length !== 1) throw new Error(`run_header must appear exactly once, got ${headers.length}`);
  const header = headers[0], runId = header.run_id;
  const schemaVersion = parseInt(header.trace_schema_version ?? 1, 10);
  for (const o of byKind.observation ?? []) {
    const bad = Object.keys(o).filter(k => FORBIDDEN_OBS_FIELDS.has(k)).sort();
    if (bad.length) throw new Error(`schema violation: observation contains verdict fields ${pyListRepr(bad)}`);
  }
  if (!trace.every((r, i) => r.seq === i)) throw new Error("seq is not contiguous — trace truncated or reordered");

  const cands = (byKind.candidate ?? []).map(c => c.candidate_id);
  const probes = {}; for (const p of byKind.probe ?? []) probes[p.probe_id] = p;
  const obs = byKind.observation ?? [], infra = byKind.infra_event ?? [];

  if (footers.length !== 1 || !footers[0].completed) reasons.push("RUNNER_FAILURE");
  if (header.task_source !== "local") {
    if (!header.git_stripped) { reasons.push("RUNNER_FAILURE"); warnings.push("git_stripped=false on non-local task"); }
    if (!header.network_blocked) { reasons.push("RUNNER_FAILURE"); warnings.push("network_blocked=false on non-local task"); }
  } else if (!header.network_blocked) warnings.push("local run: network not blocked (Sandbox-only guarantee)");
  for (const ev of infra) if (["install_failed", "build_failed", "sandbox_spawn_failed"].includes(ev.event)) reasons.push("RUNNER_FAILURE");

  let completenessProblems = [];
  if (schemaVersion >= 2) {
    completenessProblems = checkCompleteness(byKind);
    if (completenessProblems.length) reasons.push("INCOMPLETE_TRACE");
    if (obs.some(o => !(o.capture_ok ?? true))) reasons.push("CAPTURE_FAILURE");
  } else warnings.push("schema v1: job completeness not verifiable");

  const rep = new Map();
  for (const o of obs) {
    const k = o.probe_id + "\u0000" + o.candidate_id;
    (rep.get(k) ?? rep.set(k, []).get(k)).push(keyStr(obsKey(o, ncfg, schemaVersion)));
  }
  const nondet = [...rep.entries()].filter(([, v]) => new Set(v).size > 1).map(([k]) => k.split("\u0000"))
    .sort((a, b) => a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : a[1] < b[1] ? -1 : a[1] > b[1] ? 1 : 0);
  if (nondet.length) reasons.push("NONDETERMINISTIC");

  if (["RUNNER_FAILURE", "NONDETERMINISTIC", "INCOMPLETE_TRACE", "CAPTURE_FAILURE"].some(r => reasons.includes(r))) {
    return verdict(runId, cfg, "UNVERIFIABLE", reasons, warnings,
      { nondeterministic_pairs: nondet.slice(0, 10), completeness_problems: completenessProblems.length ? completenessProblems : null });
  }

  const g4 = { passed: 0, failed: 0, error: 0, timeout: 0, failing_candidates: [] };
  for (const r of byKind.regression_result ?? []) if (r.suite !== "R_gate") throw new Error(`regression suite ${r.suite} must not appear in gate trace (tautology risk)`);
  const failing = new Set();
  for (const r of byKind.regression_result ?? []) {
    g4[r.outcome] = (g4[r.outcome] ?? 0) + 1;
    if (r.outcome !== "passed") failing.add(r.candidate_id);
  }
  g4.failing_candidates = [...failing].sort();

  let pool = [...cands];
  if (gates.G4 ?? true) {
    pool = cands.filter(c => !failing.has(c));
    if (failing.size) {
      reasons.push("REGRESSION_FAILURE");
      if (!pool.length) return verdict(runId, cfg, "CODE_INCOMPLETE", reasons, warnings, { g4 });
    }
  }
  const g2 = (gates.G2 ?? true) ? g2Metrics(byKind) : null;

  const patchOf = {}; for (const c of byKind.candidate ?? []) patchOf[c.candidate_id] = c.patch_hash;
  const distinctPool = new Set(pool.map(c => patchOf[c])).size;
  if (distinctPool < cfg.min_candidates) {
    reasons.push("INSUFFICIENT_DIVERSITY");
    return verdict(runId, cfg, "UNVERIFIABLE", reasons, warnings, { g4, g2, n_eff_candidates: distinctPool });
  }

  const first = {};
  for (const o of obs) if (o.repeat_idx === 0) first[o.probe_id + "\u0000" + o.candidate_id] = o;
  const validProbes = [];
  for (const pid of Object.keys(probes).sort()) {
    const ok = pool.every(c => { const o = first[pid + "\u0000" + c]; return o && o.exit_code === 0 && !o.timed_out; });
    if (ok) validProbes.push(pid);
  }
  const anyProbe = Object.values(probes)[0] ?? {};
  const g3Search = {
    domain: anyProbe.input_domain ?? null, generator: anyProbe.generator ?? null,
    generator_seed: anyProbe.generator_seed ?? null, budget: anyProbe.generator_budget ?? null,
    n_valid_probes: validProbes.length, n_eff_candidates: pool.length, observation_channels: ["return_value"],
  };

  const show = (o) => (schemaVersion >= 2 && o.exception_type !== null && o.exception_type !== undefined)
    ? `EXC:${o.exception_type}(${pyStrRepr(o.exception_message_raw ?? "")})` : o.return_value_raw;

  let witness = null;
  if (gates.G3 ?? true) {
    if (!validProbes.length) {
      reasons.push("NO_VALID_PROBES");
      return verdict(runId, cfg, "UNVERIFIABLE", reasons, warnings, { g4, g2, g3_search: g3Search });
    }
    for (const pid of validProbes) {
      const keys = pool.map(c => keyStr(obsKey(first[pid + "\u0000" + c], ncfg, schemaVersion)));
      if (new Set(keys).size > 1) {
        const outputs = {}; for (const c of pool) outputs[c] = show(first[pid + "\u0000" + c]);
        witness = { probe_id: pid, input: probes[pid].input, outputs };
        break;
      }
    }
    if (witness) {
      reasons.push("BEHAVIORAL_DISAGREEMENT");
      return verdict(runId, cfg, "NEEDS_CLARIFICATION", reasons, warnings, { witness, g3_search: g3Search, g4, g2 });
    }
  }
  return verdict(runId, cfg, "PASS", reasons, warnings, { g3_search: g3Search, g4, g2 });
}
