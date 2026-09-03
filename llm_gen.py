#!/usr/bin/env python3
"""
llm_gen.py — LLM 후보 생성 모듈

역할: Nemotron 에 같은 이슈를 주고 후보 구현 N개를 받는다.
      코드 추출 → 안전 검사 → 중복 제거 → (source, 메타) 반환.
판정 없음. 원본 응답은 전부 파일로 보존한다 (원본 우선 원칙).

주의: 생성된 코드를 로컬에서 실행하는 것은 임시방편이다.
      AST 기반 차단은 최소 방어일 뿐이며, 진짜 격리는 Sandbox 전환 후.
"""
import ast
import hashlib
import time

GEN_VERSION = "0.5.0"

def make_prompt(task: dict, jitter: bool) -> str:
    p = (
        "You are implementing a small Python utility.\n\n"
        "ISSUE:\n" + task["issue_text"] + "\n\n"
        "Requirements:\n"
        "- Output exactly one Python code block containing only the function"
        + (" (plus imports from: " + ", ".join(task.get("allowed_imports", [])) + " if needed)." if task.get("allowed_imports") else ".")
        + "\n- No prints, no tests, no explanation outside the code block.\n"
        "- The function must be named `" + task["function_name"] + "`.\n"
    )
    confirmed = task.get("confirmed_behaviors") or []
    if confirmed:
        p += "\nThe following behaviors have been confirmed by the user and MUST hold:\n"
        p += "".join(f"  - {line}\n" for line in confirmed)
    if jitter:
        p += JITTER_SUFFIX
    return p


JITTER_SUFFIX = (
    "\nUse a different implementation approach than the most obvious one."
)

# 로컬 실행 최소 방어 (Sandbox 전환 전 임시)
FORBIDDEN_NAMES = {"open", "exec", "eval", "__import__", "compile", "input",
                   "breakpoint", "globals", "locals", "vars"}
FORBIDDEN_MODULES = {"os", "sys", "subprocess", "socket", "shutil", "pathlib",
                     "requests", "urllib", "http", "ctypes", "importlib",
                     "multiprocessing", "threading", "signal"}


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
def extract_code(text: str) -> str:
    """응답에서 마지막 python 코드 블록을 추출. reasoning(<think>) 구간 제거."""
    # <think> ... </think> 제거 (reasoning 모델 대응)
    while "<think>" in text and "</think>" in text:
        a = text.index("<think>")
        b = text.index("</think>") + len("</think>")
        text = text[:a] + text[b:]

    blocks = []
    lines = text.splitlines()
    cur, inside = [], False
    for ln in lines:
        stripped = ln.strip()
        if stripped.startswith("```"):
            if inside:
                blocks.append("\n".join(cur))
                cur, inside = [], False
            else:
                inside = True
            continue
        if inside:
            cur.append(ln)
    if inside and cur:            # 닫는 펜스 누락 대응
        blocks.append("\n".join(cur))

    if not blocks:
        # 코드 블록이 없으면 본문 전체가 코드일 수도 있음 → 파싱 시도
        blocks = [text]

    src = blocks[-1].strip() + "\n"
    ast.parse(src)                # SyntaxError 면 호출부에서 처리
    return src


