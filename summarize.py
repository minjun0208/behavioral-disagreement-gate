#!/usr/bin/env python3
"""
summarize.py — 여러 run 의 verdict + gold 결과를 표로. 사후 분석 전용 (게이트에 역류 금지).
usage: python summarize.py [--runs runs] [--grades grades] [--cfg cfg_full.json]
"""
import json, pathlib, subprocess, sys

def opt(flag, default):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default
runs_root, grades_root, cfg = pathlib.Path(opt("--runs", "runs")), pathlib.Path(opt("--grades", "grades")), opt("--cfg", "cfg_full.json")

rows = []
for rd in sorted(runs_root.iterdir()):
    tr = rd / "trace.jsonl"
    if not tr.exists():
        continue
    p = subprocess.run([sys.executable, "scorer.py", str(tr), cfg], capture_output=True, text=True)
    if p.returncode != 0 or not p.stdout.strip():
        err = (p.stderr.strip().splitlines() or ["(no stderr)"])[-1]
        v = {"status": "SCORER_ERROR", "_err": err[:70]}
    else:
        v = json.loads(p.stdout)
    man = json.loads((rd / "manifest.json").read_text()) if (rd / "manifest.json").exists() else {}
    task = man.get("task_id", "?")
    w = v.get("witness")
    wit = (json.dumps(w["input"], ensure_ascii=False)[:28] + " → " + "/".join(w["outputs"][k] for k in sorted(w["outputs"]))) if w else v.get("_err", "")
    gold = ""
    gp = grades_root / rd.name / "grade.jsonl"
    if gp.exists():
        per = {}
        for l in open(gp, encoding="utf-8"):
            r = json.loads(l)
            if r["kind"] == "gold_result" and r["suite"] == "T_meta":
                per.setdefault(r["patch_hash"][:6], []).append(r["outcome"] == "passed")
        gold = " ".join(f"{k}:{sum(x)}/{len(x)}" for k, x in per.items())
    rows.append((rd.name, task, v.get("status", "?"), wit, gold))

w0 = max(len(r[0]) for r in rows) if rows else 6
print(f"{'run':{w0}}  {'task':15} {'status':22} {'witness':44} gold(T_meta pass)")
for r in rows:
    print(f"{r[0]:{w0}}  {r[1]:15} {r[2]:22} {r[3]:44} {r[4]}")
