#!/usr/bin/env python3
"""
experiment_compliance.py — 프롬프트 제약 준수 통제 실험  (v0.1.0)

질문: 확정 사례("MUST hold: round_half(x=-3.5) == -4")를 프롬프트에 넣으면
      각 모델이 실제로 그 관례로 전환하는가? baseline(제약 없음) 대비 얼마나?

설계 (루프 미사용 — 교란 변수 제거):
  조건: none / away1 / halfup1 / halfeven1 / away2 / halfup2 / halfeven2
        (숫자 = 확정 사례 개수. live5 가 제기한 "사례 개수 효과" 축)
  모델: 3종
  반복: --reps (기본 10), seed 만 변경
  → 후보 1개씩 생성 → 고정 probe 세트로 실행 → 관례 분류

측정:
  convention   : 후보가 속한 반올림 관례 (half_up / away_zero / half_even / half_down / toward_zero / other / broken)
  satisfies    : 조건의 확정 사례를 전부 만족하는가 (G4 통과 여부와 동일 의미)
  ref_policy_match  : 분류된 관례가 요구한 관례와 같은가 (사례만 맞추고 정책은 다른 "부분 준수" 구분)

원본은 experiments/compliance_<tag>.jsonl 에 전부 저장. 요약은 --summarize 로 재계산 가능.
기존 코드 무변경: llm_gen.generate_candidates / runner.execute 재사용.
"""
import argparse
import collections
import datetime as dt
import hashlib
import json
import math
import pathlib
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import llm_gen
import runner

EXP_VERSION = "0.2.0"
FN = "round_half"

# 조건: 확정 사례 (E절 형식 그대로 llm_gen.make_prompt 가 렌더링)
CONDITIONS = {
    "none":      {"target": None,        "confirmed": []},
    "away1":     {"target": "ties_away_from_zero", "confirmed": [{"x": -3.5, "expected": -4}]},
    "halfup1":   {"target": "ties_to_pos_inf",   "confirmed": [{"x": -3.5, "expected": -3}]},
    "halfeven1": {"target": "ties_to_even", "confirmed": [{"x": 8.5, "expected": 8}]},
    "away2":     {"target": "ties_away_from_zero", "confirmed": [{"x": -3.5, "expected": -4}, {"x": 2.5, "expected": 3}]},
    "halfup2":   {"target": "ties_to_pos_inf",   "confirmed": [{"x": -3.5, "expected": -3}, {"x": 2.5, "expected": 3}]},
    "halfeven2": {"target": "ties_to_even", "confirmed": [{"x": -39.5, "expected": -40}, {"x": 8.5, "expected": 8}]},
}

# 고정 probe: tie 입력(관례 판별) + non-tie(정상 동작 확인)
TIE_PROBES = [-39.5, -3.5, -2.5, -1.5, -0.5, 0.5, 1.5, 2.5, 3.5, 8.5]
SANE_PROBES = {-1.2: -1, 1.2: 1, 2.0: 2, 7.7: 8, -7.7: -8, 0.0: 0}

REFERENCE = {   # 관례별 기준 구현 (분류용 signature 생성)
    "ties_to_pos_inf":     lambda x: math.floor(x + 0.5),
    "ties_away_from_zero":   lambda x: int(x + 0.5) if x >= 0 else int(x - 0.5),
    "ties_to_even":   lambda x: round(x),
    "ties_to_neg_inf":   lambda x: math.ceil(x - 0.5),
    "ties_toward_zero": lambda x: int(x + 0.5) if x < 0 else int(x - 0.5) if x > 0 else 0,
}
SIGNATURES = {name: tuple(str(int(f(x))) for x in TIE_PROBES) for name, f in REFERENCE.items()}


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


# ---------------------------------------------------------------------------
def classify(source: str) -> dict:
    """후보 소스를 고정 probe 로 실행해 관례 분류. runner.execute 재사용 (로컬 subprocess)."""
    outs = {}
    for x in TIE_PROBES + list(SANE_PROBES):
        o = runner.execute(source, FN, {"x": x})
        outs[x] = o["return_value_raw"] if (o["exit_code"] == 0 and o.get("capture_ok", True)) else "ERR"
    sane_ok = all(_num_eq(outs[x], v) for x, v in SANE_PROBES.items())
    tie_sig = tuple(_canon_num(outs[x]) for x in TIE_PROBES)
    conv = "other"
    if not sane_ok or "ERR" in tie_sig:
        conv = "broken"
    else:
        for name, sig in SIGNATURES.items():
            if tie_sig == sig:
                conv = name; break
    return {"convention": conv, "sane_ok": sane_ok, "tie_outputs": {str(x): outs[x] for x in TIE_PROBES}}


