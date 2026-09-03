#!/usr/bin/env python3
"""
loop.py — 명료화 루프 / 오케스트레이션  (v0.1.0)

한 세션 = 과제 1개 = 라운드 최대 N회.
  라운드 r:
    1. seed_schedule[r]  (세션 시작 시 확정. L5)
    2. ledger 활성 뷰 → acceptance 컴파일 → 파생 task 의 r_gate 에 합류 (F절)
       + confirmed_behaviors 를 파생 task 에 기록 → 재수리 프롬프트로 (E절)
    3. runner → 새 run_id  /  scorer → verdict
    4. PASS → 종료. UNVERIFIABLE/CODE_INCOMPLETE → 종료.
    5. representative → question → question_sha256 비교 (NO_PROGRESS)
    6. provider.ask → parse (실패 시 raw 만 기록, 재질문 최대 2회) → decision
    7. 활성 뷰 모순 → LEDGER_CONFLICT
종료: PASS | MAX_ROUNDS_EXCEEDED | NO_PROGRESS | LEDGER_CONFLICT | DEFERRED | UNVERIFIABLE | CODE_INCOMPLETE

산출물:
  ledger/<session>/ledger.jsonl
  clarify/<session>/round_<n>.task.json / .question.json / .verdict.json
  runs/<session>_r<n>/   (라운드마다 1개)
"""
import argparse
import hashlib
import json
import pathlib
import subprocess
import sys

import acceptance
import ledger as ledgermod
import question as qmod
import representative
import scorer
from providers import CliAnswerProvider, ScriptedAnswerProvider

LOOP_VERSION = "0.1.0"
MAX_PARSE_RETRY = 2


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
def build_round_task(task: dict, active: dict, round_no: int) -> dict:
    """원본 task + 확정 사례(acceptance→r_gate, confirmed_behaviors→프롬프트) + 라운드별 fixture."""
    fn = task["function_name"]
    derived = json.loads(json.dumps(task))
    tests = acceptance.compile_acceptance(active, fn)
    # 시그니처 검사: decision 의 input 키가 과제 인자와 일치해야 함
    arg_names = {a["name"] for a in task["probe"]["args"]}
    for t in tests:
        if set(t["args"]) != arg_names:
            raise ValueError(f"decision input keys {sorted(t['args'])} != task args {sorted(arg_names)}")
    derived["r_gate"] = list(task["r_gate"]) + tests
    derived["confirmed_behaviors"] = acceptance.render_confirmed_lines(active, fn)
    rounds = task.get("fixture_rounds")
    if rounds:
        derived["fixture_candidates"] = rounds.get(str(round_no)) or rounds[max(rounds, key=int)]
    derived.pop("fixture_rounds", None)
    derived["derived_from_round"] = round_no
    return derived


def run_round(task_path: pathlib.Path, run_id: str, probe_seed: int, args) -> tuple[list, dict]:
    cmd = [sys.executable, "runner.py", "--task", str(task_path), "--run-id", run_id,
           "--probe-seed", str(probe_seed), "--probe-budget", str(args.probe_budget),
           "--repeats", str(args.repeats), "--gen", args.gen]
    if args.gen == "llm":
        cmd += ["--n-candidates", str(args.n_candidates), "--temperature", str(args.temperature),
                "--gen-seed", str(args.gen_seed)]
        if args.models:
            cmd += ["--models", args.models]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"runner failed:\n{p.stderr[-800:]}")
    trace = [json.loads(l) for l in open(f"runs/{run_id}/trace.jsonl", encoding="utf-8")]
    cfg = json.load(open(args.cfg, encoding="utf-8"))
    return trace, scorer.score(trace, cfg), cfg


