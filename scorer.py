#!/usr/bin/env python3
"""
scorer.py — Behavioral Disagreement Gate / Scorer

역할: trace.jsonl 만 읽어 판정한다. 실행하지 않는다.
계약: verdict = score(trace, gate_cfg)   ← 순수 함수
      같은 입력 → 바이트 단위로 같은 출력. 시간·난수·외부 IO 없음.
      grades/ 는 절대 읽지 않는다.

usage: python scorer.py <trace.jsonl> <gate_cfg.json>
"""
import hashlib
import json
import math
import sys

SCORER_VERSION = "0.2.1"

# observation 레코드에 있으면 스키마 위반 (판정을 Runner 가 한 것)
FORBIDDEN_OBS_FIELDS = {"disagree", "mismatch", "pass", "fail", "verdict",
                        "normalized_output", "is_correct"}

DEFAULT_CFG = {
    "gates": {"G2": True, "G3": True, "G4": True},
    "normalizer": {
        "float_abs_tol": 1e-9,
        "strip_whitespace": True,
        "json_key_order_insensitive": True,
        "exception_compare": "type",           # v2 전용: "type"(기본) | "type_message"
    },
    "g2_role": "advisory",
    "min_candidates": 2,
}


# ---------------------------------------------------------------------------
# 정규화 (설정으로 통제. 원본은 trace 에 그대로 있으므로 언제든 재채점 가능)
# ---------------------------------------------------------------------------
def normalize(raw: str, ncfg: dict):
    s = raw.strip() if ncfg.get("strip_whitespace", True) else raw
    # 1) 숫자
    try:
        v = float(s)
        if math.isnan(v):
            return ("num_nan", "nan")      # 모든 NaN 동일 취급 (nan != nan 로 인한 거짓 불일치 방지)
        if math.isinf(v):
            return ("num", v)              # 양자화 생략. inf==inf, -inf==-inf 정상 비교
        tol = ncfg.get("float_abs_tol", 0.0)
        if tol > 0:
            return ("num", round(v / tol) * tol)
        return ("num", v)
    except ValueError:
        pass
    # 2) JSON
    try:
        obj = json.loads(s)
        if ncfg.get("json_key_order_insensitive", True):
            return ("json", json.dumps(obj, sort_keys=True, separators=(",", ":")))
        return ("json", s)
    except (ValueError, TypeError):
        pass
    # 3) 문자열
    return ("str", s)


def obs_key(o: dict, ncfg: dict, schema_version: int):
    """observation 1건을 비교 가능한 키로. v2 는 예외 메시지까지 볼 수 있음."""
    if o["exit_code"] != 0:
        return ("exit", o["exit_code"])
    if schema_version >= 2 and o.get("exception_type") is not None:
        if ncfg.get("exception_compare", "type") == "type_message":
            return ("exc", o["exception_type"], o.get("exception_message_raw"))
        return ("exc", o["exception_type"])
    return normalize(o["return_value_raw"], ncfg)


def check_completeness(by_kind: dict) -> list[str]:
    """v2 전용. job_plan 대비 누락·중복·바꿔치기 검출. 문제 목록 반환 (빈 리스트 = 정상)."""
    plans = by_kind.get("job_plan", [])
    if len(plans) != 1:
        return [f"job_plan must appear exactly once, got {len(plans)}"]
    plan = plans[0]
    problems = []
    seen = []
    expected_id = {
        "observation":        lambda r: f"obs:{r['probe_id']}:{r['candidate_id']}:{r['repeat_idx']}",
        "mutant_test_result": lambda r: f"mut:{r['mutant_id']}:{r['suite']}",
        "regression_result":  lambda r: f"reg:{r['candidate_id']}:{r['test_id']}",
    }
    for kind, n_exp in plan["planned_counts"].items():
        recs = by_kind.get(kind, [])
        ids = [r.get("job_id") for r in recs]
        if any(i is None for i in ids):
            problems.append(f"{kind}: record without job_id")
        bad = [r["job_id"] for r in recs if r.get("job_id") and kind in expected_id
               and expected_id[kind](r) != r["job_id"]]
        if bad:
            problems.append(f"{kind}: job_id inconsistent with record content ({len(bad)}): {bad[:2]}")
        if len(recs) != n_exp:
            problems.append(f"{kind}: planned {n_exp}, actual {len(recs)}")
        if len(set(ids)) != len(ids):
            problems.append(f"{kind}: duplicate job_id")
        seen += [i for i in ids if i is not None]
    if len(seen) != plan["n_jobs"]:
        problems.append(f"total jobs planned {plan['n_jobs']}, actual {len(seen)}")
    got = hashlib.sha256("\n".join(sorted(seen)).encode()).hexdigest()
    if got != plan["job_manifest_sha256"]:
        problems.append("job manifest hash mismatch (missing/extra/swapped job ids)")
    return problems


