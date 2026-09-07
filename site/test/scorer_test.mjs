// Node 검증: site/data 의 모든 run 에 대해 JS verdict → decision_core_sha256 를 Python 값과 대조
import fs from "node:fs"; import path from "node:path";
import { score, parsePy, canon, pyFloatRepr, pyRound, pyStrRepr, sha256Hex } from "../scorer.js";

const dataDir = process.argv[2] ?? "site/data";
const cfg = parsePy(fs.readFileSync("cfg_full.json", "utf-8"));

// 0) 단위: Python 규칙 재현
const unit = [
  [pyFloatRepr(2.5), "2.5"], [pyFloatRepr(2.0), "2.0"], [pyFloatRepr(-0.0), "-0.0"], [pyFloatRepr(1e-9), "1e-09"],
  [pyFloatRepr(1e-5), "1e-05"], [pyFloatRepr(0.0001), "0.0001"], [pyFloatRepr(1e16), "1e+16"],
  [pyFloatRepr(1e15), "1000000000000000.0"], [pyFloatRepr(123456789.123), "123456789.123"],
  [pyFloatRepr(-96.83908045977012), "-96.83908045977012"], [pyFloatRepr(1e22), "1e+22"], [pyFloatRepr(5e-324), "5e-324"],
  [String(pyRound(0.5)) + pyRound(1.5) + pyRound(2.5) + pyRound(-0.5), "0220"],
  [pyStrRepr("it's"), `"it's"`], [pyStrRepr('say "hi"'), `'say "hi"'`], [pyStrRepr("both ' and \""), `'both \\' and "'`],
  [pyStrRepr("\n\t\x01é\u2028"), `'\\n\\t\\x01é\\u2028'`],
  [canon(parsePy('{"b":[1,2.0,"x"],"a":null,"c":true}')), '{"a":null,"b":[1,2.0,"x"],"c":true}'],
  [canon(parsePy('{"x": 1e-9, "y": -0.0, "z": 1E5}')), '{"x":1e-09,"y":-0.0,"z":100000.0}'],
  [sha256Hex("abc"), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"],
];
let uf = 0;
for (const [got, exp] of unit) if (got !== exp) { uf++; console.log("  UNIT FAIL:", JSON.stringify(got), "≠", JSON.stringify(exp)); }
console.log(`unit: ${unit.length - uf}/${unit.length} pass`);

// 1) 세션 + 코호트 run 전부
const index = parsePy(fs.readFileSync(path.join(dataDir, "index.json"), "utf-8"));
const runs = [];
for (const s of index.sessions) {
  const sess = parsePy(fs.readFileSync(path.join(dataDir, "sessions", s.session_id + ".json"), "utf-8"));
  for (const rd of sess.rounds) runs.push({ id: rd.run_id, trace: rd.trace, py: rd.verdict_python });
}
const div = parsePy(fs.readFileSync(path.join(dataDir, "experiments", "diversity.json"), "utf-8"));
for (const cohort of Object.values(div.cohorts)) for (const r of cohort) runs.push({ id: r.run_id, trace: r.trace, py: r.verdict_python });

let ok = 0; const bad = [];
for (const r of runs) {
  const trace = r.trace.map(x => { const { source, ...rest } = x; return rest; });   // Python 도 source 제외하고 채점
  let v;
  try { v = score(trace, cfg); } catch (e) { bad.push([r.id, "THROW " + e.message]); continue; }
  if (v.decision_core_sha256 === r.py.decision_core_sha256 && v.status === r.py.status) ok++;
  else bad.push([r.id, `js=${v.status} ${v.decision_core_sha256.slice(0, 12)}  py=${r.py.status} ${r.py.decision_core_sha256.slice(0, 12)}`, v.decision_core_canon]);
}
console.log(`runs: ${ok}/${runs.length} decision_core identical`);
for (const b of bad) { console.log("  MISMATCH", b[0], b[1]); if (b[2]) console.log("    js canon:", b[2].slice(0, 300)); }
process.exit(uf || bad.length ? 1 : 0);