def _canon_num(s: str) -> str:
    try:
        v = float(s)
        return str(int(v)) if v == int(v) else s
    except (ValueError, OverflowError):
        return "ERR" if s == "ERR" else s


def _num_eq(s: str, v) -> bool:
    try:
        return abs(float(s) - float(v)) < 1e-9
    except (ValueError, OverflowError):
        return False


def satisfies(tie_outputs: dict, confirmed: list) -> bool:
    return all(_num_eq(tie_outputs.get(str(c["x"]), "ERR"), c["expected"]) for c in confirmed)


# ---------------------------------------------------------------------------
def one_generation(task_base: dict, cond: str, model: str, rep: int, seed: int, temperature: float, dry_run: bool) -> dict:
    spec = CONDITIONS[cond]
    task = json.loads(json.dumps(task_base))
    task["confirmed_behaviors"] = [f"{FN}(x={c['x']!r}) == {c['expected']!r}" for c in spec["confirmed"]]
    rec = {"condition": cond, "target": spec["target"], "n_confirmed": len(spec["confirmed"]),
           "model": model, "rep": rep, "seed": seed, "temperature": temperature,
           "ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    try:
        gens, events = llm_gen.generate_candidates(task=task, models=[model], n=1, temperature=temperature,
                                                   base_seed=seed, jitter=False, dry_run=dry_run, max_attempts_per_slot=2)
    except Exception as e:  # noqa: BLE001
        rec.update({"status": "gen_error", "error": f"{type(e).__name__}: {e}"[:200]})
        return rec
    if not gens:
        rec.update({"status": "gen_failed", "events": [e["detail"][:120] for e in events]})
        return rec
    g = gens[0]
    cls = classify(g["source"])
    rec.update({"status": "ok", "source": g["source"], "source_sha256": sha256(g["source"]),
                "served_model": g["served_model"], "gen_duration_ms": g["gen_duration_ms"],
                "tokens_in": g["tokens_in"], "tokens_out": g["tokens_out"],
                "prompt_hash": g["prompt_hash"], **cls,
                "satisfies": satisfies(cls["tie_outputs"], spec["confirmed"]),
                "ref_policy_match": (cls["convention"] == spec["target"]) if spec["target"] else None})
    return rec


ALIAS = {"half_up": "ties_to_pos_inf", "away_zero": "ties_away_from_zero", "half_even": "ties_to_even",
         "half_down": "ties_to_neg_inf", "toward_zero": "ties_toward_zero"}


def _upgrade(r: dict) -> dict:
    """v0.1.0 기록(구 관례명 / full_policy) 호환."""
    r = dict(r)
    r["convention"] = ALIAS.get(r.get("convention"), r.get("convention"))
    r["target"] = ALIAS.get(r.get("target"), r.get("target"))
    if "ref_policy_match" not in r and "full_policy" in r:
        r["ref_policy_match"] = r["full_policy"]
    return r


def summarize(records: list[dict]) -> str:
    records = [_upgrade(r) for r in records]
    ok = [r for r in records if r["status"] == "ok"]
    fail = [r for r in records if r["status"] != "ok"]
    models = sorted({r["model"] for r in records})
    conds = [c for c in CONDITIONS if any(r["condition"] == c for r in records)]
    short = lambda m: m.split("/")[-1][:18]
    out = [f"records={len(records)}  ok={len(ok)}  gen_failed={len(fail)}", ""]

    # 1) baseline 관례 분포
    out.append("== baseline (condition=none): convention distribution per model ==")
    for m in models:
        rs = [r for r in ok if r["condition"] == "none" and r["model"] == m]
        c = collections.Counter(r["convention"] for r in rs)
        out.append(f"  {short(m):20s} n={len(rs):2d}  " + "  ".join(f"{k}:{v}" for k, v in c.most_common()))
    out.append("")

    # 2) 조건별 준수율
    out.append("== compliance per condition x model  (sat = 명시 사례 전부 만족 / ref = 고정 probe 에서 기준 관례와 일치) ==")
    out.append(f"  {'condition':10s} {'target':10s} " + " ".join(f"{short(m):>22s}" for m in models))
    for cd in conds:
        if cd == "none":
            continue
        row = f"  {cd:10s} {CONDITIONS[cd]['target'] or '-':10s} "
        for m in models:
            rs = [r for r in ok if r["condition"] == cd and r["model"] == m]
            if not rs:
                row += f"{'-':>22s} "; continue
            s = sum(r["satisfies"] for r in rs); f = sum(bool(r["ref_policy_match"]) for r in rs)
            row += f"{f'sat {s}/{len(rs)}  full {f}/{len(rs)}':>22s} "
        out.append(row)
    out.append("")

    # 3) 사례 개수 효과 (1개 vs 2개), 전 모델 합산
    out.append("== example-count effect (all models pooled): satisfies rate ==")
    for tgt in ("ties_away_from_zero", "ties_to_pos_inf", "ties_to_even"):
        for n in (1, 2):
            rs = [r for r in ok if r["target"] == tgt and r["n_confirmed"] == n]
            if rs:
                out.append(f"  {tgt:10s} n_confirmed={n}: sat {sum(r['satisfies'] for r in rs)}/{len(rs)}"
                           f"   full {sum(bool(r['ref_policy_match']) for r in rs)}/{len(rs)}")
    out.append("")

    # 4) 부분 준수: 사례는 맞췄는데 관례는 다름
    partial = [r for r in ok if r["target"] and r["satisfies"] and not r["ref_policy_match"]]
    out.append(f"== satisfies examples but different reference policy (허용된 다른 연장, 위반 아님): {len(partial)} ==")
    for r in partial[:12]:
        out.append(f"  {r['condition']:10s} {short(r['model']):20s} rep{r['rep']:2d} → {r['convention']}")
    return "\n".join(out)


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", default="tasks/round_half.json")
    ap.add_argument("--models", default="nvidia/Nemotron-3-Ultra-550b-a55b,Qwen/Qwen3-235B-A22B-Instruct-2507,deepseek-ai/DeepSeek-V4-Pro")
    ap.add_argument("--conditions", default="all")
    ap.add_argument("--reps", type=int, default=10)
    ap.add_argument("--temperature", type=float, default=0.8)
    ap.add_argument("--base-seed", type=int, default=5000)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--tag", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--summarize", default=None, help="기존 jsonl 요약만 재계산")
    args = ap.parse_args()

    if args.summarize:
        recs = [json.loads(l) for l in open(args.summarize, encoding="utf-8")]
        print(summarize(recs)); return

    task_base = json.load(open(args.task, encoding="utf-8"))
    conds = list(CONDITIONS) if args.conditions == "all" else args.conditions.split(",")
    models = args.models.split(",")
    tag = args.tag or dt.datetime.now().strftime("%m%d_%H%M")
    pathlib.Path("experiments").mkdir(exist_ok=True)
    out_path = pathlib.Path("experiments") / f"compliance_{tag}.jsonl"

    jobs = [(c, m, r, args.base_seed + i)
            for i, (c, m, r) in enumerate((c, m, r) for c in conds for m in models for r in range(args.reps))]
    print(f"jobs={len(jobs)}  conditions={conds}  models={[m.split('/')[-1] for m in models]}  reps={args.reps}  workers={args.workers}")
    print(f"out={out_path}")

    records = []
    with open(out_path, "w", encoding="utf-8") as fh, ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(one_generation, task_base, c, m, r, s, args.temperature, args.dry_run): (c, m, r) for c, m, r, s in jobs}
        for i, fut in enumerate(as_completed(futs), 1):
            rec = fut.result()
            records.append(rec)
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n"); fh.flush()
            if i % 10 == 0 or i == len(jobs):
                print(f"  {i}/{len(jobs)}  last: {rec['condition']} {rec['model'].split('/')[-1][:14]} → {rec.get('convention', rec['status'])}")
    print()
    print(summarize(records))


if __name__ == "__main__":
    main()
