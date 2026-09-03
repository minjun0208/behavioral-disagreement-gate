#!/usr/bin/env python3
"""
question.py — 명료화 루프 / 질문 생성  (v0.1.0)

원칙:
  L1  결정론적 템플릿. LLM 미사용.
  앵커링 방지: 모델명·득표수 비노출. 선택지는 표시 문자열 사전순 (후보 순서 무관).
  항상 열린 선택지 제공: other_value / other_exception / unknown  (GPT 라운드3 지적)

입력: task, representative.select() 결과, round, run_id
출력: question dict (ledger 'question' 레코드 그대로 기록 가능) + 표시용 텍스트
"""
import json

import ledger  # question_hash

QUESTION_VERSION = "0.1.0"


def canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _call_repr(fn_name: str, inp: dict) -> str:
    args = ", ".join(f"{k}={repr(v)}" for k, v in sorted(inp.items()))
    return f"{fn_name}({args})"


def _observed_options(outputs: dict) -> list[dict]:
    """
    후보 출력 → 선택지. 같은 출력은 하나로 접고, 득표수는 버린다.
    exception 출력은 타입만 선택지 값으로 (acceptance 가 타입만 고정 가능).
    """
    seen = {}
    for _, out in outputs.items():
        if out.startswith("EXC:"):
            typ = out[4:].split("(", 1)[0]
            key = ("exception", typ)
            seen.setdefault(key, {"kind": "exception", "value": typ, "display": f"raises {typ}"})
        else:
            key = ("value", out)
            seen.setdefault(key, {"kind": "value", "value": out, "display": out})
    opts = sorted(seen.values(), key=lambda o: (o["kind"], o["display"]))
    return opts


def make_question(task: dict, rep: dict, round_no: int, run_id: str) -> dict:
    """rep = representative.select() 결과. representative 가 None 이면 ValueError."""
    r = rep["representative"]
    if r is None:
        raise ValueError("no disagreement to ask about")
    fn = task["function_name"]

    observed = _observed_options(r["outputs"])
    options = []
    for i, o in enumerate(observed):
        options.append({"option_id": f"o{i + 1}", **o})
    options += [
        {"option_id": "other_value", "kind": "value", "value": None, "display": "a different value (type it)"},
        {"option_id": "other_exception", "kind": "exception", "value": None, "display": "it should raise a different exception (name it)"},
        {"option_id": "unknown", "kind": "unknown", "value": None, "display": "not sure / decide later"},
    ]

    q = {
        "question_id": f"q{round_no:02d}",
        "round": round_no,
        "run_id": run_id,
        "function_name": fn,
        "representative": {"input": r["input"], "call": _call_repr(fn, r["input"])},
        "extra_examples": [{"input": e["input"], "call": _call_repr(fn, e["input"]),
                            "n_distinct": len(set(e["outputs"].values()))} for e in rep["extra_examples"]],
        "n_disagreeing": rep["n_disagreeing"],
        "n_valid_probes": rep["n_valid_probes"],
        "options": options,
        "question_sha256": ledger.question_hash(r["input"], options),
        "question_version": QUESTION_VERSION,
    }
    return q


def render(q: dict) -> str:
    """CLI 표시용 텍스트. 모델명·득표수 없음."""
    lines = [
        f"[Round {q['round']}] Candidates disagree on:",
        f"    {q['representative']['call']}",
        "",
        f"Observed results ({q['n_disagreeing']} of {q['n_valid_probes']} probes disagree):",
    ]
    for o in q["options"]:
        lines.append(f"    [{o['option_id']}] {o['display']}")
    if q["extra_examples"]:
        lines += ["", "Also differs at:"]
        for e in q["extra_examples"]:
            lines.append(f"    {e['call']}")
    lines += ["", "Which behavior is correct?"]
    return "\n".join(lines)
