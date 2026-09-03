#!/usr/bin/env python3
"""
acceptance.py — 명료화 루프 / typed acceptance test 컴파일  (v0.1.0)

원칙 (L2·L3):
  - 사용자 입력·후보 출력은 신뢰 불가 데이터. 문자열 결합으로 코드·프롬프트에 넣지 않는다.
  - value 답은 ast.literal_eval 을 통과한 JSON-안전 리터럴만 허용.
  - exception 답은 파이썬 식별자 정규식을 통과한 이름만 허용.
  - 컴파일 결과는 runner 의 r_gate 테스트 형식과 동일 → 그대로 회귀 스위트에 합류 가능.

전부 순수 함수. 시간·난수·IO 없음.
"""
import ast
import math
import re

ACCEPTANCE_VERSION = "0.1.0"
MAX_RAW_LEN = 2000
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_JSON_SCALARS = (int, float, str, bool, type(None))


class ParseError(ValueError):
    pass


# ---------------------------------------------------------------------------
def is_json_safe(obj, _depth=0) -> bool:
    """JSON 으로 왕복 가능한 값만 True. tuple/set/bytes/complex 등 거부."""
    if _depth > 20:
        return False
    if isinstance(obj, float):
        return math.isfinite(obj)          # inf / nan 은 JSON 왕복 불가 → 거부 (1e999 등)
    if isinstance(obj, bool) or obj is None or isinstance(obj, (int, str)):
        return True
    if isinstance(obj, list):
        return all(is_json_safe(x, _depth + 1) for x in obj)
    if isinstance(obj, dict):
        return all(isinstance(k, str) and is_json_safe(v, _depth + 1) for k, v in obj.items())
    return False


def parse_value(raw: str):
    """
    사용자 원문 → 파이썬 리터럴. 코드가 될 수 있는 것은 전부 거부.
    허용: 숫자, 문자열, True/False/None, 리스트, 딕셔너리 (재귀)
    거부: 함수 호출, 이름 참조, 연산, tuple/set/bytes, 과도한 길이
    """
    if not isinstance(raw, str):
        raise ParseError("raw must be str")
    s = raw.strip()
    if not s:
        raise ParseError("empty")
    if len(s) > MAX_RAW_LEN:
        raise ParseError(f"too long ({len(s)} > {MAX_RAW_LEN})")
    try:
        node = ast.parse(s, mode="eval")
    except SyntaxError as e:
        raise ParseError(f"not a literal: {e.msg}") from None
    # literal_eval 이 허용하는 노드만 통과시키되, 추가로 tuple/set 도 거부
    for n in ast.walk(node):
        if isinstance(n, (ast.Call, ast.Name, ast.Attribute, ast.Subscript, ast.Lambda,
                          ast.BinOp, ast.BoolOp, ast.Compare, ast.Tuple, ast.Set,
                          ast.JoinedStr, ast.Starred, ast.comprehension)):
            raise ParseError(f"forbidden construct: {type(n).__name__}")
    try:
        val = ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError) as e:
        raise ParseError(f"literal_eval failed: {e}") from None
    if not is_json_safe(val):
        raise ParseError("value is not JSON-safe")
    return val


def parse_exception_type(raw: str) -> str:
    if not isinstance(raw, str):
        raise ParseError("raw must be str")
    s = raw.strip()
    if not _IDENT.match(s):
        raise ParseError("exception type must be a plain identifier")
    return s


def expected_from_answer(kind: str, raw: str) -> dict:
    """
    answer.kind + raw_input → decision.expected
      value     → {"kind": "value", "repr": repr(parsed), "value": parsed}
      exception → {"kind": "exception", "type": name}
    ParseError 시 예외. 호출자는 raw 만 ledger 에 남기고 parsed 는 null 로 기록.
    """
    if kind == "value":
        v = parse_value(raw)
        return {"kind": "value", "repr": repr(v), "value": v}
    if kind == "exception":
        return {"kind": "exception", "type": parse_exception_type(raw)}
    raise ParseError(f"unsupported answer kind {kind}")


# ---------------------------------------------------------------------------
def compile_acceptance(active: dict, fn_name: str) -> list[dict]:
    """
    ledger 활성 뷰 → runner r_gate 형식 테스트 목록.
      {"test_id": "acceptance/<decision_id>", "args": <input>, "expected": <value>}
      {"test_id": "acceptance/<decision_id>", "args": <input>, "expected_exc": <type>}
    문자열 결합 없음. 값은 JSON 객체 그대로 전달 → runner harness 가 비교.
    """
    tests = []
    for _, d in sorted(active.items()):
        exp = d["expected"]
        t = {"test_id": f"acceptance/{d['decision_id']}", "args": d["input"], "origin": "user_confirmed"}
        if exp["kind"] == "value":
            if not is_json_safe(exp.get("value")):
                raise ParseError(f"decision {d['decision_id']} has non-JSON-safe value")
            t["expected"] = exp["value"]
        elif exp["kind"] == "exception":
            t["expected_exc"] = parse_exception_type(exp["type"])
        else:
            raise ParseError(f"decision {d['decision_id']} has unknown expected kind")
        tests.append(t)
    return tests


def render_confirmed_lines(active: dict, fn_name: str) -> list[str]:
    """
    재수리 프롬프트용 문장. raw_input 이 아니라 검증된 parsed 값에서 재구성 (L2).
      round_half(x=-0.5) == 0
      mean(xs=[]) raises ValueError
    """
    lines = []
    for _, d in sorted(active.items()):
        args = ", ".join(f"{k}={repr(v)}" for k, v in sorted(d["input"].items()))
        exp = d["expected"]
        if exp["kind"] == "value":
            lines.append(f"{fn_name}({args}) == {exp['repr']}")
        else:
            lines.append(f"{fn_name}({args}) raises {parse_exception_type(exp['type'])}")
    return lines
