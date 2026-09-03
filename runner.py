#!/usr/bin/env python3
"""
runner.py — Behavioral Disagreement Gate / Runner  (v0.4.0, task-driven)

역할: 실행하고 관측한다. 판정은 절대 하지 않는다.
입력: tasks/<task>.json  (게이트 영역. gold 정보 없음)
출력: runs/<run_id>/trace.jsonl (append-only), runs/<run_id>/manifest.json

관측 인코딩:
  - 함수가 값을 반환하면  return_value_raw = repr(value)
  - 함수가 예외를 던지면  return_value_raw = "EXC:<ExceptionType>"  (harness 가 잡음, exit 0)
  - harness 밖 오류(SyntaxError 등) 는 exit != 0
"""
import argparse
import datetime as dt
import hashlib
import json
import pathlib
import random
import subprocess
import sys
import time

RUNNER_VERSION = "0.5.0"
TRACE_SCHEMA_VERSION = 2
ENVELOPE = "@@BDG@@"
EXEC_TIMEOUT_S = 5
MAX_MUTANTS_PER_CANDIDATE = 8

# 단순 토큰 치환 뮤테이션 (첫 등장 1회). advisory G2 용.
MUTATION_OPERATORS = [
    ("plus_to_minus", " + ", " - "),
    ("minus_to_plus", " - ", " + "),
    ("mul_to_div", " * ", " / "),
    ("div_to_mul", " / ", " * "),
    ("le_to_lt", "<=", "<"),
    ("ge_to_gt", ">=", ">"),
    ("eq_to_ne", "==", "!="),
    ("and_to_or", " and ", " or "),
    ("or_to_and", " or ", " and "),
    ("const_half", "0.5", "0.0"),
    ("floor_to_ceil", "math.floor", "math.ceil"),
    ("ceil_to_floor", "math.ceil", "math.floor"),
    ("round_to_int", "round(", "int("),
    ("idx0_to_neg1", "[0]", "[-1]"),
    ("true_to_false", "True", "False"),
    ("rev_to_fwd", "[::-1]", "[::1]"),
    ("lower_to_upper", ".lower()", ".upper()"),
    ("reverse_flag", "reverse=True", "reverse=False"),
    ("x100_to_x10", "* 100", "* 10"),
    ("lenpar_plus1", "len(", "(1+len("),
]


# ---------------------------------------------------------------------------
def sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class TraceWriter:
    def __init__(self, path: pathlib.Path, run_id: str):
        self.run_id, self.seq = run_id, 0
        self.fh = open(path, "w", encoding="utf-8")

    def write(self, kind: str, **fields):
        rec = {"run_id": self.run_id, "seq": self.seq, "ts": now_iso(), "kind": kind}
        rec.update(fields)
        self.fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.fh.flush()
        self.seq += 1

    def close(self):
        self.fh.close()


# ---------------------------------------------------------------------------
# 실행 harness
# ---------------------------------------------------------------------------
def execute(source: str, fn_name: str, args: dict) -> dict:
    """후보 소스를 subprocess 에서 실행. 관측 원본만 반환. 해석 없음."""
    args_json = json.dumps(args)
    code = (
        source
        + "\nimport json as _json\n"
        + f"_args = _json.loads({json.dumps(args_json)})\n"
        + "try:\n"
        + f"    _r = {fn_name}(**_args)\n"
        + f"    print({ENVELOPE!r} + _json.dumps({{'kind': 'ret', 'repr': repr(_r)}}))\n"
        + "except Exception as _e:\n"
        + f"    print({ENVELOPE!r} + _json.dumps({{'kind': 'exc', 'type': type(_e).__name__,\n"
        + "        'message': str(_e), 'args': [repr(_a) for _a in _e.args]}))\n"
    )
    t0 = time.perf_counter()
    timed_out = False
    try:
        p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           timeout=EXEC_TIMEOUT_S)
        stdout, stderr, rc = p.stdout, p.stderr, p.returncode
    except subprocess.TimeoutExpired as e:
        timed_out = True
        stdout = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        stderr = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
        rc = -1
    dur = int((time.perf_counter() - t0) * 1000)
    # envelope 파싱: 마지막 envelope 줄만 신뢰. 후보가 찍은 print 는 stdout_raw 에 그대로 남음
    env_line = next((l for l in reversed(stdout.splitlines()) if l.startswith(ENVELOPE)), None)
    ret_raw, exc_type, exc_msg, exc_args, capture_ok = "", None, None, None, False
    if env_line is not None:
        try:
            env = json.loads(env_line[len(ENVELOPE):])
            capture_ok = True
            if env.get("kind") == "ret":
                ret_raw = env["repr"]
            else:
                exc_type, exc_msg, exc_args = env["type"], env["message"], env["args"]
                ret_raw = "EXC:" + exc_type          # v1 호환 표현 (타입만)
        except (ValueError, KeyError):
            capture_ok = False
    return {"stdout_raw": stdout, "stderr_raw": stderr, "exit_code": rc, "duration_ms": dur,
            "timed_out": timed_out, "oom": False, "files_written": [],
            "return_value_raw": ret_raw, "capture_ok": capture_ok,
            "exception_type": exc_type, "exception_message_raw": exc_msg, "exception_args_raw": exc_args,
            "stdout_bytes": len(stdout.encode("utf-8")), "stderr_bytes": len(stderr.encode("utf-8")),
            "output_truncated": False}


