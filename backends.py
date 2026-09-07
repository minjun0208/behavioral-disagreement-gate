#!/usr/bin/env python3
"""
backends.py — 실행 백엔드  (v0.1.0)

Runner 는 백엔드를 통해서만 코드를 실행한다.
  Backend.prepare_candidate(handle_id, source, fn)  후보/뮤턴트별 1회
  Backend.execute_many(jobs, max_inflight)          병렬 실행, {exec_id: RawResult} 반환
  Backend.teardown()

규율:
  - 워커는 실행만 한다. trace 에 쓰지 않는다 (단일 writer 는 Runner).
  - 두 백엔드는 같은 harness 파일(BDG_HARNESS)을 실행한다 → envelope 형식 동일.
  - envelope 는 exec_id 를 echo 한다. 불일치면 capture_ok=False (결과 바꿔치기 방지).
  - SDK/인프라 예외는 결과로 위장하지 않는다: sandbox_error 필드 + exit_code=-2.
  - 폴백 없음. ContreeBackend 가 실패하면 실패로 기록된다.

ContreeBackend 는 실측된 SDK 동작(contree-sdk 0.3.6)에 맞춰 작성됨:
  images.use(tag).run(shell=..., files=[UploadFileSpec(source=..., path=...)], disposable=False).wait()
  result.uuid / .stdout / .stderr / .exit_code ;  result.run(...) 으로 분기
"""
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

BACKENDS_VERSION = "0.1.2"
ENVELOPE = "@@BDG@@"
EXEC_TIMEOUT_S = 5

# 두 백엔드 공용 harness. impl 파일을 import 해 함수를 호출하고 envelope 1줄을 출력한다.
BDG_HARNESS = r'''
import json, sys, importlib.util
fn_name, exec_id, args_json, impl_path = sys.argv[1:5]
ENV = "@@BDG@@"
spec = importlib.util.spec_from_file_location("bdg_impl", impl_path)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
args = json.loads(args_json)
try:
    r = getattr(m, fn_name)(**args)
    print(ENV + json.dumps({"job_id": exec_id, "kind": "ret", "repr": repr(r)}))
except Exception as e:
    print(ENV + json.dumps({"job_id": exec_id, "kind": "exc", "type": type(e).__name__,
                            "message": str(e), "args": [repr(a) for a in e.args]}))
'''.lstrip()


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


@dataclass
class ExecJob:
    exec_id: str
    handle_id: str
    args: dict


@dataclass
class RawResult:
    stdout_raw: str = ""
    stderr_raw: str = ""
    exit_code: int = -2
    duration_ms: int = 0
    timed_out: bool = False
    oom: bool = False
    files_written: list = field(default_factory=list)
    return_value_raw: str = ""
    capture_ok: bool = False
    exception_type: str | None = None
    exception_message_raw: str | None = None
    exception_args_raw: list | None = None
    stdout_bytes: int = 0
    stderr_bytes: int = 0
    output_truncated: bool = False
    node_uuid: str | None = None
    job_id_echo_ok: bool = False
    attempt: int = 0
    sandbox_error: str | None = None

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def parse_envelope(stdout: str, expected_exec_id: str, res: RawResult) -> RawResult:
    """마지막 envelope 줄만 신뢰. exec_id echo 불일치 → capture_ok=False."""
    res.stdout_bytes = len(stdout.encode("utf-8"))
    env_line = next((l for l in reversed(stdout.splitlines()) if l.startswith(ENVELOPE)), None)
    if env_line is None:
        return res
    try:
        env = json.loads(env_line[len(ENVELOPE):])
    except ValueError:
        return res
    res.job_id_echo_ok = (env.get("job_id") == expected_exec_id)
    if not res.job_id_echo_ok:
        res.capture_ok = False
        res.sandbox_error = f"job_id echo mismatch: got {env.get('job_id')!r}"
        return res
    res.capture_ok = True
    if env.get("kind") == "ret":
        res.return_value_raw = env["repr"]
    else:
        res.exception_type = env.get("type")
        res.exception_message_raw = env.get("message")
        res.exception_args_raw = env.get("args")
        res.return_value_raw = "EXC:" + str(env.get("type"))
    return res


