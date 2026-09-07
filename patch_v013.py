#!/usr/bin/env python3
"""
patch_v013.py — v0.12 → v0.13 패치. 실행: python patch_v013.py

1) experiment_g4_ablation.py : v0.8 에서 바뀐 질문 파일명(round_1.question_1.json) 대응
2) site/app.css              : 왼쪽 주석 여백 260px, 줄바꿈 정리
이미 적용돼 있으면 건너뛴다 (여러 번 실행해도 안전).
"""
import pathlib
import sys

changed = []


def patch(path: str, pairs: list[tuple[str, str]], required=True) -> None:
    p = pathlib.Path(path)
    if not p.exists():
        print(f"  SKIP {path} (없음)")
        return
    s = p.read_text(encoding="utf-8")
    orig = s
    for old, new in pairs:
        if new in s and old not in s:
            continue                      # 이미 적용됨
        if old not in s:
            if required:
                print(f"  WARN {path}: 대상 문자열 없음 → {old[:60]!r}")
            continue
        s = s.replace(old, new)
    if s != orig:
        p.write_text(s, encoding="utf-8", newline="\n")
        changed.append(path)
        print(f"  patched {path}")
    else:
        print(f"  already up to date: {path}")


patch("experiment_g4_ablation.py", [
    ('        q1 = json.load(open(f"clarify/{sid}/round_1.question.json", encoding="utf-8"))',
     '        _qs = sorted(pathlib.Path(f"clarify/{sid}").glob("round_1.question*.json"))\n'
     '        q1 = json.loads(_qs[0].read_text(encoding="utf-8")) if _qs else {"representative": {"call": "(no question recorded)"}}'),
    ('EXP_VERSION = "0.1.0"', 'EXP_VERSION = "0.1.1"'),
])

patch("site/app.css", [
    ("--gutter: 220px;", "--gutter: 260px;"),
    (".entry > aside .v { display: block; color: var(--ink); margin-bottom: 0.6em; word-break: break-all; }",
     ".entry > aside .v { display: block; color: var(--ink); margin-bottom: 0.7em; overflow-wrap: anywhere; word-break: normal; hyphens: none; }"),
    (".entry > aside { font-family: var(--mono); font-size: 0.8rem;",
     ".entry > aside { font-family: var(--mono); font-size: 0.78rem;"),
    (".hash { letter-spacing: 0.02em; word-break: break-all; }",
     ".hash { letter-spacing: 0.02em; overflow-wrap: anywhere; }"),
])

print(f"\n{len(changed)} file(s) changed")
print("다음:")
print("  1. rmdir /s /q ledger\\g4_A_unanimous_violation  (PowerShell: Remove-Item -Recurse -Force ledger/g4_*, clarify/g4_*, runs/g4_*)")
print("  2. python experiment_g4_ablation.py")
print("  3. python build_site_data.py --exclude-sessions demo1")
sys.exit(0)