# ---------------------------------------------------------------------------
# 순수 함수
# ---------------------------------------------------------------------------
def score(trace: list[dict], cfg: dict) -> dict:
    cfg = _merge(DEFAULT_CFG, cfg)
    gates = cfg["gates"]
    ncfg = cfg["normalizer"]
    warnings = []
    reasons = []

    by_kind = {}
    for r in trace:
        by_kind.setdefault(r["kind"], []).append(r)

    # --- 구조 검증 ---------------------------------------------------------
    headers = by_kind.get("run_header", [])
    footers = by_kind.get("run_footer", [])
    if len(headers) != 1:
        raise ValueError(f"run_header must appear exactly once, got {len(headers)}")
    header = headers[0]
    run_id = header["run_id"]
    schema_version = int(header.get("trace_schema_version", 1))

    for o in by_kind.get("observation", []):
        bad = FORBIDDEN_OBS_FIELDS & set(o.keys())
        if bad:
            raise ValueError(f"schema violation: observation contains verdict fields {sorted(bad)}")

    seqs = [r["seq"] for r in trace]
    if seqs != list(range(len(trace))):
        raise ValueError("seq is not contiguous — trace truncated or reordered")

    # --- 기본 집계 ---------------------------------------------------------
    cands = [c["candidate_id"] for c in by_kind.get("candidate", [])]
    probes = {p["probe_id"]: p for p in by_kind.get("probe", [])}
    obs = by_kind.get("observation", [])
    infra = by_kind.get("infra_event", [])

    # --- UNVERIFIABLE 조건 (fail-closed: 인프라 문제를 코드 결함으로 오판하지 않음) ---
    if len(footers) != 1 or not footers[0].get("completed", False):
        reasons.append("RUNNER_FAILURE")
    if header.get("task_source") != "local":
        if not header.get("git_stripped", False):
            reasons.append("RUNNER_FAILURE")
            warnings.append("git_stripped=false on non-local task")
        if not header.get("network_blocked", False):
            reasons.append("RUNNER_FAILURE")
            warnings.append("network_blocked=false on non-local task")
    else:
        if not header.get("network_blocked", False):
            warnings.append("local run: network not blocked (Sandbox-only guarantee)")
    for ev in infra:
        if ev["event"] in ("install_failed", "build_failed", "sandbox_spawn_failed"):
            reasons.append("RUNNER_FAILURE")

    # 완전성: 계획된 작업이 전부, 정확히 한 번씩 있는가 (v2). v1 은 검증 불가 → 경고만
    completeness_problems = []
    if schema_version >= 2:
        completeness_problems = check_completeness(by_kind)
        if completeness_problems:
            reasons.append("INCOMPLETE_TRACE")
        if any(not o.get("capture_ok", True) for o in obs):
            reasons.append("CAPTURE_FAILURE")
    else:
        warnings.append("schema v1: job completeness not verifiable")

    # 비결정성: 같은 (probe, candidate) 의 반복 결과가 갈리면 UNVERIFIABLE
    rep = {}
    for o in obs:
        key = (o["probe_id"], o["candidate_id"])
        rep.setdefault(key, []).append(obs_key(o, ncfg, schema_version))
    nondet = sorted({k for k, v in rep.items() if len(set(v)) > 1})
    if nondet:
        reasons.append("NONDETERMINISTIC")

    if any(r in reasons for r in ("RUNNER_FAILURE", "NONDETERMINISTIC", "INCOMPLETE_TRACE", "CAPTURE_FAILURE")):
        return _verdict(run_id, cfg, "UNVERIFIABLE", reasons, warnings,
                        extra={"nondeterministic_pairs": [list(k) for k in nondet][:10],
                               "completeness_problems": completeness_problems or None})

    # --- G4 회귀 -----------------------------------------------------------
    g4 = {"passed": 0, "failed": 0, "error": 0, "timeout": 0, "failing_candidates": []}
    reg = [r for r in by_kind.get("regression_result", []) if r.get("suite") == "R_gate"]
    for r in by_kind.get("regression_result", []):
        if r.get("suite") != "R_gate":
            raise ValueError(f"regression suite {r.get('suite')} must not appear in gate trace (tautology risk)")
    failing = set()
    for r in reg:
        g4[r["outcome"]] = g4.get(r["outcome"], 0) + 1
        if r["outcome"] != "passed":
            failing.add(r["candidate_id"])
    g4["failing_candidates"] = sorted(failing)

    pool = list(cands)
    if gates.get("G4", True):
        pool = [c for c in cands if c not in failing]
        if failing:
            reasons.append("REGRESSION_FAILURE")
            if not pool:
                return _verdict(run_id, cfg, "CODE_INCOMPLETE", reasons, warnings, extra={"g4": g4})

    # --- G2 뮤테이션 (advisory: 상태에 영향 없음, 지표만 산출) ----------------
    g2 = None
    if gates.get("G2", True):
        g2 = _g2_metrics(by_kind)

    # --- G3 불일치 ---------------------------------------------------------
    # 다양성은 후보 '수'가 아니라 '구별되는 구현 수'로 센다.
    # 같은 patch_hash 후보 2개는 정의상 절대 갈리지 않으므로 1개로 취급.
    patch_of = {c["candidate_id"]: c.get("patch_hash") for c in by_kind.get("candidate", [])}
    distinct_pool = len({patch_of.get(c) for c in pool})
    if distinct_pool < cfg["min_candidates"]:
        reasons.append("INSUFFICIENT_DIVERSITY")
        return _verdict(run_id, cfg, "UNVERIFIABLE", reasons, warnings,
                        extra={"g4": g4, "g2": g2, "n_eff_candidates": distinct_pool})

    # 유효 probe: pool 내 모든 후보가 exit 0 이고 timeout 아님 (repeat 0 기준)
    first_obs = {}
    for o in obs:
        if o["repeat_idx"] == 0:
            first_obs[(o["probe_id"], o["candidate_id"])] = o

    valid_probes = []
    for pid in sorted(probes):
        ok = all(
            (pid, c) in first_obs and first_obs[(pid, c)]["exit_code"] == 0 and not first_obs[(pid, c)]["timed_out"]
            for c in pool
        )
        if ok:
            valid_probes.append(pid)

    any_probe = next(iter(probes.values()), {})
    g3_search = {
        "domain": any_probe.get("input_domain"),
        "generator": any_probe.get("generator"),
        "generator_seed": any_probe.get("generator_seed"),
        "budget": any_probe.get("generator_budget"),
        "n_valid_probes": len(valid_probes),
        "n_eff_candidates": distinct_pool,
        "observation_channels": ["return_value"],
    }

    witness = None
    if gates.get("G3", True):
        if not valid_probes:
            reasons.append("NO_VALID_PROBES")
            return _verdict(run_id, cfg, "UNVERIFIABLE", reasons, warnings,
                            extra={"g4": g4, "g2": g2, "g3_search": g3_search})
        for pid in valid_probes:
            norm = {c: obs_key(first_obs[(pid, c)], ncfg, schema_version) for c in pool}
            if len(set(norm.values())) > 1:
                def _show(o):
                    if schema_version >= 2 and o.get("exception_type") is not None:
                        return f"EXC:{o['exception_type']}({o.get('exception_message_raw', '')!r})"
                    return o["return_value_raw"]
                witness = {
                    "probe_id": pid,
                    "input": probes[pid]["input"],
                    "outputs": {c: _show(first_obs[(pid, c)]) for c in pool},
                }
                break
        if witness is not None:
            reasons.append("BEHAVIORAL_DISAGREEMENT")
            return _verdict(run_id, cfg, "NEEDS_CLARIFICATION", reasons, warnings,
                            extra={"witness": witness, "g3_search": g3_search, "g4": g4, "g2": g2})

    return _verdict(run_id, cfg, "PASS", reasons, warnings,
                    extra={"g3_search": g3_search, "g4": g4, "g2": g2})


