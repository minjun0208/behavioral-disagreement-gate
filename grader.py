#!/usr/bin/env python3
"""
grader.py — Behavioral Disagreement Gate / Grader  (v0.2.0)

역할: gold/<task_id>.json (T_meta, R_meta) 로 후보를 채점한다.
금지: runs/<run_id>/trace.jsonl 을 읽지 않는다.
읽는 것: runs/<run_id>/manifest.json (task_id 만), runs/<run_id>/patches/*.diff, tasks/<id>.json (R_gate disjoint 검사용)
출력: grades/<run_id>/grade.jsonl, grades/<run_id>/canary.txt
"""
import datetime as dt
import hashlib
import json
import pathlib
import secrets
import subprocess
import sys

GRADER_VERSION = "0.2.0"


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def expected_repr(t: dict) -> str:
    return "EXC:" + t["expected_exc"] if "expected_exc" in t else repr(t["expected"])


def values_match(got: str, exp: str) -> bool:
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


def run_one(source: str, fn: str, args: dict) -> str:
    code = (source + "\nimport json as _json\n"
            + f"_args = _json.loads({json.dumps(json.dumps(args))})\n"
            + "try:\n" + f"    print(repr({fn}(**_args)))\n"
            + "except Exception as _e:\n    print('EXC:' + type(_e).__name__)\n")
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=5)
    if p.returncode != 0:
        return "<<error>>"
    return p.stdout.strip().splitlines()[-1] if p.stdout.strip() else ""


def main():
    if len(sys.argv) < 2:
        print("usage: grader.py <run_id> [--runs runs] [--grades grades] [--gold gold] [--tasks tasks]")
        sys.exit(2)
    run_id = sys.argv[1]
    def opt(flag, default):
        return pathlib.Path(sys.argv[sys.argv.index(flag) + 1]) if flag in sys.argv else pathlib.Path(default)
    runs_root, grades_root = opt("--runs", "runs"), opt("--grades", "grades")
    gold_root, tasks_root = opt("--gold", "gold"), opt("--tasks", "tasks")

    manifest = json.loads((runs_root / run_id / "manifest.json").read_text(encoding="utf-8"))
    task_id, fn = manifest["task_id"], manifest["function_name"]
    gold = json.loads((gold_root / f"{task_id}.json").read_text(encoding="utf-8"))
    task = json.loads((tasks_root / f"{task_id}.json").read_text(encoding="utf-8"))

    # G4 동어반복 방지: R_gate 입력과 gold 입력이 겹치면 채점 거부
    g_in = {json.dumps(t["args"], sort_keys=True) for t in task["r_gate"]}
    m_in = {json.dumps(t["args"], sort_keys=True) for t in gold["t_meta"] + gold["r_meta"]}
    if g_in & m_in:
        raise SystemExit(f"REFUSED: R_gate and gold share inputs for {task_id}: {sorted(g_in & m_in)}")

    patch_dir = runs_root / run_id / "patches"
    gdir = grades_root / run_id
    gdir.mkdir(parents=True, exist_ok=True)
    canary = "CANARY_" + secrets.token_hex(6) + "_DO_NOT_LEAK"
    (gdir / "canary.txt").write_text(canary + "\n", encoding="utf-8")

    seq = 0
    fh = open(gdir / "grade.jsonl", "w", encoding="utf-8")
    def write(kind, **f):
        nonlocal seq
        rec = {"run_id": run_id, "seq": seq,
               "ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
               "kind": kind}
        rec.update(f)
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        seq += 1

    write("grade_header", task_id=task_id,
          gold_suite_hash=sha256(json.dumps(gold["t_meta"], sort_keys=True)),
          meta_regression_suite_hash=sha256(json.dumps(gold["r_meta"], sort_keys=True)),
          canary=canary, grader_version=GRADER_VERSION)

    for p in sorted(patch_dir.glob("*.diff")):
        src, ph = p.read_text(encoding="utf-8"), p.stem
        for suite, tests in (("T_meta", gold["t_meta"]), ("R_meta", gold["r_meta"])):
            for t in tests:
                got = run_one(src, fn, t["args"])
                outcome = "error" if got == "<<error>>" else ("passed" if values_match(got, expected_repr(t)) else "failed")
                write("gold_result", patch_hash=ph, suite=suite, test_id=t["test_id"], outcome=outcome)

    fh.close()
    print(f"grade={gdir / 'grade.jsonl'}")
    print(f"canary_file={gdir / 'canary.txt'}")


if __name__ == "__main__":
    main()
