#!/usr/bin/env python3
"""
experiment_g4_ablation.py — G4(회귀 게이트) 순증분 가치 paired ablation  (v0.1.0)

질문: G4 없이도 G3(행동 불일치)가 과거 결정 위반을 잡는가?
      잡지 못하는 경우(unanimous regression)가 존재하는가?

설계 (LLM 0회, fixture 만):
  round 1  후보 갈림 → 사용자 답(scripted) → ledger 에 결정 확정
  round 2  케이스별 후보 구성 (동결) → 같은 trace 를 G4 ON / OFF 로 재채점
    A  unanimous_violation : 전원이 과거 결정을 같은 방식으로 위반   ← G4 의 존재 이유
    B  all_comply          : 전원 준수 (negative control — 오차단 없음)
    C  partial_violation   : 일부만 위반 (live1~5 재현 — G3·G4 중복)
    D  unanimous_violation_plus_other_disagreement
                           : 전원 위반 + 다른 축에서 갈림 (G3 는 엉뚱한 걸 잡음)

측정:
  status(ON/OFF), 승인 pool, pool 내 위반 후보 수,
  unsafe_approval = PASS 인데 pool 에 위반 후보가 있음
  G3 detected     = G4 OFF 에서 NEEDS_CLARIFICATION
  G4 detected     = acceptance 실패 후보 존재 (cfg 무관, trace 사실)

기존 코드 무변경. loop.py / scorer.py 를 subprocess·import 로 호출.
"""
import json
import pathlib
import shutil
import subprocess
import sys

import scorer

EXP_VERSION = "0.1.0"
SESSION_SEED = 7
PROBE_BUDGET = 40

# 후보 소스 (소스 해시는 다르고 행동은 같은 변형을 섞어 "동일 후보 거부" 를 피함)
POS_INF_A = "import math\ndef round_half(x: float) -> int:\n    return math.floor(x + 0.5)\n"
POS_INF_B = "import math\ndef round_half(x: float) -> int:\n    return int(math.floor(x + 0.5))\n"
POS_INF_C = "import math\ndef round_half(x: float) -> int:\n    y = x + 0.5\n    return math.floor(y)\n"
AWAY_A    = "def round_half(x: float) -> int:\n    return int(x + 0.5) if x >= 0 else int(x - 0.5)\n"
AWAY_B    = "import math\ndef round_half(x: float) -> int:\n    return int(math.copysign(math.floor(abs(x) + 0.5), x))\n"
AWAY_C    = "def round_half(x: float) -> int:\n    if x >= 0:\n        return int(x + 0.5)\n    return int(x - 0.5)\n"
EVEN_A    = "def round_half(x: float) -> int:\n    return round(x)\n"

CASES = {
    "A_unanimous_violation":  {"c1": POS_INF_A, "c2": POS_INF_B, "c3": POS_INF_C},          # 전원 -0.5→0 (결정은 -1)
    "B_all_comply":           {"c1": AWAY_A, "c2": AWAY_B, "c3": AWAY_C},                   # 전원 -0.5→-1
    "C_partial_violation":    {"c1": AWAY_A, "c2": AWAY_B, "c3": POS_INF_A},                # c3 만 위반
    "D_unanimous_plus_other": {"c1": POS_INF_A, "c2": EVEN_A, "c3": POS_INF_B},             # 전원 -0.5 위반, 2.5 에서 갈림
}


def build_task(base: dict, case: str) -> dict:
    t = json.loads(json.dumps(base))
    # 대표 입력이 결정론적으로 -0.5 가 되도록 경계값을 작게 고정 (전부 포함됨)
    t["probe"]["boundary_cases"] = [{"x": v} for v in (-0.5, -1.5, -2.5, 0.5, 1.5, 2.5)]
    t["probe"]["boundary_fraction"] = 0.2
    t["probe"]["domain"] = "float[-1000,1000] + fixed ties {-2.5..2.5}"
    t.pop("fixture_candidates", None)
    t["fixture_rounds"] = {
        "1": {"c1": {"source": POS_INF_A, "agent_tests": []}, "c2": {"source": AWAY_A, "agent_tests": []}},
        "2": {cid: {"source": src, "agent_tests": []} for cid, src in CASES[case].items()},
    }
    return t


def score_trace(run_id: str, cfg_path: str) -> dict:
    trace = [json.loads(l) for l in open(f"runs/{run_id}/trace.jsonl", encoding="utf-8")]
    return scorer.score(trace, json.load(open(cfg_path, encoding="utf-8")))


def violators(run_id: str) -> set:
    """trace 사실: acceptance 테스트를 실패한 후보 (cfg 무관)."""
    v = set()
    for l in open(f"runs/{run_id}/trace.jsonl", encoding="utf-8"):
        r = json.loads(l)
        if r["kind"] == "regression_result" and r["test_id"].startswith("acceptance/") and r["outcome"] != "passed":
            v.add(r["candidate_id"])
    return v