def _g2_metrics(by_kind: dict) -> dict:
    """
    K_base   : 기존 테스트가 실패시킨 mutant
    K_agent  : 에이전트 테스트가 실패시킨 mutant
    K_unique : 에이전트 테스트만 추가로 실패시킨 mutant  ← 제품 가치에 가까운 값
    'outcome == failed' 를 '해당 스위트가 결함에 민감했다' 로 해석하는 곳은 여기뿐.
    """
    per = {}
    for m in by_kind.get("mutant", []):
        if m.get("in_changed_region", False):
            per.setdefault(m["candidate_id"], {})[m["mutant_id"]] = {"base": None, "agent": None}
    for r in by_kind.get("mutant_test_result", []):
        d = per.get(r["candidate_id"], {}).get(r["mutant_id"])
        if d is not None:
            d[r["suite"]] = r["outcome"]
    out = {}
    for cid, muts in per.items():
        k_base = sum(1 for d in muts.values() if d["base"] == "failed")
        k_full = sum(1 for d in muts.values() if d["base"] == "failed" or d["agent"] == "failed")
        k_agent = sum(1 for d in muts.values() if d["agent"] == "failed")
        out[cid] = {"applicable": len(muts), "k_base": k_base, "k_agent": k_agent,
                    "k_unique": k_full - k_base, "role": "advisory"}
    return out


