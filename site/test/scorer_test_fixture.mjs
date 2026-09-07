import fs from "node:fs"; import { score, parsePy } from "../scorer.js";
const fx = parsePy(fs.readFileSync("site/test/test_fixture.json", "utf-8"));
let ok = 0;
for (const f of fx) {
  let v; try { v = score(f.trace, f.cfg); } catch (e) { console.log("  THROW", f.name, e.message); continue; }
  const same = v.decision_core_sha256 === f.py_core && v.status === f.py_status;
  if (same) ok++; else { console.log("  MISMATCH", f.name, "js:", v.status, v.reason_codes, "py:", f.py_status, f.py_reasons); console.log("    js cp:", v.completeness_problems, "\n    py cp:", f.py_cp); }
}
console.log(`fixture: ${ok}/${fx.length} identical`);
process.exit(ok === fx.length ? 0 : 1);