def main():
    base = json.load(open("tasks/round_half.json", encoding="utf-8"))
    pathlib.Path("examples").mkdir(exist_ok=True)
    pathlib.Path("experiments").mkdir(exist_ok=True)
    answers = pathlib.Path("examples/g4_ablation.answers.json")
    answers.write_text(json.dumps([{"value": "-1"}]), encoding="utf-8")   # round 1: -0.5 → -1 (away)

    rows = []
    for case in CASES:
        sid = f"g4_{case}"
        for d in ("ledger", "clarify"):
            shutil.rmtree(pathlib.Path(d) / sid, ignore_errors=True)
        for p in pathlib.Path("runs").glob(f"{sid}_r*"):
            shutil.rmtree(p, ignore_errors=True)
        tpath = pathlib.Path("examples") / f"g4_ablation_{case}.task.json"
        tpath.write_text(json.dumps(build_task(base, case), ensure_ascii=False, indent=1), encoding="utf-8")

        # round 1~2 를 G4 ON 으로 실행 (loop 가 결정 확정 + round 2 trace 생성)
        p = subprocess.run([sys.executable, "loop.py", "--task", str(tpath), "--session", sid, "--answers", str(answers),
                            "--max-rounds", "2", "--probe-budget", str(PROBE_BUDGET), "--session-seed", str(SESSION_SEED),
                            "--cfg", "cfg_full.json", "--quiet"], capture_output=True, text=True)
        if not pathlib.Path(f"runs/{sid}_r2/trace.jsonl").exists():
            print(f"[{case}] round 2 trace missing\n{p.stdout[-600:]}\n{p.stderr[-600:]}"); continue

        q1 = json.load(open(f"clarify/{sid}/round_1.question.json", encoding="utf-8"))
        on = score_trace(f"{sid}_r2", "cfg_full.json")
        off = score_trace(f"{sid}_r2", "cfg_no_g4.json")
        viol = violators(f"{sid}_r2")
        cands = set(CASES[case])

        def pool(v):   # 승인 대상 pool = 전체 - (G4 ON 이면 탈락자)
            return cands - set(v.get("g4", {}).get("failing_candidates", [])) if v["gate_cfg"]["gates"]["G4"] else cands

        row = {
            "case": case, "round1_question": q1["representative"]["call"], "decision": "round_half(-0.5) == -1",
            "violators_in_trace": sorted(viol),
            "G4_on":  {"status": on["status"],  "reasons": on["reason_codes"],  "pool": sorted(pool(on)),
                       "violators_in_pool": sorted(viol & pool(on)),  "witness": (on.get("witness") or {}).get("input")},
            "G4_off": {"status": off["status"], "reasons": off["reason_codes"], "pool": sorted(pool(off)),
                       "violators_in_pool": sorted(viol & pool(off)), "witness": (off.get("witness") or {}).get("input")},
        }
        for k in ("G4_on", "G4_off"):
            row[k]["unsafe_approval"] = row[k]["status"] == "PASS" and len(row[k]["violators_in_pool"]) > 0
        row["G3_detected_disagreement"] = off["status"] == "NEEDS_CLARIFICATION"
        row["G4_detected_violation"] = len(viol) > 0
        row["G4_only_catch"] = row["G4_off"]["unsafe_approval"] and not row["G4_on"]["unsafe_approval"]
        rows.append(row)

    out = pathlib.Path("experiments/g4_ablation.json")
    out.write_text(json.dumps({"version": EXP_VERSION, "session_seed": SESSION_SEED, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"decision fixed in round 1: round_half(-0.5) == -1   (session_seed={SESSION_SEED})\n")
    print(f"{'case':26s} {'violators':10s} {'G4 ON':30s} {'G4 OFF':30s} {'unsafe(OFF→ON)':14s} G3det G4det  G4-only")
    for r in rows:
        on, off = r["G4_on"], r["G4_off"]
        print(f"{r['case']:26s} {','.join(r['violators_in_trace']) or '-':10s} "
              f"{on['status'] + ' ' + str(on['reasons']):30s} {off['status'] + ' ' + str(off['reasons']):30s} "
              f"{str(off['unsafe_approval']) + '→' + str(on['unsafe_approval']):14s} "
              f"{str(r['G3_detected_disagreement']):5s} {str(r['G4_detected_violation']):5s}  {r['G4_only_catch']}")
    print("\nG3 x G4 matrix (case → quadrant):")
    for r in rows:
        g3, g4 = r["G3_detected_disagreement"], r["G4_detected_violation"]
        quad = {(True, True): "both detect", (False, True): "G4 only", (True, False): "G3 only", (False, False): "neither"}[(g3, g4)]
        print(f"  {r['case']:26s} {quad}   witness_off={r['G4_off']['witness']}")
    print(f"\nsaved: {out}")


if __name__ == "__main__":
    main()