def safety_check(source: str, fn_name: str, allowed_imports) -> None:
    """최소 방어. 위반 시 ValueError. (진짜 격리는 Sandbox)"""
    ALLOWED_IMPORTS = set(allowed_imports)
    tree = ast.parse(source)
    has_fn = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                root = a.name.split(".")[0]
                if root not in ALLOWED_IMPORTS:
                    raise ValueError(f"forbidden import: {a.name}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root not in ALLOWED_IMPORTS:
                raise ValueError(f"forbidden import-from: {node.module}")
        elif isinstance(node, ast.Name):
            if node.id in FORBIDDEN_NAMES:
                raise ValueError(f"forbidden name: {node.id}")
            if node.id in FORBIDDEN_MODULES:
                raise ValueError(f"forbidden module ref: {node.id}")
        elif isinstance(node, ast.Attribute):
            base = node.value
            if isinstance(base, ast.Name) and base.id in FORBIDDEN_MODULES:
                raise ValueError(f"forbidden module attr: {base.id}.{node.attr}")
        elif isinstance(node, ast.FunctionDef) and node.name == fn_name:
            has_fn = True
    if not has_fn:
        raise ValueError(f"function {fn_name} not defined")


def ast_fingerprint(source: str) -> str:
    """공백·주석 차이를 무시한 구조 해시 (중복 판정용)."""
    return sha256(ast.dump(ast.parse(source)))


# ---------------------------------------------------------------------------
CANNED = [  # --dry-run 용 (네트워크 없이 파이프라인 검증)
    "Here is my implementation:\n```python\ndef round_half(x: float) -> int:\n    return int(round(x))\n```",
    "<think>half-up rounding is common</think>\n```python\nimport math\ndef round_half(x: float) -> int:\n    return int(math.floor(x + 0.5))\n```",
    "```python\ndef round_half(x: float) -> int:\n    return int(round(x))\n```",   # c1 과 중복 → dedup 확인용
    "```python\nimport math\ndef round_half(x: float) -> int:\n    if x >= 0:\n        return int(math.floor(x + 0.5))\n    return int(math.ceil(x - 0.5))\n```",
]


def generate_candidates(task: dict, models, n: int, temperature: float, base_seed: int,
                        jitter: bool, dry_run: bool = False,
                        max_attempts_per_slot: int = 3):
    """
    models: str 또는 list[str]. 리스트면 슬롯 i 는 models[i % len(models)] 사용.
            (교차 모델 다양성: 슬롯마다 다른 모델)
    반환: (candidates, events)
      candidates: [{source, prompt_text, prompt_hash, response_text, seed,
                    temperature, model, served_model, gen_duration_ms,
                    tokens_in, tokens_out, fingerprint}]
      events:     [{event, detail}]   ← runner 가 infra_event 로 기록
    """
    if isinstance(models, str):
        models = [models]
    events = []
    out = []
    seen_fp = set()

    client = None
    if not dry_run:
        import os
        from openai import OpenAI
        key = os.environ.get("NEBIUS_API_KEY")
        if not key:
            raise SystemExit("NEBIUS_API_KEY is not set")
        client = OpenAI(base_url="https://api.tokenfactory.nebius.com/v1/",
                        api_key=key, timeout=180.0)

    canned_i = 0
    for slot in range(n):
        model = models[slot % len(models)]
        prompt = make_prompt(task, jitter=(jitter and slot > 0))
        got = None
        for attempt in range(max_attempts_per_slot):
            seed = base_seed + slot * 10 + attempt
            t0 = time.perf_counter()
            try:
                if dry_run:
                    text = CANNED[canned_i % len(CANNED)]
                    canned_i += 1
                    served, tin, tout = "dry-run", 0, 0
                else:
                    resp = client.chat.completions.create(
                        model=model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=temperature,
                        seed=seed,
                        max_tokens=4096,
                    )
                    text = resp.choices[0].message.content or ""
                    served = getattr(resp, "model", model)
                    u = getattr(resp, "usage", None)
                    tin = getattr(u, "prompt_tokens", 0) if u else 0
                    tout = getattr(u, "completion_tokens", 0) if u else 0
                dur = int((time.perf_counter() - t0) * 1000)
                src = extract_code(text)
                safety_check(src, task["function_name"], task.get("allowed_imports", ["math"]))
                fp = ast_fingerprint(src)
                if fp in seen_fp:
                    events.append({"event": "generation_retry",
                                   "detail": f"slot {slot} seed {seed}: duplicate implementation"})
                    continue
                got = {
                    "source": src, "prompt_text": prompt,
                    "prompt_hash": sha256(prompt), "response_text": text,
                    "seed": seed, "temperature": temperature, "model": model,
                    "served_model": served, "gen_duration_ms": dur,
                    "tokens_in": tin, "tokens_out": tout, "fingerprint": fp,
                }
                seen_fp.add(fp)
                break
            except Exception as e:  # API 오류·SyntaxError·안전 위반 전부 기록 후 재시도
                events.append({"event": "generation_retry",
                               "detail": f"slot {slot} seed {seed}: {type(e).__name__}: {e}"})
        if got is None:
            events.append({"event": "generation_slot_failed",
                           "detail": f"slot {slot}: no valid candidate after {max_attempts_per_slot} attempts"})
        else:
            out.append(got)

    if len({c["fingerprint"] for c in out}) < 2:
        events.append({"event": "insufficient_candidate_diversity",
                       "detail": f"distinct implementations: {len(out)}"})
    return out, events
