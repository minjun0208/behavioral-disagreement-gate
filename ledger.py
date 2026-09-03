#!/usr/bin/env python3
"""
ledger.py — 명료화 루프 / append-only 결정 원장  (v0.1.0)

레코드 종류:
  session_header  세션 시작. seed_schedule 을 여기서 확정 (L5: 답변 전에 seed 고정)
  question        라운드별 질문 (question_sha256 로 재출현 검출)
  answer          사용자 답. raw_input(원문) 과 parsed(검증 통과분) 분리 (L2)
  decision        확정된 (input → expected)
  supersede       기존 decision 을 새 expected 로 대체
  revoke          decision 무효화
  defer           답 보류
  session_footer  종료

순수 함수 (시간·난수·IO 없음):
  fold(records)        → 활성 뷰 + 모순 목록
  question_hash(...)   → 질문 동일성 해시
  make_seed_schedule() → 결정론적 seed 목록

IO 는 Ledger 클래스에만 있음. 수정·삭제 API 없음 (append 만).
"""
import datetime as dt
import hashlib
import json
import pathlib
import random

LEDGER_VERSION = "0.1.1"
KINDS = ("session_header", "question", "answer", "decision", "supersede", "revoke", "defer", "session_footer")


def canon(obj) -> str:
    """canonical JSON. 같은 의미 → 같은 문자열."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 순수 함수
# ---------------------------------------------------------------------------
def make_seed_schedule(session_seed: int, max_rounds: int) -> list[int]:
    """세션 시작 시 전 라운드 probe seed 확정. 같은 session_seed → 같은 목록."""
    rng = random.Random(session_seed)
    return rng.sample(range(1, 10**6), max_rounds)


def question_hash(representative_input: dict, options: list[dict]) -> str:
    """질문 동일성. 대표 입력 + 선택지 집합이 같으면 같은 질문 (NO_PROGRESS 검출용)."""
    opt_keys = sorted(canon({"kind": o["kind"], "value": o.get("value")}) for o in options)
    return sha256(canon({"input": representative_input, "options": opt_keys}))


def fold(records: list[dict]) -> dict:
    """
    ledger 전체를 seq 순으로 접어 활성 뷰를 만든다.
    반환: {
      "active":    {input_key: decision_record},   # 살아있는 결정
      "inactive":  [decision_id, ...],              # supersede/revoke 된 것
      "conflicts": [{"input": ..., "decision_ids": [...]}],  # 같은 input 에 다른 expected
      "n_records": int,
    }
    예외를 던지지 않는다. 모순은 반환값으로 알린다 (루프가 결정).
    """
    recs = sorted(records, key=lambda r: r["seq"])
    decisions: dict[str, dict] = {}
    inactive: list[str] = []
    for r in recs:
        k = r["kind"]
        if k == "decision":
            decisions[r["decision_id"]] = {**r, "_alive": True}
        elif k == "supersede":
            tgt = decisions.get(r["target_decision_id"])
            if tgt is not None and tgt["_alive"]:
                tgt["_alive"] = False
                inactive.append(tgt["decision_id"])
            decisions[r["new_decision_id"]] = {
                "kind": "decision", "decision_id": r["new_decision_id"], "round": r["round"],
                "input": tgt["input"] if tgt else r["input"], "expected": r["new_expected"],
                "seq": r["seq"], "supersedes": r["target_decision_id"], "_alive": True,
            }
        elif k == "revoke":
            tgt = decisions.get(r["target_decision_id"])
            if tgt is not None and tgt["_alive"]:
                tgt["_alive"] = False
                inactive.append(tgt["decision_id"])

    active_by_input: dict[str, list[dict]] = {}
    for d in decisions.values():
        if d["_alive"]:
            active_by_input.setdefault(canon(d["input"]), []).append(d)

    active, conflicts = {}, []
    for ik, ds in sorted(active_by_input.items()):
        expecteds = {canon(d["expected"]) for d in ds}
        if len(expecteds) > 1:
            conflicts.append({"input": ds[0]["input"], "decision_ids": sorted(d["decision_id"] for d in ds)})
        # 모순이어도 최신(seq 최대)을 active 로 노출하되 conflicts 에 기록 — 루프가 중단 판단
        latest = max(ds, key=lambda d: d["seq"])
        active[ik] = {kk: v for kk, v in latest.items() if not kk.startswith("_")}
    return {"active": active, "inactive": sorted(inactive), "conflicts": conflicts, "n_records": len(recs)}


def active_view_hash(view: dict) -> str:
    """
    활성 뷰의 의미 내용만 해시: (input → expected) 집합 + conflicts.
    session_id / ts / seq / decision_id 같은 provenance 는 제외 → 다른 세션에서 같은 결정이면 같은 해시.
    """
    core = {
        "active": sorted([{"input": d["input"], "expected": d["expected"]} for d in view["active"].values()], key=canon),
        "conflicts": view["conflicts"],
    }
    return sha256(canon(core))


# ---------------------------------------------------------------------------
# IO (append-only)
# ---------------------------------------------------------------------------
class Ledger:
    def __init__(self, path: pathlib.Path, session_id: str):
        self.path, self.session_id = pathlib.Path(path), session_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.records: list[dict] = []
        if self.path.exists():
            self.records = [json.loads(l) for l in self.path.read_text(encoding="utf-8").splitlines() if l.strip()]
        self._seq = (self.records[-1]["seq"] + 1) if self.records else 0

    @staticmethod
    def now() -> str:
        return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def append(self, kind: str, **fields) -> dict:
        if kind not in KINDS:
            raise ValueError(f"unknown ledger kind {kind}")
        rec = {"session_id": self.session_id, "seq": self._seq, "ts": self.now(), "kind": kind}
        rec.update(fields)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.records.append(rec)
        self._seq += 1
        return rec

    def view(self) -> dict:
        return fold(self.records)
