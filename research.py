#!/usr/bin/env python3
"""
research.py — 명료화 루프 / 참고 검색  (v0.1.0)

질문이 만들어진 뒤, 답을 받기 전에, 그 불일치 축의 표준 관례를 Tavily 로 검색해
답하는 사람에게 참고 정보로 보여준다. 판정 경로 밖이다.

불변식:
  - verdict / decision_core_sha256 / acceptance test / question_sha256 에 들어가지 않는다.
    loop.py 는 결과를 ledger 의 'reference' 레코드와 provider 표시에만 넘긴다.
  - 선택지를 고르거나 기본값을 제시하지 않는다.
  - 질의는 과제 정의(ambiguity_axis, issue_text)와 질문(대표 입력, 관측 선택지)만으로 만든다.
    사용자 답(raw_input)은 순서상 들어올 수 없고, build_query 가 코드로도 거부한다.
  - 축 이름별 검색어 표를 두지 않는다. 그건 정답을 미리 아는 셈이라 게이트의 주장과 어긋난다.
  - Tavily 의 LLM 생성 answer 필드는 요청하지 않는다. URL 이 붙은 결과만 쓴다.
  - 키 없음 / HTTP 오류 / 타임아웃은 예외가 아니라 status "unavailable" + reason 으로 돌아온다. 루프는 계속된다.
  - API 키는 어디에도 기록하지 않는다 (오류 문자열에서도 지운다).

캐시: research_cache/<cache_key>.json. 편의 장치이지 정확성 장치가 아니다 — 검색은 매번 다른 결과를 줄 수 있다.
      캐시에서 온 결과는 cached: true 와 원래 fetched_at 로 표시된다. 키 없이도 캐시는 읽는다.

순수 함수 (시간·난수·IO 없음): issue_core, axis_words, observed_displays, build_query, cache_key, normalize_results
IO: search (캐시 파일 + HTTP), render (문자열만)

usage:
  python research.py --selftest
  python research.py --task tasks/round_half.json --question clarify/live5/round_1.question.json          # 질의만 출력
  python research.py --task tasks/round_half.json --question clarify/live5/round_1.question.json --search # 실제 호출
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import sys
import textwrap
import unicodedata

RESEARCH_VERSION = "0.1.0"
SEARCH_PROVIDER = "tavily"
ENDPOINT = "https://api.tavily.com/search"
ENV_KEY = "TAVILY_API_KEY"
TIMEOUT_S = 10.0
CACHE_DIR = pathlib.Path("research_cache")
SNIPPET_MAX = 300
TITLE_MAX = 160
KEEP_WS = (chr(9), chr(10), chr(13))   # 탭·개행·CR 은 제어문자지만 공백으로 접히므로 남긴다
PARAMS = {
    "search_depth": "advanced",       # 2 credits. basic 은 실측에서 튜토리얼·잡음(score 0.17~0.24)을 냈다
    "max_results": 3,
    "chunks_per_source": 3,           # 관례를 말하는 문장이 첫 청크에 없을 때가 있다
    "include_answer": False,          # LLM 생성 요약은 받지 않는다 — 출처 없는 문장이 된다
    "include_raw_content": False,
    # include_domains 부스트는 쓰지 않는다: 실측에서 위키 리비전 diff·LaTeX 조각 같은 잡음을 끌어들였다 (2026-09-11 비교)
}
# 질문 dict 에 이런 키가 있으면 답 이후의 객체다 → 질의를 만들지 않는다
FORBIDDEN_QUESTION_KEYS = ("raw_input", "answer", "answers", "parsed", "expected", "decision", "option_id")


def canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# 순수 함수
# ---------------------------------------------------------------------------
def issue_core(issue_text: str) -> str:
    """'Implement `sig` that rounds a number ...' → 'rounds a number ...'. 다른 형식이면 원문(끝 마침표 제거)."""
    s = " ".join((issue_text or "").split())
    m = re.match(r"^Implement\s+`[^`]*`\s+that\s+(.+)$", s)
    if m:
        s = m.group(1)
    return s.rstrip(". ").strip()


def axis_words(axis) -> str:
    """'tie_policy' → 'tie policy'. 표를 두지 않는다: 축 이름을 단어로 풀 뿐이다."""
    return " ".join(str(axis or "").replace("_", " ").split())


def observed_displays(options: list[dict]) -> list[str]:
    """관측 선택지(o1, o2, …)의 표시 문자열만. other_value / other_exception / unknown 은 제외."""
    out = []
    for o in options or []:
        if re.fullmatch(r"o\d+", str(o.get("option_id", ""))) and o.get("kind") in ("value", "exception") and o.get("value") is not None:
            out.append(str(o["display"]))
    return out


def build_query(task: dict, question: dict) -> str:
    """
    순수 함수. 같은 (task, question) → 같은 질의.
    question 은 question.make_question() 의 결과, 즉 답을 받기 전의 객체여야 한다.
    """
    for k in FORBIDDEN_QUESTION_KEYS:
        if k in question:
            raise ValueError(f"build_query: question carries '{k}' — the query must be built before any answer")
    core = issue_core(task.get("issue_text", ""))
    axis = axis_words(task.get("ambiguity_axis"))
    inp = (question.get("representative") or {}).get("input") or {}
    args = ", ".join(f"{k}={inp[k]!r}" for k in sorted(inp))
    alts = " or ".join(observed_displays(question.get("options") or []))
    q = f"Python {core}" if core else f"Python {task.get('function_name', 'function')}"
    if axis:
        q += f": {axis} convention"
    q += "."
    if args and alts:
        q += f" For {args}, {alts}?"
    return q


def cache_key(query: str, params: dict = PARAMS) -> str:
    return sha256(canon({"endpoint": ENDPOINT, "query": query, "params": params}))


def _clean(s, limit: int) -> str:
    """사이트 데이터로 나가는 문자열: 제어문자 제거, 공백 접기, 길이 제한."""
    s = "".join(ch for ch in str(s or "") if unicodedata.category(ch) != "Cc" or ch in KEEP_WS)
    s = " ".join(s.split())
    return s if len(s) <= limit else s[:limit - 3].rstrip() + "..."


def normalize_results(payload: dict, max_results: int = PARAMS["max_results"]) -> list[dict]:
    """Tavily 응답 → 표시·기록용 최소 필드. http(s) URL 이 없는 결과는 버린다 (출처 없는 문장은 쓰지 않는다)."""
    out = []
    for r in (payload or {}).get("results") or []:
        url = str(r.get("url") or "").strip()
        if not re.match(r"^https?://", url, re.I):
            continue
        score = r.get("score")
        out.append({"title": _clean(r.get("title"), TITLE_MAX), "url": url,
                    "content": _clean(r.get("content"), SNIPPET_MAX),
                    "score": float(score) if isinstance(score, (int, float)) else None})
        if len(out) >= max_results:
            break
    return out


def _redact(s: str, key: str | None) -> str:
    s = str(s)
    if key:
        s = s.replace(key, "tvly-***")
    return re.sub(r"tvly-[A-Za-z0-9_-]+", "tvly-***", s)


# ---------------------------------------------------------------------------
# IO
# ---------------------------------------------------------------------------
def _base(query: str) -> dict:
    return {"search_provider": SEARCH_PROVIDER, "endpoint": f"POST {ENDPOINT}", "query": query,
            "params": json.loads(json.dumps(PARAMS)), "cache_key": cache_key(query),
            "status": None, "reason": None, "results": [], "n_results": 0,
            "request_id": None, "response_time": None, "cached": False, "fetched_at": None,
            "role": "reference_only", "research_version": RESEARCH_VERSION}


def _http_post(url: str, body: dict, key: str) -> tuple[int, str]:
    import httpx  # requirements.txt 에 이미 고정 (0.24.1). 새 의존성 없음.
    r = httpx.post(url, json=body, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, timeout=TIMEOUT_S)
    return r.status_code, r.text


def search(query: str, mode: str = "auto", key: str | None = None,
           cache_dir: pathlib.Path = CACHE_DIR, post=_http_post) -> dict:
    """
    ledger 'reference' 레코드의 본문을 돌려준다. 프로그래밍 오류가 아니면 예외를 던지지 않는다.
      status "ok"          results 가 있다 (0개일 수도 있다). cached 가 True 면 캐시에서 왔다.
      status "unavailable" 키 없음 / HTTP 오류 / 타임아웃 / 응답 파싱 실패. reason 에 이유.
      status "off"         --research off. 호출하지 않았다.
    """
    rec = _base(query)
    if mode == "off":
        rec.update(status="off", reason="research disabled (--research off)")
        return rec

    cpath = pathlib.Path(cache_dir) / f"{rec['cache_key']}.json"
    if cpath.exists():
        try:
            c = json.loads(cpath.read_text(encoding="utf-8"))
            if c.get("query") == query and c.get("params") == rec["params"]:
                results = normalize_results({"results": c.get("results") or []})   # 캐시도 다시 거른다: 손댄 캐시가 http 아닌 URL 을 넣지 못한다
                rec.update(status="ok", results=results, n_results=len(results), request_id=c.get("request_id"),
                           response_time=c.get("response_time"), cached=True, fetched_at=c.get("fetched_at"))
                return rec
        except (OSError, ValueError, KeyError):
            pass  # 깨진 캐시는 무시하고 새로 부른다

    key = key or os.environ.get(ENV_KEY)
    if not key:
        rec.update(status="unavailable", reason=f"no API key ({ENV_KEY} not set)")
        return rec

    try:
        status_code, text = post(ENDPOINT, {"query": query, **PARAMS}, key)
    except Exception as e:  # noqa: BLE001 — 네트워크 계층의 어떤 실패도 루프를 멈추지 않는다
        rec.update(status="unavailable", reason=_redact(f"{type(e).__name__}: {str(e)[:160]}", key))
        return rec
    if status_code != 200:
        rec.update(status="unavailable", reason=_redact(f"HTTP {status_code}: {text[:160]}", key))
        return rec
    try:
        payload = json.loads(text)
    except ValueError as e:
        rec.update(status="unavailable", reason=f"bad JSON from API: {str(e)[:120]}")
        return rec

    results = normalize_results(payload)
    rec.update(status="ok", results=results, n_results=len(results), request_id=payload.get("request_id"),
               response_time=payload.get("response_time"), cached=False, fetched_at=now_iso())
    try:
        cpath.parent.mkdir(parents=True, exist_ok=True)
        cpath.write_text(json.dumps({"query": query, "params": rec["params"], "fetched_at": rec["fetched_at"],
                                     "request_id": rec["request_id"], "response_time": rec["response_time"],
                                     "results": results}, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass  # 캐시 실패는 결과에 영향 없음
    return rec


def render(ref: dict, width: int = 96) -> str:
    """CLI 표시. 선택지 아래, 입력 프롬프트 위에 찍힌다. 발췌는 width 로 줄바꿈 (터미널 한 줄 300자 방지)."""
    ind = " " * 7
    lines = ["", "Reference (Tavily search; shown for your information, not read by the gate):"]
    if ref["status"] != "ok":
        lines.append(f"    {ref['status']}: {ref['reason']}")
        return "\n".join(lines)
    lines += textwrap.wrap("query: " + ref["query"], width=width, initial_indent="    ", subsequent_indent=ind)
    for i, r in enumerate(ref["results"], 1):
        lines.append(f"    {i}. {r['title']}")
        lines.append(f"{ind}{r['url']}")
        if r["content"]:
            lines += textwrap.wrap(r["content"], width=width, initial_indent=ind, subsequent_indent=ind)
    if not ref["results"]:
        lines.append("    (no results)")
    lines.append(f"    fetched {ref['fetched_at']}" + (" (from local cache)" if ref["cached"] else "") + f" · {ref['n_results']} results")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
def _selftest() -> int:
    import tempfile
    task = {"task_id": "round_half", "ambiguity_axis": "tie_policy", "function_name": "round_half",
            "issue_text": "Implement `round_half(x: float) -> int` that rounds a number to the nearest integer."}
    q = {"question_id": "q01", "representative": {"input": {"x": -39.5}, "call": "round_half(x=-39.5)"},
         "options": [{"option_id": "o1", "kind": "value", "value": "-39", "display": "-39"},
                     {"option_id": "o2", "kind": "value", "value": "-40", "display": "-40"},
                     {"option_id": "other_value", "kind": "value", "value": None, "display": "a different value (type it)"},
                     {"option_id": "other_exception", "kind": "exception", "value": None, "display": "it should raise a different exception (name it)"},
                     {"option_id": "unknown", "kind": "unknown", "value": None, "display": "not sure / decide later"}]}
    fails = []

    def check(name, cond):
        if not cond:
            fails.append(name)
        print(f"  {'ok  ' if cond else 'FAIL'} {name}")

    check("issue_core strips the Implement prefix", issue_core(task["issue_text"]) == "rounds a number to the nearest integer")
    check("issue_core leaves other text alone", issue_core("Return the median.") == "Return the median")
    check("axis_words", axis_words("even_length_definition") == "even length definition")
    check("observed_displays excludes open options", observed_displays(q["options"]) == ["-39", "-40"])
    expect = "Python rounds a number to the nearest integer: tie policy convention. For x=-39.5, -39 or -40?"
    check("build_query exact", build_query(task, q) == expect)
    check("build_query deterministic", build_query(task, q) == build_query(json.loads(json.dumps(task)), json.loads(json.dumps(q))))
    exc = {"option_id": "o1", "kind": "exception", "value": "ZeroDivisionError", "display": "raises ZeroDivisionError"}
    q2 = {"representative": {"input": {"a": 1.0, "b": 0.0}}, "options": [exc, {"option_id": "o2", "kind": "value", "value": "inf", "display": "inf"}]}
    check("build_query with exception option", build_query({"issue_text": "Implement `safe_div(a, b)` that divides a by b.", "ambiguity_axis": "error_policy"}, q2)
          == "Python divides a by b: error policy convention. For a=1.0, b=0.0, raises ZeroDivisionError or inf?")
    try:
        build_query(task, {**q, "raw_input": "-40"}); check("build_query rejects raw_input", False)
    except ValueError:
        check("build_query rejects raw_input", True)
    try:
        build_query(task, {**q, "option_id": "o2"}); check("build_query rejects an answered object", False)
    except ValueError:
        check("build_query rejects an answered object", True)
    check("cache_key stable", cache_key(expect) == cache_key(expect) and len(cache_key(expect)) == 64)
    norm = normalize_results({"results": [{"title": "A\n B", "url": "javascript:alert(1)", "content": "x"},
                                          {"title": "Rounding", "url": "https://en.wikipedia.org/wiki/Rounding", "content": "  a  " + "b" * 400, "score": 0.9},
                                          {"title": "T", "url": "http://x.y", "content": "", "score": "n/a"}]})
    check("normalize drops non-http url", [r["url"] for r in norm] == ["https://en.wikipedia.org/wiki/Rounding", "http://x.y"])
    check("normalize truncates content", len(norm[0]["content"]) <= SNIPPET_MAX and norm[0]["content"].endswith("..."))
    check("normalize keeps numeric score only", norm[0]["score"] == 0.9 and norm[1]["score"] is None)
    ctl = normalize_results({"results": [{"title": "a" + chr(8) + "b" + chr(0) + "c" + chr(0x2013) + "d", "url": "https://x.y", "content": "p" + chr(27) + "q" + chr(9) + "r"}]})[0]
    check("normalize strips control chars, keeps unicode", ctl["title"] == "abc" + chr(0x2013) + "d" and ctl["content"] == "pq r")

    with tempfile.TemporaryDirectory() as td:
        cdir = pathlib.Path(td)
        off = search(expect, mode="off", cache_dir=cdir)
        check("mode off -> status off, no results", off["status"] == "off" and off["results"] == [] and off["role"] == "reference_only")
        saved = os.environ.pop(ENV_KEY, None)
        try:
            nokey = search(expect, cache_dir=cdir, post=lambda *a: (_ for _ in ()).throw(AssertionError("must not call")))
        finally:
            if saved is not None:
                os.environ[ENV_KEY] = saved
        check("no key -> unavailable, network not called", nokey["status"] == "unavailable" and "no API key" in nokey["reason"])

        def boom(url, body, key):
            raise TimeoutError("timed out after 10s key=" + key)
        t = search(expect, key="tvly-secret123", cache_dir=cdir, post=boom)
        check("network error -> unavailable, key redacted", t["status"] == "unavailable" and "tvly-secret123" not in t["reason"] and "TimeoutError" in t["reason"])
        h = search(expect, key="tvly-secret123", cache_dir=cdir, post=lambda u, b, k: (432, "quota exceeded"))
        check("HTTP 432 -> unavailable with code", h["status"] == "unavailable" and h["reason"].startswith("HTTP 432"))
        bad = search(expect, key="tvly-secret123", cache_dir=cdir, post=lambda u, b, k: (200, "{not json"))
        check("bad JSON -> unavailable", bad["status"] == "unavailable" and bad["reason"].startswith("bad JSON"))

        calls = []

        def fake(url, body, key):
            calls.append(body)
            check("request omits include_answer=true", body.get("include_answer") is False and body["query"] == expect)
            return 200, json.dumps({"query": expect, "request_id": "req-1", "response_time": 1.2,
                                    "results": [{"title": "Rounding - Wikipedia", "url": "https://en.wikipedia.org/wiki/Rounding",
                                                 "content": "Round half to even is the default rounding mode used in IEEE 754.", "score": 0.83}]})
        ok = search(expect, key="tvly-secret123", cache_dir=cdir, post=fake)
        check("ok -> results with url", ok["status"] == "ok" and ok["n_results"] == 1 and ok["results"][0]["url"].startswith("https://") and ok["cached"] is False and ok["request_id"] == "req-1")
        again = search(expect, key=None, cache_dir=cdir, post=boom)
        check("second call -> cache hit, no network, cached=True, same fetched_at", again["status"] == "ok" and again["cached"] is True
              and again["fetched_at"] == ok["fetched_at"] and len(calls) == 1)
        check("cache never stores the key", "tvly-secret123" not in (cdir / f"{ok['cache_key']}.json").read_text(encoding="utf-8"))
        cf = cdir / f"{ok['cache_key']}.json"
        cj = json.loads(cf.read_text(encoding="utf-8")); cj["results"].append({"title": "x", "url": "javascript:alert(1)", "content": "y"})
        cf.write_text(json.dumps(cj), encoding="utf-8")
        tam = search(expect, key=None, cache_dir=cdir, post=boom)
        check("tampered cache -> non-http url dropped on read", tam["status"] == "ok" and tam["n_results"] == 1 and all(r["url"].startswith("http") for r in tam["results"]))
        check("render mentions not read by the gate", "not read by the gate" in render(ok) and "https://en.wikipedia.org/wiki/Rounding" in render(ok))
        check("render unavailable", "unavailable: no API key" in render(nokey))
    print(f"selftest: {'PASS' if not fails else 'FAIL ' + str(fails)}")
    return 0 if not fails else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--task")
    ap.add_argument("--question")
    ap.add_argument("--search", action="store_true", help="actually call the API (needs TAVILY_API_KEY); default prints the query only")
    args = ap.parse_args()
    if args.selftest:
        sys.exit(_selftest())
    if not (args.task and args.question):
        ap.error("--task and --question, or --selftest")
    task = json.load(open(args.task, encoding="utf-8"))
    q = json.load(open(args.question, encoding="utf-8"))
    query = build_query(task, q)
    print(query)
    if args.search:
        print(render(search(query)))


if __name__ == "__main__":
    main()
