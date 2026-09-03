#!/usr/bin/env python3
"""
canary_check.py — 격리 검증 (v0.2.0)

grades/<run_id>/canary.txt 의 문자열이 게이트 영역 어디에도 없어야 한다.
기본 스캔: runs/<run_id>/  +  verdicts/<run_id>/ (있으면)
확장:      --extra <path> (반복 가능) — 향후 ledger/, clarify/ 등 명료화 루프 경로 추가용
exit 0 = 격리 유지 / 1 = 누출 / 2 = 사용법 오류
"""
import datetime as dt
import json
import pathlib
import sys


def main():
    if len(sys.argv) < 2:
        print("usage: canary_check.py <run_id> [--runs runs] [--grades grades] [--verdicts verdicts] [--extra PATH]...")
        sys.exit(2)
    run_id = sys.argv[1]
    def opt(flag, default):
        return pathlib.Path(sys.argv[sys.argv.index(flag) + 1]) if flag in sys.argv else pathlib.Path(default)
    runs_root, grades_root, verd_root = opt("--runs", "runs"), opt("--grades", "grades"), opt("--verdicts", "verdicts")
    extras = [pathlib.Path(sys.argv[i + 1]) for i, a in enumerate(sys.argv) if a == "--extra"]

    canary = (grades_root / run_id / "canary.txt").read_text(encoding="utf-8").strip().encode("utf-8")
    scan_roots = [runs_root / run_id]
    if (verd_root / run_id).exists():
        scan_roots.append(verd_root / run_id)
    scan_roots += [e for e in extras if e.exists()]

    n_files, hits = 0, []
    for root in scan_roots:
        for f in sorted(root.rglob("*")):
            if not f.is_file():
                continue
            n_files += 1
            try:
                if canary in f.read_bytes():
                    hits.append(str(f))
            except OSError:
                continue

    rec = {"run_id": run_id,
           "ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
           "kind": "canary_check", "scanned_paths": [str(r) for r in scan_roots],
           "files_scanned": n_files, "canary_found": bool(hits), "hits": hits}
    gpath = grades_root / run_id / "grade.jsonl"
    last_seq = -1
    if gpath.exists():
        for line in open(gpath, encoding="utf-8"):
            if line.strip():
                last_seq = json.loads(line)["seq"]
    rec["seq"] = last_seq + 1
    with open(gpath, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(json.dumps({k: rec[k] for k in ("scanned_paths", "files_scanned", "canary_found", "hits")}, ensure_ascii=False))
    sys.exit(1 if hits else 0)


if __name__ == "__main__":
    main()
