#!/usr/bin/env python3
"""
providers.py — 명료화 루프 / 답변 공급자  (v0.1.0)

루프는 provider.ask(question, fetch_reference) 만 호출한다. 구현체를 바꿔도 루프 코드는 안 바뀐다.
  CliAnswerProvider       사람이 터미널에 입력
  ScriptedAnswerProvider  미리 정한 답 (회귀 테스트·데모). 소진되면 unknown.
  (WebAnswerProvider 는 웹 UI 단계에서 추가)

fetch_reference: 인자 없는 callable. 부르면 그 시점에 참고 검색(research.search)이 실행되고 ledger 에 기록된 뒤
  레코드(dict)가 돌아온다. provider 는 질문을 다 보여준 다음에 부르고, 결과를 입력 프롬프트 앞에 표시만 한다.
  선택지·기본값·답에 영향을 주지 않는다. None 이면 아무것도 찍지 않는다. 몇 번을 불러도 검색은 한 번이다 (loop 가 보장).

반환 형식: {"option_id": str, "raw_input": str | None}
raw_input 은 절대 그대로 코드·프롬프트에 들어가지 않는다 (acceptance.parse_* 가 검증).
"""
import time

import question as qmod
import research

PROVIDERS_VERSION = "0.2.0"


def _show(q: dict, fetch_reference) -> None:
    print(qmod.render(q))
    if fetch_reference is None:
        return
    print()
    print("reference search via Tavily ...", end=" ", flush=True)   # 실제 런타임 호출은 다음 줄에서 일어난다
    t0 = time.perf_counter()
    ref = fetch_reference()
    dt = time.perf_counter() - t0
    tail = f"ok · {ref['n_results']} results" if ref["status"] == "ok" else ref["status"]
    if ref.get("cached"):
        tail += " · from local cache"
    print(f"{tail} · {dt:.1f}s", flush=True)
    print(research.render(ref))


class CliAnswerProvider:
    label = "cli"

    def ask(self, q: dict, fetch_reference=None) -> dict:
        _show(q, fetch_reference)
        valid = {o["option_id"] for o in q["options"]}
        while True:
            choice = input("> option id: ").strip()
            if choice not in valid:
                print(f"  choose one of: {sorted(valid)}")
                continue
            raw = None
            if choice == "other_value":
                raw = input("> value (Python literal, e.g. -1, 'abc', [1, 2], None): ")
            elif choice == "other_exception":
                raw = input("> exception type name (e.g. ValueError): ")
            return {"option_id": choice, "raw_input": raw}


class ScriptedAnswerProvider:
    """answers: [{"option_id": "o2"}, {"option_id": "other_value", "raw_input": "0"}, ...]"""
    label = "scripted"

    def __init__(self, answers: list[dict], echo: bool = True):
        self.answers = list(answers)
        self.echo = echo
        self.i = 0

    def ask(self, q: dict, fetch_reference=None) -> dict:
        if self.echo:
            _show(q, fetch_reference)
        if self.i >= len(self.answers):
            a = {"option_id": "unknown", "raw_input": None}
        else:
            spec = self.answers[self.i]; self.i += 1
            a = None
            if "value" in spec:           # 관측 선택지 중 값이 같은 것 → 없으면 other_value
                hit = next((o for o in q["options"] if o["kind"] == "value" and o["value"] == str(spec["value"])), None)
                a = {"option_id": hit["option_id"], "raw_input": None} if hit else {"option_id": "other_value", "raw_input": str(spec["value"])}
            elif "exception" in spec:
                hit = next((o for o in q["options"] if o["kind"] == "exception" and o["value"] == spec["exception"]), None)
                a = {"option_id": hit["option_id"], "raw_input": None} if hit else {"option_id": "other_exception", "raw_input": spec["exception"]}
            else:
                a = {"option_id": spec.get("option_id", "unknown"), "raw_input": spec.get("raw_input")}
        if self.echo:
            print(f"> [scripted] {a['option_id']}" + (f"  raw={a['raw_input']!r}" if a["raw_input"] is not None else ""))
        return a
