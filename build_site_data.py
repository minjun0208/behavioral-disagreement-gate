#!/usr/bin/env python3
"""
build_site_data.py — 사이트 데이터 빌드  (v0.1.0)

입력  runs/ ledger/ clarify/ grades/ experiments/
출력  site/data/
        index.json                  매니페스트
        sessions/<sid>.json         명료화 세션 리플레이 (라운드별 trace-lite + verdict + 질문/답/결정)
        experiments/compliance.json 210건 제약 준수 집계
        experiments/ablation.json   G4 paired ablation
        experiments/diversity.json  run 코호트별 감지율 (예: v2_*, xt_*)
        provenance.json             canary 검사 / 백엔드 교차 decision_core 일치 / 스키마 버전 분포

trace-lite: 원본 trace 에서 표시·판정에 불필요한 무거운 필드만 제거.
  제거: stdout_raw stderr_raw ts duration_ms files_written stdout_bytes stderr_bytes output_truncated
        oom gen_duration_ms tokens_in tokens_out prompt_ref response_ref patch_ref
  유지: scorer.score() 가 읽는 모든 필드  → 빌드 시 "scorer(lite) == scorer(full)" 를 검증

격리: 출력 전체를 grades/*/canary.txt 문자열 + "gold/" 문자열로 스캔. 발견 시 빌드 실패.
      gold 테스트 내용은 절대 포함하지 않는다. grade 결과는 후보별 pass 개수(집계)만.
"""
import argparse
import collections
import datetime as dt
import glob
import hashlib
import json
import pathlib
import subprocess
import sys

import scorer