def expected_repr(t: dict) -> str:
    if "expected_exc" in t:
        return "EXC:" + t["expected_exc"]
    return repr(t["expected"])


def values_match(got: str, exp: str) -> bool:
    """테스트 판정용 관대한 비교 (3 vs 3.0, [8,4] vs [8.0,4.0] 동일 취급)."""
    if got == exp:
        return True
    try:
        return abs(float(got) - float(exp)) <= 1e-9
    except ValueError:
        pass
    try:
        return json.loads(got) == json.loads(exp)
    except (ValueError, TypeError):
        return False


def run_test_suite(source: str, fn_name: str, tests: list) -> tuple[str, int, bool]:
    """outcome ∈ passed / failed / error / timeout (관측. killed 같은 해석 금지)"""
    t0 = time.perf_counter()
    outcome = "passed"
    for t in tests:
        obs = execute(source, fn_name, t["args"])
        if obs["timed_out"]:
            outcome = "timeout"; break
        if obs["exit_code"] != 0:
            outcome = "error"; break
        if not values_match(obs["return_value_raw"], expected_repr(t)):
            outcome = "failed"; break
    return outcome, int((time.perf_counter() - t0) * 1000), outcome == "timeout"


# ---------------------------------------------------------------------------
# probe 생성 (과제 스펙 기반, 결정론)
# ---------------------------------------------------------------------------
def sample_arg(rng: random.Random, spec: dict):
    t = spec["type"]
    if t == "float":
        return round(rng.uniform(spec["min"], spec["max"]), spec.get("decimals", 6))
    if t == "int":
        return rng.randint(spec["min"], spec["max"])
    if t == "bool":
        return rng.random() < 0.5
    if t == "str":
        n = rng.randint(spec["min_len"], spec["max_len"])
        return "".join(rng.choice(spec["alphabet"]) for _ in range(n))
    if t == "list_int":
        n = rng.randint(spec["min_len"], spec["max_len"])
        return [rng.randint(spec["min"], spec["max"]) for _ in range(n)]
    if t == "list_float":
        n = rng.randint(spec["min_len"], spec["max_len"])
        return [round(rng.uniform(spec["min"], spec["max"]), spec.get("decimals", 2)) for _ in range(n)]
    raise ValueError(f"unknown arg type {t}")


def gen_probes(task: dict, seed: int, budget: int) -> list[dict]:
    """
    mixed_v2: (1-f)*budget 무작위 + f*budget 경계값.
    경계값이 f*budget 보다 적으면 전부 포함. 같은 seed/budget → 같은 집합.
    """
    p = task["probe"]
    rng = random.Random(seed)
    n_b = max(1, int(round(budget * p.get("boundary_fraction", 0.1))))
    bcases = list(p.get("boundary_cases", []))
    chosen_b = rng.sample(bcases, n_b) if len(bcases) > n_b else bcases
    n_r = max(0, budget - len(chosen_b))
    probes = [{a["name"]: sample_arg(rng, a) for a in p["args"]} for _ in range(n_r)]
    probes += chosen_b
    rng.shuffle(probes)
    return probes