def _merge(base: dict, over: dict) -> dict:
    out = json.loads(json.dumps(base))
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    return out


def _cfg_hash(cfg: dict) -> str:
    return hashlib.sha256(json.dumps(cfg, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


DECISION_CORE_KEYS = ("status", "reason_codes", "witness", "g2", "g3_search", "g4",
                      "n_eff_candidates", "completeness_problems", "nondeterministic_pairs")


def _verdict(run_id, cfg, status, reasons, warnings, extra=None):
    v = {
        "run_id": run_id,
        "gate_cfg": cfg,
        "gate_cfg_hash": _cfg_hash(cfg),
        "status": status,
        "reason_codes": sorted(set(reasons)),
        "warnings": sorted(set(warnings)),
        "scorer_version": SCORER_VERSION,
    }
    if extra:
        v.update({k: val for k, val in extra.items() if val is not None})
    # decision_core: 판정에 해당하는 부분만. provenance(run_id·warnings·버전) 제외.
    # 같은 의미 증거 → 같은 decision_core_sha256 이어야 한다 (ts/uuid/duration 무관).
    core = {k: v[k] for k in DECISION_CORE_KEYS if k in v}
    v["decision_core_sha256"] = hashlib.sha256(
        json.dumps(core, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    return v


# ---------------------------------------------------------------------------
def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    trace = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8") if line.strip()]
    cfg = json.load(open(sys.argv[2], encoding="utf-8"))
    v = score(trace, cfg)
    # sort_keys=True → 바이트 단위 결정론
    print(json.dumps(v, sort_keys=True, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
