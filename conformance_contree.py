#!/usr/bin/env python3
"""
conformance_contree.py — ConTree SDK 적합성 검사  (v0.1.0)

ContreeBackend 가 의존하는 SDK 동작 8가지를 실제로 실행해 확인한다.
contree-sdk 는 Pre-Alpha (0.3.6 고정). 버전이 바뀌면 이 스크립트를 먼저 돌려 파괴적 변경을 감지한다.

usage: python conformance_contree.py [--image python:3.12-slim]
exit 0 = 전부 통과 / 1 = 실패 있음
"""
import argparse
import hashlib
import json
import pathlib
import sys
import tempfile
import time

from backends import BDG_HARNESS, ENVELOPE, parse_envelope, RawResult

CHECKS = []


def check(name):
    def deco(fn):
        CHECKS.append((name, fn)); return fn
    return deco


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


# ---------------------------------------------------------------------------
@check("1 auth + images.use(tag) resolves")
def c1(ctx):
    from contree_sdk import ContreeSync
    ctx["client"] = ContreeSync()
    ctx["img"] = ctx["client"].images.use(ctx["image"])
    assert ctx["img"] is not None
    return f"image={ctx['image']}"


@check("2 disposable=False keeps uuid; default(True) discards")
def c2(ctx):
    base = ctx["img"].run(shell="mkdir -p /work", disposable=False, timeout=60).wait()
    assert getattr(base, "uuid", None), "disposable=False run has no uuid"
    d = ctx["img"].run(shell="true", timeout=60).wait()
    assert getattr(d, "uuid", None) is None, "default run unexpectedly kept uuid (API changed?)"
    ctx["base"] = base
    json.dumps(str(base.uuid))   # str() 로 JSON 직렬화 가능해야 함
    return f"uuid type={type(base.uuid).__name__} base uuid={str(base.uuid)[:8]}…  disposable uuid={d.uuid}"


@check("3 branch isolation (A/B do not see each other)")
def c3(ctx):
    b = ctx["base"]
    a = b.run(shell="echo A > /work/f.txt", disposable=False, timeout=60).wait()
    c = b.run(shell="echo C > /work/f.txt", disposable=False, timeout=60).wait()
    oa = a.run(shell="cat /work/f.txt", timeout=30).wait().stdout.strip()
    oc = c.run(shell="cat /work/f.txt", timeout=30).wait().stdout.strip()
    assert oa == "A" and oc == "C", f"leak: a={oa!r} c={oc!r}"
    assert str(a.uuid) != str(c.uuid) != str(b.uuid)
    return f"a={oa} c={oc} uuids distinct"


@check("4 files= upload + sha256 verified in node")
def c4(ctx):
    from contree_sdk.utils.models.file import UploadFileSpec
    tmp = pathlib.Path(tempfile.mkdtemp())
    src = "def f(x):\n    return x * 2\n"
    (tmp / "impl.py").write_text(src, encoding="utf-8", newline="\n")
    assert b"\r" not in (tmp / "impl.py").read_bytes(), "CRLF leaked into upload file"
    node = ctx["base"].run(shell="mkdir -p /work/h1", files=[UploadFileSpec(source=str(tmp / "impl.py"), path="/work/h1/impl.py")],
                           disposable=False, timeout=60).wait()
    chk = node.run(command="sha256sum", args=["/work/h1/impl.py"], timeout=30).wait()
    remote = (chk.stdout or "").split()[0]
    assert remote == sha256(src), f"sha mismatch {remote[:8]} vs {sha256(src)[:8]}"
    ctx["node_h1"], ctx["tmp"] = node, tmp
    return f"sha256 match {remote[:12]}…"


@check("5 command/args list form (no shell) with python")
def c5(ctx):
    r = ctx["base"].run(command="python", args=["-c", "print(1+1)"], timeout=30).wait()
    assert r.exit_code == 0 and (r.stdout or "").strip() == "2", f"exit={r.exit_code} out={r.stdout!r}"
    return "python -c via args OK"


@check("6 result attrs (stdout/stderr/exit_code/state) + non-zero exit propagates")
def c6(ctx):
    r = ctx["base"].run(command="python", args=["-c", "import sys; sys.stderr.write('E\\n'); sys.exit(3)"], timeout=30).wait()
    for a in ("stdout", "stderr", "exit_code", "state"):
        assert hasattr(r, a), f"missing attr {a}"
    assert r.exit_code == 3, f"exit_code={r.exit_code}"
    assert "E" in (r.stderr or ""), f"stderr={r.stderr!r}"
    return f"exit_code=3 state={r.state}"


@check("7 harness end-to-end: envelope + exec_id echo")
def c7(ctx):
    assert "node_h1" in ctx, "skipped: depends on check 4"
    from contree_sdk.utils.models.file import UploadFileSpec
    (ctx["tmp"] / "bdg_harness.py").write_text(BDG_HARNESS, encoding="utf-8", newline="\n")
    node = ctx["node_h1"].run(shell="true", files=[UploadFileSpec(source=str(ctx["tmp"] / "bdg_harness.py"), path="/work/bdg_harness.py")],
                              disposable=False, timeout=60).wait()
    r = node.run(command="python", args=["/work/bdg_harness.py", "f", "obs:p0:c1:0", json.dumps({"x": 21}), "/work/h1/impl.py"],
                 timeout=30).wait()
    res = parse_envelope(r.stdout or "", "obs:p0:c1:0", RawResult(exit_code=r.exit_code))
    assert r.exit_code == 0 and res.capture_ok and res.job_id_echo_ok and res.return_value_raw == "42", \
        f"exit={r.exit_code} capture={res.capture_ok} echo={res.job_id_echo_ok} val={res.return_value_raw!r} out={r.stdout!r}"
    return "envelope OK, echo OK, f(21)=42"


@check("8 timeout param honored (sleep 10 with timeout 2)")
def c8(ctx):
    t0 = time.perf_counter()
    try:
        r = ctx["base"].run(command="python", args=["-c", "import time; time.sleep(10)"], timeout=2).wait()
        st = str(getattr(r, "state", "")).upper()
        assert r.exit_code == -1 or "TIME" in st, f"no timeout signal: exit={r.exit_code} state={st}"
        sig = f"state={st} exit={r.exit_code} (timeout signal = exit -1)"
    except Exception as e:  # noqa: BLE001 — SDK 가 예외로 알리는 경우도 허용
        sig = f"exception {type(e).__name__}"
    dt = time.perf_counter() - t0
    assert dt < 9, f"took {dt:.1f}s — timeout not applied"
    return f"{sig} in {dt:.1f}s"


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default="python:3.12-slim")
    args = ap.parse_args()
    ctx = {"image": args.image}
    try:
        import contree_sdk
        print(f"contree-sdk version: {getattr(contree_sdk, '__version__', 'unknown')}")
    except Exception as e:  # noqa: BLE001
        print("contree_sdk import failed:", e); sys.exit(1)

    fails = 0
    for name, fn in CHECKS:
        try:
            msg = fn(ctx)
            print(f"  PASS  {name:58s} {msg}")
        except Exception as e:  # noqa: BLE001
            fails += 1
            print(f"  FAIL  {name:58s} {type(e).__name__}: {e}")
            if name.startswith(("1", "2")):
                print("  (fatal: later checks depend on this)"); break
    print(f"\n{len(CHECKS) - fails}/{len(CHECKS)} passed")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