def make_mutants(source: str) -> list[dict]:
    out = []
    for op, a, b in MUTATION_OPERATORS:
        idx = source.find(a)
        if idx < 0:
            continue
        line = source.count("\n", 0, idx) + 1
        col = idx - (source.rfind("\n", 0, idx) + 1)
        out.append({"operator": op, "original_token": a, "mutated_token": b, "line": line, "col": col,
                    "mutated_source": source[:idx] + b + source[idx + len(a):]})
        if len(out) >= MAX_MUTANTS_PER_CANDIDATE:
            break
    return out


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", default="tasks/round_half.json")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--probe-seed", type=int, default=7)
    ap.add_argument("--probe-budget", type=int, default=100)
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--root", default="runs")
    ap.add_argument("--gen", choices=["hardcoded", "llm"], default="hardcoded")
    ap.add_argument("--n-candidates", type=int, default=3)
    ap.add_argument("--model", default="nvidia/Nemotron-3-Ultra-550b-a55b")
    ap.add_argument("--models", default=None, help="comma-separated; slot i uses models[i %% len]")
    ap.add_argument("--temperature", type=float, default=0.8)
    ap.add_argument("--gen-seed", type=int, default=1001)
    ap.add_argument("--diversity", choices=["none", "prompt_jitter"], default="none")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    task_path = pathlib.Path(args.task)
    task_text = task_path.read_text(encoding="utf-8")
    task = json.loads(task_text)
    fn = task["function_name"]

    run_id = args.run_id or ("r_" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S")
                             + "_" + sha256(str(time.time()))[:6])
    run_dir = pathlib.Path(args.root) / run_id
    (run_dir / "prompts").mkdir(parents=True, exist_ok=True)
    (run_dir / "patches").mkdir(parents=True, exist_ok=True)
    tw = TraceWriter(run_dir / "trace.jsonl", run_id)
    wall0 = time.perf_counter()

    # Grader 가 trace 를 읽지 않고도 과제를 알 수 있도록 (gold 정보 없음)
    (run_dir / "manifest.json").write_text(json.dumps({
        "run_id": run_id, "task_id": task["task_id"], "task_file": str(task_path),
        "task_file_hash": sha256(task_text), "function_name": fn}, indent=2), encoding="utf-8")

    tw.write("run_header",
             trace_schema_version=TRACE_SCHEMA_VERSION,
             task_id=task["task_id"], task_source="local", task_file_hash=sha256(task_text),
             ambiguity_axis=task.get("ambiguity_axis"),
             base_checkpoint="local:" + sha256(task["issue_text"])[:16],
             base_source_hash=sha256(""), git_stripped=True, network_blocked=False,
             execution_env="local_subprocess",
             gate_code_hash=sha256(pathlib.Path(__file__).read_text(encoding="utf-8")),
             runner_version=RUNNER_VERSION, python_version=sys.version.split()[0],
             generation_mode=args.gen + ("(dry-run)" if args.dry_run else ""))

    # --- candidate -----------------------------------------------------------
    if args.gen == "llm":
        import llm_gen
        (run_dir / "llm_responses").mkdir(exist_ok=True)
        gens, gevents = llm_gen.generate_candidates(
            task=task, models=(args.models.split(",") if args.models else args.model),
            n=args.n_candidates, temperature=args.temperature, base_seed=args.gen_seed,
            jitter=(args.diversity == "prompt_jitter"), dry_run=args.dry_run)
        cand_map = {}
        for i, g in enumerate(gens):
            cid = f"c{i + 1}"
            cand_map[cid] = {"source": g["source"], "agent_tests": []}
            ph = sha256(g["source"])
            (run_dir / "patches" / f"{ph}.diff").write_text(g["source"], encoding="utf-8")
            (run_dir / "prompts" / f"{g['prompt_hash']}.txt").write_text(g["prompt_text"], encoding="utf-8")
            rref = f"llm_responses/{cid}.txt"
            (run_dir / rref).write_text(g["response_text"], encoding="utf-8")
            tw.write("candidate", candidate_id=cid, patch_hash=ph, patch_ref=f"patches/{ph}.diff",
                     model=g["model"], model_version=g["served_model"], temperature=g["temperature"],
                     seed=g["seed"], prompt_hash=g["prompt_hash"], prompt_ref=f"prompts/{g['prompt_hash']}.txt",
                     response_ref=rref, gen_duration_ms=g["gen_duration_ms"],
                     tokens_in=g["tokens_in"], tokens_out=g["tokens_out"])
        for ev in gevents:
            tw.write("infra_event", scope="generation", candidate_id=None, **ev)
    else:
        fixtures = task.get("fixture_candidates")
        if not fixtures:
            raise SystemExit(f"task {task['task_id']} has no fixture_candidates; use --gen llm")
        prompt_text = "ISSUE:\n" + task["issue_text"] + "\n\n(fixture candidates, no LLM)\n"
        prompt_hash = sha256(prompt_text)
        (run_dir / "prompts" / f"{prompt_hash}.txt").write_text(prompt_text, encoding="utf-8")
        cand_map = {cid: {"source": c["source"], "agent_tests": c.get("agent_tests", [])}
                    for cid, c in fixtures.items()}
        for cid, c in cand_map.items():
            ph = sha256(c["source"])
            (run_dir / "patches" / f"{ph}.diff").write_text(c["source"], encoding="utf-8")
            tw.write("candidate", candidate_id=cid, patch_hash=ph, patch_ref=f"patches/{ph}.diff",
                     model="fixture/hardcoded", model_version="n/a", temperature=0.0, seed=0,
                     prompt_hash=prompt_hash, prompt_ref=f"prompts/{prompt_hash}.txt",
                     gen_duration_ms=0, tokens_in=0, tokens_out=0)

    # --- probe ----------------------------------------------------------------
    probes = gen_probes(task, args.probe_seed, args.probe_budget)
    probe_ids = []
    for i, inp in enumerate(probes):
        pid = f"p{i:03d}"
        probe_ids.append((pid, inp))
        tw.write("probe", probe_id=pid, generator=task["probe"]["generator"],
                 generator_seed=args.probe_seed, generator_budget=args.probe_budget,
                 input_hash=sha256(json.dumps(inp, sort_keys=True)), input=inp,
                 input_domain=task["probe"]["domain"])

    # --- job_plan: 실행 전 전체 작업 목록 확정 (누락·중복·바꿔치기 검출용) -------
    mut_plan = {cid: make_mutants(c["source"]) for cid, c in cand_map.items()}
    job_ids = []
    for pid, _ in probe_ids:
        for cid in cand_map:
            for r in range(args.repeats):
                job_ids.append(f"obs:{pid}:{cid}:{r}")
    for cid, muts in mut_plan.items():
        for j, _ in enumerate(muts):
            for suite in ("base", "agent"):
                job_ids.append(f"mut:{cid}_m{j:02d}:{suite}")
    for cid in cand_map:
        for t in task["r_gate"]:
            job_ids.append(f"reg:{cid}:{t['test_id']}")
    planned = {"observation": sum(1 for j in job_ids if j.startswith("obs:")),
               "mutant_test_result": sum(1 for j in job_ids if j.startswith("mut:")),
               "regression_result": sum(1 for j in job_ids if j.startswith("reg:"))}
    tw.write("job_plan", planned_counts=planned, n_jobs=len(job_ids),
             job_manifest_sha256=sha256("\n".join(sorted(job_ids))), repeats=args.repeats)

    # --- observation ----------------------------------------------------------
    n_obs = 0
    for pid, inp in probe_ids:
        for cid, c in cand_map.items():
            for r in range(args.repeats):
                obs = execute(c["source"], fn, inp)
                tw.write("observation", job_id=f"obs:{pid}:{cid}:{r}",
                         probe_id=pid, candidate_id=cid, repeat_idx=r, **obs)
                n_obs += 1

    # --- mutant / mutant_test_result ------------------------------------------
    n_mut = 0
    for cid, c in cand_map.items():
        muts = mut_plan[cid]
        if not muts:
            tw.write("infra_event", event="zero_applicable_mutants", scope="candidate",
                     candidate_id=cid, detail="no operator applicable")
            continue
        for j, m in enumerate(muts):
            mid = f"{cid}_m{j:02d}"
            n_mut += 1
            tw.write("mutant", mutant_id=mid, candidate_id=cid, operator=m["operator"],
                     file=f"{fn}.py", line=m["line"], col=m["col"],
                     original_token=m["original_token"], mutated_token=m["mutated_token"],
                     in_changed_region=True)
            for suite, tests in (("base", task["r_gate"]), ("agent", c["agent_tests"])):
                outcome, dur, to = run_test_suite(m["mutated_source"], fn, tests)
                tw.write("mutant_test_result", job_id=f"mut:{mid}:{suite}", mutant_id=mid,
                         candidate_id=cid, suite=suite, outcome=outcome, duration_ms=dur,
                         timed_out=to, error_type=None)

    # --- regression_result (R_gate) -------------------------------------------
    for cid, c in cand_map.items():
        for t in task["r_gate"]:
            obs = execute(c["source"], fn, t["args"])
            if obs["timed_out"]:
                outcome = "timeout"
            elif obs["exit_code"] != 0:
                outcome = "error"
            elif values_match(obs["return_value_raw"], expected_repr(t)):
                outcome = "passed"
            else:
                outcome = "failed"
            tw.write("regression_result", job_id=f"reg:{cid}:{t['test_id']}", candidate_id=cid,
                     suite="R_gate", test_id=t["test_id"], outcome=outcome, duration_ms=obs["duration_ms"])

    tw.write("run_footer", completed=True, n_candidates=len(cand_map), n_probes=len(probe_ids),
             n_observations=n_obs, n_mutants=n_mut, wall_clock_ms=int((time.perf_counter() - wall0) * 1000))
    tw.close()
    print(f"run_id={run_id}")
    print(f"task={task['task_id']}")
    print(f"trace={run_dir / 'trace.jsonl'}")
    print(f"records={tw.seq}")


if __name__ == "__main__":
    main()
