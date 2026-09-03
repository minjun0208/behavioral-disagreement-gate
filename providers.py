#!/usr/bin/env python3
"""
providers.py — 명료화 루프 / 답변 공급자  (v0.1.0)

루프는 provider.ask(question) 만 호출한다. 구현체를 바꿔도 루프 코드는 안 바뀐다.
  CliAnswerProvider       사람이 터미널에 입력
  ScriptedAnswerProvider  미리 정한 답 (회귀 테스트·데모). 소진되면 unknown.
  (WebAnswerProvider 는 웹 UI 단계에서 추가)

반환 형식: {"option_id": str, "raw_input": str | None}
raw_input 은 절대 그대로 코드·프롬프트에 들어가지 않는다 (acceptance.parse_* 가 검증).
"""
import question as qmod

PROVIDERS_VERSION = "0.1.0"


class CliAnswerProvider:
    label = "cli"

    def ask(self, q: dict) -> dict:
        print(qmod.render(q))
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

    def ask(self, q: dict) -> dict:
        if self.echo:
            print(qmod.render(q))
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
