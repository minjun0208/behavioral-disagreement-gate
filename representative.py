#!/usr/bin/env python3
"""
representative.py — 명료화 루프 / 대표 입력 선택  (v0.1.0)

Scorer 는 첫 불일치 probe 하나만 witness 로 낸다. 사람에게 묻기엔 무작위 값(-14.5)이 불편하다.
이 모듈은 같은 trace 에서 불일치 probe 전체를 모아 **가장 단순한 것**을 대표로 고른다.

- 추가 실행 없음. trace 만 읽는다.
- 판정에 관여하지 않는다. verdict 는 그대로, 표시용 선택일 뿐.
- 순수 함수. 같은 trace + 같은 cfg → 같은 대표.
- 비교 키는 scorer.obs_key 를 그대로 사용 → Scorer 와 "갈림" 정의가 일치.

선택 규칙 (v0.2, 순서대로):
  1. 불일치 probe 집합 D
  2. 행동 클래스(전체 출력 벡터가 같은 후보 묶음) 기준으로, 그 probe 의 답 하나로
     남을 수 있는 클래스 수의 최악값(worst_residual) 최소   ← separating witness
  3. 동률이면 complexity_score 최소
  4. 동률이면 canonical JSON 사전순 최소
restrict_to 로 후보 부분집합만 대상으로 재선택 가능 (라운드 내 후속 질문용).
"""
import json
import math

import scorer  # obs_key / normalize 재사용 (Scorer 와 동일한 갈림 정의)

REPRESENTATIVE_VERSION = "0.2.0"


def canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# ---------------------------------------------------------------------------
def complexity_score(value, _depth: int = 0) -> float:
    """작을수록 사람이 이해하기 쉬운 값. 결정론."""
    if _depth > 20:
        return 1e9
    if value is None or isinstance(value, bool):
        return 0.0
    if isinstance(value, int):
        return float(abs(value))
    if isinstance(value, float):
        if not math.isfinite(value):
            return 1e6
        s = repr(value)
        decimals = len(s.split(".")[1].rstrip("0")) if "." in s and "e" not in s else 0
        return abs(value) + decimals * 0.01
    if isinstance(value, str):
        return float(len(value))
    if isinstance(value, list):
        return 10.0 * len(value) + sum(complexity_score(v, _depth + 1) for v in value)
    if isinstance(value, dict):
        return 5.0 * len(value) + sum(complexity_score(v, _depth + 1) for v in value.values())
    return 1e6


def input_score(inp: dict) -> float:
    return sum(complexity_score(v) for v in inp.values())


# ---------------------------------------------------------------------------
def select(trace: list[dict], verdict: dict, cfg: dict, n_extra: int = 2, restrict_to=None) -> dict:
    """
    반환:
      {
        "representative": {"probe_id", "input", "outputs", "score", "partition"},
        "extra_examples": [ ... 최대 n_extra ... ],
        "n_disagreeing": int, "n_valid_probes": int, "pool": [...]
      }
    불일치가 없으면 representative = None.
    """
    cfg = scorer._merge(scorer.DEFAULT_CFG, cfg)
    ncfg = cfg["normalizer"]
    by_kind: dict[str, list] = {}
    for r in trace:
        by_kind.setdefault(r["kind"], []).append(r)
    header = by_kind["run_header"][0]
    schema_version = int(header.get("trace_schema_version", 1))

    cands = sorted(c["candidate_id"] for c in by_kind.get("candidate", []))
    # scorer 와 동일 규칙: G4 게이트가 켜진 verdict 에서만 회귀 실패 후보를 pool 에서 제외
    g4_on = (verdict.get("gate_cfg") or {}).get("gates", {}).get("G4", True)
    failing = set((verdict.get("g4") or {}).get("failing_candidates", [])) if g4_on else set()
    pool = [c for c in cands if c not in failing]
    if restrict_to is not None:
        pool = [c for c in pool if c in set(restrict_to)]
    probes = {p["probe_id"]: p for p in by_kind.get("probe", [])}
    first = {(o["probe_id"], o["candidate_id"]): o
             for o in by_kind.get("observation", []) if o["repeat_idx"] == 0}

    def show(o):
        if schema_version >= 2 and o.get("exception_type") is not None:
            return f"EXC:{o['exception_type']}({o.get('exception_message_raw', '')!r})"
        return o["return_value_raw"]

    # 유효 probe (pool 전원 정상 종료) 와 후보별 비교키
    valid, keys_at = [], {}
    for pid in sorted(probes):
        if not all((pid, c) in first and first[(pid, c)]["exit_code"] == 0 and not first[(pid, c)]["timed_out"] for c in pool):
            continue
        valid.append(pid)
        keys_at[pid] = {c: scorer.obs_key(first[(pid, c)], ncfg, schema_version) for c in pool}
    n_valid = len(valid)

    # 행동 클래스: 유효 probe 전체에서 출력 벡터가 같은 후보 묶음
    vec = {c: canon([canon(keys_at[pid][c]) for pid in valid]) for c in pool}
    class_of, classes = {}, {}
    for c in pool:
        cid = classes.setdefault(vec[c], f"B{len(classes) + 1}")
        class_of[c] = cid
    n_classes = len(classes)

    disagreeing = []
    for pid in valid:
        keys = keys_at[pid]
        if len(set(keys.values())) > 1:
            groups: dict = {}
            for c, k in keys.items():
                groups.setdefault(canon(k), []).append(c)
            partition = sorted(tuple(g) for g in groups.values())
            # 클래스 단위 분할: 같은 클래스는 항상 같은 그룹에 있음
            class_groups = [sorted({class_of[c] for c in g}) for g in partition]
            worst_residual = max(len(g) for g in class_groups)
            inp = probes[pid]["input"]
            disagreeing.append({
                "probe_id": pid, "input": inp,
                "outputs": {c: show(first[(pid, c)]) for c in pool},
                "score": input_score(inp), "partition": partition,
                "class_groups": class_groups, "worst_residual": worst_residual,
            })

    if not disagreeing:
        return {"representative": None, "extra_examples": [], "n_disagreeing": 0,
                "n_valid_probes": n_valid, "pool": pool, "n_classes": n_classes, "class_of": class_of}

    # 정렬: worst_residual(정보량) → complexity → canon. 같은 입력은 1개만.
    uniq, seen_inputs = [], set()
    for d in sorted(disagreeing, key=lambda d: (d["worst_residual"], d["score"], canon(d["input"]))):
        k = canon(d["input"])
        if k not in seen_inputs:
            uniq.append(d); seen_inputs.add(k)
    ordered = uniq
    rep = ordered[0]
    # extra: 대표와 다른 partition 을 우선 (다른 축의 갈림을 보여줌), 부족하면 점수순
    extras, seen_parts = [], {canon(rep["partition"])}
    for d in ordered[1:]:
        if len(extras) >= n_extra:
            break
        if canon(d["partition"]) not in seen_parts:
            extras.append(d); seen_parts.add(canon(d["partition"]))
    for d in ordered[1:]:
        if len(extras) >= n_extra:
            break
        if d not in extras:
            extras.append(d)
    return {"representative": rep, "extra_examples": extras, "n_disagreeing": len(disagreeing),
            "n_distinct_inputs": len(ordered),
            "n_valid_probes": n_valid, "pool": pool, "n_classes": n_classes, "class_of": class_of}