# ---------------------------------------------------------------------------
class LocalBackend:
    name, version = "local_subprocess", BACKENDS_VERSION

    def __init__(self, workdir: pathlib.Path | None = None, default_workers: int = 4):
        self.workdir = pathlib.Path(workdir or tempfile.mkdtemp(prefix="bdg_local_"))
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.harness = self.workdir / "bdg_harness.py"
        self.harness.write_text(BDG_HARNESS, encoding="utf-8", newline="\n")
        self.handles: dict[str, dict] = {}
        self.default_workers = default_workers

    def header_fields(self) -> dict:
        return {"backend": self.name, "backend_version": self.version, "base_image_tag": None,
                "base_node_uuid": None, "network_policy_requested": "none",
                "egress_probe_result": "not_measured", "execution_env": "local_subprocess", "network_blocked": False}

    def prepare_candidate(self, handle_id: str, source: str, fn: str) -> dict:
        p = self.workdir / handle_id / "impl.py"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(source, encoding="utf-8", newline="\n")
        ok = hashlib.sha256(p.read_bytes()).hexdigest() == sha256(source)
        self.handles[handle_id] = {"impl": p, "fn": fn, "branch_uuid": None, "source_sha256_verified": ok}
        return {"branch_uuid": None, "source_sha256_verified": ok}

    def _run_one(self, job: ExecJob) -> RawResult:
        h = self.handles[job.handle_id]
        res = RawResult()
        t0 = time.perf_counter()
        try:
            p = subprocess.run([sys.executable, str(self.harness), h["fn"], job.exec_id, json.dumps(job.args), str(h["impl"])],
                               capture_output=True, text=True, timeout=EXEC_TIMEOUT_S)
            res.stdout_raw, res.stderr_raw, res.exit_code = p.stdout, p.stderr, p.returncode
        except subprocess.TimeoutExpired as e:
            res.timed_out, res.exit_code = True, -1
            res.stdout_raw = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
            res.stderr_raw = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
        res.duration_ms = int((time.perf_counter() - t0) * 1000)
        res.stderr_bytes = len(res.stderr_raw.encode("utf-8"))
        if res.exit_code == 0:
            parse_envelope(res.stdout_raw, job.exec_id, res)
        else:
            res.stdout_bytes = len(res.stdout_raw.encode("utf-8"))
        return res

    def execute_many(self, jobs: list[ExecJob], max_inflight: int | None = None) -> dict[str, RawResult]:
        out: dict[str, RawResult] = {}
        with ThreadPoolExecutor(max_workers=max_inflight or self.default_workers) as ex:
            futs = {ex.submit(self._run_one, j): j.exec_id for j in jobs}
            for f in as_completed(futs):
                out[futs[f]] = f.result()
        return out

    def teardown(self):
        pass