def answer_to_expected(q: dict, ans: dict) -> tuple[dict | None, str | None]:
    """(expected, parse_error). unknown → (None, None)."""
    opt = next((o for o in q["options"] if o["option_id"] == ans["option_id"]), None)
    if opt is None:
        return None, f"unknown option {ans['option_id']}"
    try:
        if opt["kind"] == "unknown":
            return None, None
        if opt["option_id"] == "other_value":
            return acceptance.expected_from_answer("value", ans.get("raw_input") or ""), None
        if opt["option_id"] == "other_exception":
            return acceptance.expected_from_answer("exception", ans.get("raw_input") or ""), None
        # 관측된 선택지: 표시 값 자체를 검증 통과시켜야 함 (inf/nan 등은 여기서 걸림)
        return acceptance.expected_from_answer(opt["kind"], opt["value"]), None
    except acceptance.ParseError as e:
        return None, str(e)


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True)
    ap.add_argument("--session", required=True)
    ap.add_argument("--max-rounds", type=int, default=5)
    ap.add_argument("--session-seed", type=int, default=42)
    ap.add_argument("--probe-budget", type=int, default=40)
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--cfg", default="cfg_full.json")
    ap.add_argument("--gen", choices=["hardcoded", "llm"], default="hardcoded")
    ap.add_argument("--n-candidates", type=int, default=3)
    ap.add_argument("--temperature", type=float, default=0.8)
    ap.add_argument("--gen-seed", type=int, default=1001)
    ap.add_argument("--models", default=None)
    ap.add_argument("--answers", default=None, help="scripted answers JSON (list). omit → CLI")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    task = json.load(open(args.task, encoding="utf-8"))
    sid = args.session
    cdir = pathlib.Path("clarify") / sid
    cdir.mkdir(parents=True, exist_ok=True)
    provider = ScriptedAnswerProvider(json.load(open(args.answers, encoding="utf-8")), echo=not args.quiet) \
        if args.answers else CliAnswerProvider()

    L = ledgermod.Ledger(pathlib.Path("ledger") / sid / "ledger.jsonl", sid)
    schedule = ledgermod.make_seed_schedule(args.session_seed, args.max_rounds)
    L.append("session_header", task_id=task["task_id"], task_file_sha256=sha256(open(args.task, encoding="utf-8").read()),
             max_rounds=args.max_rounds, session_seed=args.session_seed, seed_schedule=schedule,
             provider=provider.label, gen=args.gen, loop_version=LOOP_VERSION)

    outcome, prev_qhash, final_run = None, None, None
    for r in range(1, args.max_rounds + 1):
        view = L.view()
        if view["conflicts"]:
            outcome = "LEDGER_CONFLICT"; break
        derived = build_round_task(task, view["active"], r)
        tpath = cdir / f"round_{r}.task.json"
        tpath.write_text(json.dumps(derived, ensure_ascii=False, indent=2), encoding="utf-8")

        run_id = f"{sid}_r{r}"
        trace, verdict, cfg = run_round(tpath, run_id, schedule[r - 1], args)
        final_run = run_id
        (cdir / f"round_{r}.verdict.json").write_text(json.dumps(verdict, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        if not args.quiet:
            print(f"\n=== round {r}  seed={schedule[r-1]}  acceptance={len(derived['r_gate']) - len(task['r_gate'])}  → {verdict['status']} {verdict['reason_codes']}")

        if verdict["status"] == "PASS":
            outcome = "PASS"; break
        if verdict["status"] in ("UNVERIFIABLE", "CODE_INCOMPLETE"):
            outcome = verdict["status"]; break

        rep = representative.select(trace, verdict, cfg)
        q = qmod.make_question(task, rep, r, run_id)
        (cdir / f"round_{r}.question.json").write_text(json.dumps(q, ensure_ascii=False, indent=2), encoding="utf-8")
        L.append("question", round=r, run_id=run_id, question_id=q["question_id"],
                 representative=q["representative"], extra_examples=q["extra_examples"],
                 options=q["options"], question_sha256=q["question_sha256"])
        if q["question_sha256"] == prev_qhash:
            outcome = "NO_PROGRESS"; break
        prev_qhash = q["question_sha256"]

        expected, decided = None, False
        for attempt in range(MAX_PARSE_RETRY + 1):
            ans = provider.ask(q)
            expected, perr = answer_to_expected(q, ans)
            L.append("answer", round=r, question_id=q["question_id"], option_id=ans["option_id"],
                     answer_kind=(expected or {}).get("kind", "unknown"), raw_input=ans.get("raw_input"),
                     parsed=(expected if expected else None), parse_error=perr, attempt=attempt)
            if ans["option_id"] == "unknown":
                L.append("defer", round=r, question_id=q["question_id"])
                outcome = "DEFERRED"; break
            if perr is None and expected is not None:
                decided = True; break
            if not args.quiet:
                print(f"  ! answer rejected: {perr}")
        if outcome == "DEFERRED":
            break
        if not decided:
            outcome = "DEFERRED"; L.append("defer", round=r, question_id=q["question_id"], reason="parse failures"); break

        L.append("decision", decision_id=f"d{r:02d}", round=r, input=q["representative"]["input"],
                 expected=expected, question_id=q["question_id"])
        if L.view()["conflicts"]:
            outcome = "LEDGER_CONFLICT"; break
    else:
        outcome = "MAX_ROUNDS_EXCEEDED"

    view = L.view()
    L.append("session_footer", outcome=outcome, rounds_used=r, final_run_id=final_run,
             n_active_decisions=len(view["active"]), active_view_sha256=ledgermod.active_view_hash(view))
    print(f"\nSESSION {sid}: {outcome}  rounds={r}  decisions={len(view['active'])}  final_run={final_run}")
    print(f"  ledger: ledger/{sid}/ledger.jsonl")
    sys.exit(0 if outcome == "PASS" else 3)


if __name__ == "__main__":
    main()