BUILD_VERSION = "0.2.0"
SITE_SCHEMA = 1
STRIP = {"stdout_raw", "stderr_raw", "ts", "duration_ms", "files_written", "stdout_bytes", "stderr_bytes",
         "output_truncated", "oom", "gen_duration_ms", "tokens_in", "tokens_out", "prompt_ref", "response_ref",
         "patch_ref"}


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def load_jsonl(p: pathlib.Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def dump(path: pathlib.Path, obj) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    path.write_text(s, encoding="utf-8", newline="\n")
    return len(s.encode("utf-8"))


# ---------------------------------------------------------------------------
def trace_lite(run_dir: pathlib.Path, cfg: dict) -> tuple[list[dict], dict, dict]:
    """(lite records, python verdict on full, python verdict on lite). 둘의 decision_core 가 같아야 한다."""
    full = load_jsonl(run_dir / "trace.jsonl")
    lite = [{k: v for k, v in r.items() if k not in STRIP} for r in full]
    # 후보 소스 첨부 (표시용) — patches/<hash>.diff
    for r in lite:
        if r["kind"] == "candidate":
            p = run_dir / "patches" / f"{r['patch_hash']}.diff"
            r["source"] = p.read_text(encoding="utf-8") if p.exists() else None
    v_full = scorer.score(full, cfg)
    v_lite = scorer.score([{k: v for k, v in r.items() if k != "source"} for r in lite], cfg)
    return lite, v_full, v_lite


def grade_summary(grades_root: pathlib.Path, run_id: str) -> dict | None:
    """gold 결과를 후보별 pass 개수로만 집계. test_id·입력·기대값은 포함하지 않는다."""
    p = grades_root / run_id / "grade.jsonl"
    if not p.exists():
        return None
    per: dict = {}
    canary_ok = None
    for r in load_jsonl(p):
        if r["kind"] == "gold_result":
            d = per.setdefault(r["patch_hash"], {"T_meta": [0, 0], "R_meta": [0, 0]})
            d[r["suite"]][1] += 1
            d[r["suite"]][0] += (r["outcome"] == "passed")
        elif r["kind"] == "canary_check":
            canary_ok = not r["canary_found"]
    return {"by_patch_hash": per, "canary_ok": canary_ok}


def build_session(sid: str, roots: dict, cfg: dict, report: list) -> dict | None:
    lpath = roots["ledger"] / sid / "ledger.jsonl"
    if not lpath.exists():
        return None
    ledger = load_jsonl(lpath)
    header = next(r for r in ledger if r["kind"] == "session_header")
    footer = next((r for r in ledger if r["kind"] == "session_footer"), None)
    cdir = roots["clarify"] / sid
    rounds = []
    for r in range(1, header["max_rounds"] + 1):
        run_dir = roots["runs"] / f"{sid}_r{r}"
        if not (run_dir / "trace.jsonl").exists():
            break
        lite, v_full, v_lite = trace_lite(run_dir, cfg)
        same = v_full["decision_core_sha256"] == v_lite["decision_core_sha256"]
        report.append((f"{sid}_r{r}", same))
        qs = sorted(cdir.glob(f"round_{r}.question*.json"))
        tpath = cdir / f"round_{r}.task.json"
        task = json.loads(tpath.read_text(encoding="utf-8")) if tpath.exists() else None
        rounds.append({
            "round": r, "run_id": f"{sid}_r{r}",
            "probe_seed": header["seed_schedule"][r - 1],
            "confirmed_behaviors": (task or {}).get("confirmed_behaviors", []),
            "acceptance_tests": [t for t in (task or {}).get("r_gate", []) if t.get("origin") == "user_confirmed"],
            "trace": lite,
            "verdict_python": {k: v for k, v in v_full.items() if k != "gate_cfg"},
            "lite_matches_full": same,
            "questions": [json.loads(q.read_text(encoding="utf-8")) for q in qs],
            "grade": grade_summary(roots["grades"], f"{sid}_r{r}"),
        })
    return {
        "session_id": sid, "task_id": header["task_id"], "provider": header.get("provider"), "gen": header.get("gen"),
        "max_rounds": header["max_rounds"], "seed_schedule": header["seed_schedule"],
        "outcome": footer["outcome"] if footer else "INCOMPLETE",
        "rounds_used": footer["rounds_used"] if footer else len(rounds),
        "ledger": [{k: v for k, v in rec.items() if k != "ts"} for rec in ledger],
        "rounds": rounds,
    }


def build_diversity(roots: dict, cfg: dict, prefixes: list[str], report: list) -> dict:
    """run_id 접두어(코호트)별 판정 집계 + trace-lite. 각 run 은 task_id 로 묶는다."""
    cohorts = {}
    for run_dir in sorted(roots["runs"].iterdir()):
        rid = run_dir.name
        pref = next((p for p in prefixes if rid.startswith(p)), None)
        if pref is None or not (run_dir / "trace.jsonl").exists():
            continue
        lite, v_full, v_lite = trace_lite(run_dir, cfg)
        report.append((rid, v_full["decision_core_sha256"] == v_lite["decision_core_sha256"]))
        hdr = lite[0]
        cohorts.setdefault(pref, []).append({
            "run_id": rid, "task_id": hdr.get("task_id"), "backend": hdr.get("backend", "local_subprocess"),
            "schema": hdr.get("trace_schema_version", 1), "status": v_full["status"],
            "decision_core_sha256": v_full["decision_core_sha256"],
            "verdict_python": {k: v for k, v in v_full.items() if k != "gate_cfg"},
            "reason_codes": v_full["reason_codes"], "witness": v_full.get("witness"),
            "n_eff_candidates": (v_full.get("g3_search") or {}).get("n_eff_candidates"),
            "models": sorted({c["model"] for c in lite if c["kind"] == "candidate"}),
            "grade": grade_summary(roots["grades"], rid), "trace": lite,
        })
    summary = {}
    for pref, runs in cohorts.items():
        by_task = collections.defaultdict(lambda: [0, 0])
        for r in runs:
            by_task[r["task_id"]][1] += 1
            by_task[r["task_id"]][0] += (r["status"] == "NEEDS_CLARIFICATION")
        summary[pref] = {"n_runs": len(runs), "detected": sum(1 for r in runs if r["status"] == "NEEDS_CLARIFICATION"),
                         "by_task": dict(by_task)}
    return {"cohorts": cohorts, "summary": summary}


def build_compliance(path: pathlib.Path) -> dict | None:
    if not path.exists():
        return None
    import experiment_compliance as E
    recs = [E._upgrade(r) for r in load_jsonl(path)]
    ok = [r for r in recs if r["status"] == "ok"]
    models = sorted({r["model"] for r in recs})
    cells = {}
    for cd in E.CONDITIONS:
        for m in models:
            rs = [r for r in ok if r["condition"] == cd and r["model"] == m]
            if rs:
                cells[f"{cd}|{m}"] = {"n": len(rs), "sat": sum(r["satisfies"] for r in rs) if cd != "none" else None,
                                      "ref": sum(bool(r["ref_policy_match"]) for r in rs) if cd != "none" else None,
                                      "conventions": dict(collections.Counter(r["convention"] for r in rs))}
    return {"source": path.name, "n_records": len(recs), "n_ok": len(ok), "models": models,
            "conditions": {k: {"target": v["target"], "n_confirmed": len(v["confirmed"]),
                               "confirmed": v["confirmed"]} for k, v in E.CONDITIONS.items()},
            "cells": cells,
            "records": [{k: r.get(k) for k in ("condition", "model", "rep", "seed", "convention", "satisfies",
                                                "ref_policy_match", "source_sha256", "status")} for r in recs]}


def build_provenance(roots: dict, all_lite: list[tuple[str, list, dict]]) -> dict:
    """백엔드 교차 일치: (task, job_manifest, patch hash 집합) 이 같고 backend 가 다른 run 들의 decision_core 비교."""
    groups = collections.defaultdict(list)
    for rid, lite, v in all_lite:
        hdr = lite[0]
        plan = next((r for r in lite if r["kind"] == "job_plan"), None)
        patches = tuple(sorted(c["patch_hash"] for c in lite if c["kind"] == "candidate"))
        seed = next((p["generator_seed"] for p in lite if p["kind"] == "probe"), None)
        key = (hdr.get("task_id"), plan["job_manifest_sha256"] if plan else None, patches, seed)
        groups[key].append({"run_id": rid, "backend": hdr.get("backend", "local_subprocess"),
                            "core": v["decision_core_sha256"], "status": v["status"]})
    cross = []
    for key, runs in groups.items():
        if len({r["backend"] for r in runs}) > 1:
            cross.append({"task_id": key[0], "runs": runs, "identical": len({r["core"] for r in runs}) == 1})
    canaries = []
    for g in sorted(roots["grades"].glob("*/grade.jsonl")):
        for r in load_jsonl(g):
            if r["kind"] == "canary_check":
                canaries.append({"run_id": r["run_id"], "files_scanned": r["files_scanned"], "found": r["canary_found"],
                                 "scanned_paths": r.get("scanned_paths")})
    schema_dist = collections.Counter(lite[0].get("trace_schema_version", 1) for _, lite, _ in all_lite)
    backend_dist = collections.Counter(lite[0].get("backend", "local_subprocess") for _, lite, _ in all_lite)
    return {"cross_backend": cross, "canary_checks": canaries,
            "canary_all_clear": all(not c["found"] for c in canaries) if canaries else None,
            "trace_schema_versions": dict(schema_dist), "backends": dict(backend_dist), "scorer_version": scorer.SCORER_VERSION}


# ---------------------------------------------------------------------------
def leak_scan(out_root: pathlib.Path, grades_root: pathlib.Path) -> list[str]:
    """출력 전체에서 canary 문자열·gold 테스트 식별자 검색. 하나라도 있으면 빌드 실패."""
    needles = set()
    for c in grades_root.glob("*/canary.txt"):
        needles.add(c.read_text(encoding="utf-8").strip().encode())
    needles.add(b"gold/test_")         # gold 테스트 식별자 (acceptance 는 acceptance/ 접두어라 구분됨)
    needles.add(b"gold/README")
    hits = []
    for f in out_root.rglob("*.json"):
        data = f.read_bytes()
        for n in needles:
            if n in data:
                hits.append(f"{f}: {n[:24]!r}")
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="site/data")
    ap.add_argument("--sessions", default=None, help="comma list. 기본: ledger/ 아래 전부")
    ap.add_argument("--diversity-prefixes", default="v2_,xt_,r_llm_,r_xm_", help="run_id 접두어 코호트")
    ap.add_argument("--compliance", default="experiments/compliance_full.jsonl")
    ap.add_argument("--ablation", default=None, help="기본: experiments/g4_ablation.json → experiments/g4_ablation_result.json → g4_ablation_result.json")
    ap.add_argument("--exclude-sessions", default="", help="comma list of session ids to leave out (test artifacts)")
    ap.add_argument("--cfg", default="cfg_full.json")
    args = ap.parse_args()

    roots = {k: pathlib.Path(k) for k in ("runs", "ledger", "clarify", "grades")}
    out = pathlib.Path(args.out)
    cfg = json.load(open(args.cfg, encoding="utf-8"))
    report: list[tuple[str, bool]] = []
    sizes = {}

    # sessions
    sids = args.sessions.split(",") if args.sessions else sorted(p.name for p in roots["ledger"].iterdir() if p.is_dir())
    sids = [s for s in sids if s not in set(x for x in args.exclude_sessions.split(",") if x)]
    # cfg 를 사이트에 동봉 (브라우저 재채점이 같은 게이트 설정을 쓰도록)
    sizes["cfg_full.json"] = dump(out / "cfg_full.json", cfg)
    sessions_idx, all_lite = [], []
    skipped = []
    for sid in sids:
        try:
            s = build_session(sid, roots, cfg, report)
        except Exception as e:  # noqa: BLE001 — 깨진 세션은 건너뛰고 기록 (빌드 전체를 죽이지 않음)
            skipped.append({"session_id": sid, "error": f"{type(e).__name__}: {e}"[:120]}); continue
        if s is None or not s["rounds"]:
            continue
        sizes[f"sessions/{sid}.json"] = dump(out / "sessions" / f"{sid}.json", s)
        sessions_idx.append({"session_id": sid, "task_id": s["task_id"], "outcome": s["outcome"],
                             "rounds_used": s["rounds_used"], "gen": s["gen"], "provider": s["provider"],
                             "n_decisions": sum(1 for r in s["ledger"] if r["kind"] == "decision")})
        for rd in s["rounds"]:
            all_lite.append((rd["run_id"], rd["trace"], rd["verdict_python"]))

    # diversity cohorts
    div = build_diversity(roots, cfg, [p for p in args.diversity_prefixes.split(",") if p], report)
    for pref, runs in div["cohorts"].items():
        for r in runs:
            all_lite.append((r["run_id"], r["trace"], {"decision_core_sha256": scorer.score(
                [{k: v for k, v in x.items() if k != "source"} for x in r["trace"]], cfg)["decision_core_sha256"], "status": r["status"]}))
    sizes["experiments/diversity.json"] = dump(out / "experiments" / "diversity.json", div)

    # 교차 백엔드 검증용: 세션/코호트 밖의 run 도 전부 훑는다 (cmp_local vs v6_contree 같은 쌍)
    seen = {rid for rid, _, _ in all_lite}
    for run_dir in sorted(roots["runs"].iterdir()):
        if run_dir.name in seen or not (run_dir / "trace.jsonl").exists():
            continue
        try:
            lite, v_full, v_lite = trace_lite(run_dir, cfg)
        except Exception:  # noqa: BLE001 — 깨진/미완 run 은 provenance 에서 제외
            continue
        report.append((run_dir.name, v_full["decision_core_sha256"] == v_lite["decision_core_sha256"]))
        all_lite.append((run_dir.name, lite, v_full))

    comp = build_compliance(pathlib.Path(args.compliance))
    if comp:
        sizes["experiments/compliance.json"] = dump(out / "experiments" / "compliance.json", comp)
    abl = next((pathlib.Path(p) for p in ([args.ablation] if args.ablation else
               ["experiments/g4_ablation.json", "experiments/g4_ablation_result.json", "g4_ablation_result.json"]) if pathlib.Path(p).exists()), pathlib.Path("experiments/g4_ablation.json"))
    if abl.exists():
        sizes["experiments/ablation.json"] = dump(out / "experiments" / "ablation.json", json.loads(abl.read_text(encoding="utf-8")))

    prov = build_provenance(roots, all_lite)
    prov["lite_equals_full"] = {"checked": len(report), "mismatch": [r for r, ok in report if not ok]}
    sizes["provenance.json"] = dump(out / "provenance.json", prov)

    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip() or None
    except Exception:  # noqa: BLE001
        commit = None
    index = {"site_schema": SITE_SCHEMA, "build_version": BUILD_VERSION, "built_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
             "source_commit": commit, "scorer_version": scorer.SCORER_VERSION, "sessions": sessions_idx,
             "diversity_cohorts": div["summary"], "has_compliance": comp is not None, "has_ablation": abl.exists(),
             "skipped_sessions": skipped, "files": sizes}
    sizes["index.json"] = dump(out / "index.json", index)

    hits = leak_scan(out, roots["grades"])
    total_kb = sum(sizes.values()) / 1024
    print(f"built {len(sizes)} files, {total_kb:.0f} KB → {out}")
    print(f"  sessions: {[s['session_id'] for s in sessions_idx]}")
    if skipped:
        print(f"  skipped (unparseable): {[x['session_id'] + ' — ' + x['error'][:40] for x in skipped]}")
    print(f"  diversity cohorts: {div['summary']}")
    print(f"  lite==full decision_core: {len(report) - len(prov['lite_equals_full']['mismatch'])}/{len(report)}")
    print(f"  cross-backend groups: {len(prov['cross_backend'])}  identical: {sum(1 for c in prov['cross_backend'] if c['identical'])}")
    print(f"  canary checks: {len(prov['canary_checks'])}  all clear: {prov['canary_all_clear']}")
    if prov["lite_equals_full"]["mismatch"]:
        print("  !! lite/full verdict mismatch:", prov["lite_equals_full"]["mismatch"]); sys.exit(2)
    if hits:
        print("  !! LEAK DETECTED — build rejected:"); [print("    ", h) for h in hits]; sys.exit(3)
    print("  leak scan: clean")


if __name__ == "__main__":
    main()