# ---------------------------------------------------------------------------
class ContreeBackend:
    """
    Nebius Token Factory Sandboxes (ConTree). 실측 API 기준.
    트리:  image → base(harness 업로드, disposable=False) → handle 노드(impl 업로드, disposable=False)
           → 실행(disposable=True, 버림)
    """
    name, version = "contree", BACKENDS_VERSION
    EGRESS_CODE = ("import urllib.request\n"
                   "try:\n    urllib.request.urlopen('https://pypi.org', timeout=5); print('EGRESS_OK')\n"
                   "except Exception as e:\n    print('EGRESS_BLOCKED', type(e).__name__)\n")

    def __init__(self, image_tag: str = "python:3.12-slim", default_workers: int = 8, measure_egress: bool = True):
        from contree_sdk import ContreeSync
        from contree_sdk.utils.models.file import UploadFileSpec
        self._UploadFileSpec = UploadFileSpec
        self.client = ContreeSync()
        try:
            import contree_sdk as _c
            self.version = f"{BACKENDS_VERSION}/contree-sdk {getattr(_c, '__version__', 'unknown')}"
        except Exception:  # noqa: BLE001
            pass
        self.image_tag = image_tag
        self.default_workers = default_workers
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="bdg_contree_"))
        (self.tmp / "bdg_harness.py").write_text(BDG_HARNESS, encoding="utf-8", newline="\n")
        # base 노드: /work + harness 업로드. disposable=False 필수 (기본값 True 면 uuid=None 으로 폐기됨)
        self.base = self.client.images.use(image_tag).run(
            shell="mkdir -p /work",
            files=[UploadFileSpec(source=str(self.tmp / "bdg_harness.py"), path="/work/bdg_harness.py")],
            disposable=False, timeout=60).wait()
        if not getattr(self.base, "uuid", None):
            raise RuntimeError("ContreeBackend: base node has no uuid (disposable?)")
        self.base_uuid = str(self.base.uuid)          # SDK 는 UUID 객체를 반환 → JSON 직렬화용 str
        self.egress = "not_measured"
        if measure_egress:
            r = self.base.run(command="python", args=["-c", self.EGRESS_CODE], disposable=True, timeout=30).wait()
            self.egress = (r.stdout or "").strip().splitlines()[-1] if (r.stdout or "").strip() else f"probe_failed exit={r.exit_code}"
        self.handles: dict[str, dict] = {}

    def header_fields(self) -> dict:
        return {"backend": self.name, "backend_version": self.version, "base_image_tag": self.image_tag,
                "base_node_uuid": self.base_uuid, "network_policy_requested": "none (ConTree default)",
                "egress_probe_result": self.egress, "execution_env": "contree_sandbox",
                "network_blocked": False}   # 자동 true 금지 — 실측값은 egress_probe_result

    def prepare_candidate(self, handle_id: str, source: str, fn: str) -> dict:
        local = self.tmp / handle_id / "impl.py"
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_text(source, encoding="utf-8", newline="\n")
        remote = f"/work/{handle_id}/impl.py"
        node = self.base.run(shell=f"mkdir -p /work/{handle_id}",
                             files=[self._UploadFileSpec(source=str(local), path=remote)],
                             disposable=False, timeout=60).wait()
        if not getattr(node, "uuid", None):
            raise RuntimeError(f"ContreeBackend: branch for {handle_id} has no uuid")
        chk = node.run(command="sha256sum", args=[remote], disposable=True, timeout=30).wait()
        remote_sha = (chk.stdout or "").split()[0] if (chk.stdout or "").strip() else ""
        verified = (remote_sha == sha256(source))
        self.handles[handle_id] = {"node": node, "impl": remote, "fn": fn, "branch_uuid": str(node.uuid),
                                   "source_sha256_verified": verified}
        return {"branch_uuid": str(node.uuid), "source_sha256_verified": verified}

    def _run_one(self, job: ExecJob, attempt: int = 0) -> RawResult:
        h = self.handles[job.handle_id]
        res = RawResult(attempt=attempt, node_uuid=h["branch_uuid"])  # 이미 str
        t0 = time.perf_counter()
        try:
            r = h["node"].run(command="python",
                              args=["/work/bdg_harness.py", h["fn"], job.exec_id, json.dumps(job.args), h["impl"]],
                              disposable=True, timeout=EXEC_TIMEOUT_S).wait()
            res.stdout_raw, res.stderr_raw = (r.stdout or ""), (r.stderr or "")
            res.exit_code = r.exit_code if r.exit_code is not None else -2
            st = str(getattr(r, "state", "")).upper()
            # 실측(conformance 8): timeout 시 state=SUCCEEDED, exit_code=-1 → exit_code 로 판정
            if res.exit_code == -1 or "TIMEOUT" in st or "TIMED_OUT" in st:
                res.timed_out, res.exit_code = True, -1
        except Exception as e:  # noqa: BLE001  — SDK/네트워크 예외: 결과로 위장하지 않음
            name = type(e).__name__
            if "Timeout" in name:
                res.timed_out, res.exit_code = True, -1
            else:
                res.sandbox_error = f"{name}: {e}"[:300]
                res.exit_code = -2
                if attempt == 0:
                    time.sleep(1.0)
                    return self._run_one(job, attempt=1)
        res.duration_ms = int((time.perf_counter() - t0) * 1000)   # 왕복 지연 포함 (로컬과 비교 불가)
        res.stderr_bytes = len(res.stderr_raw.encode("utf-8"))
        if res.exit_code == 0:
            parse_envelope(res.stdout_raw, job.exec_id, res)
        else:
            res.stdout_bytes = len(res.stdout_raw.encode("utf-8"))
        return res

    def execute_many(self, jobs: list[ExecJob], max_inflight: int | None = None) -> dict[str, RawResult]:
        out: dict[str, RawResult] = {}
        with ThreadPoolExecutor(max_workers=max_inflight or self.default_workers) as ex:
            futs = {ex.submit(self._run_one, j): j.exec_id for j in jobs}
            for f in as_completed(futs):
                out[futs[f]] = f.result()
        return out

    def teardown(self):
        pass   # disposable=False 노드는 남음 (베타: 목록에 안 잡힘, 180일 보존)


def make_backend(name: str, **kw):
    if name == "local":
        return LocalBackend(default_workers=kw.get("workers") or 4)
    if name == "contree":
        return ContreeBackend(image_tag=kw.get("image_tag") or "python:3.12-slim",
                              default_workers=kw.get("workers") or 8, measure_egress=kw.get("measure_egress", True))
    raise ValueError(f"unknown backend {name!r} (no silent fallback)")
