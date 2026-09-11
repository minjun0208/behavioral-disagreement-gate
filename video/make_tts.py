#!/usr/bin/env python3
"""
video/make_tts.py — edge-tts 로 구간별 음성을 만들고 ffprobe 로 실측한다. (영상 제작 도구. 제품 의존성 아님)

  python video/make_tts.py                       # 기본 3 음성, video/video_tts_lines.txt → video/tts/<voice>/line_NN.mp3
  python video/make_tts.py --samples             # 제품명 발음 샘플 (voice 별 + 표기 변형)
  python video/make_tts.py --voices en-US-AriaNeural --srt video/video_subtitles.srt   # 실측으로 SRT 타임코드 역산

원칙: 타임라인은 추정하지 않고 역산한다. 구간 = TTS 실측 + PAD 초, 최소 MIN_SEG 초. 총합 + TAIL 이 영상 길이.
캐시: line_NN.txt 에 원문을 같이 저장하고, 원문·rate 가 같으면 재생성하지 않는다 (바뀐 줄만 다시 만든다).
"""
import argparse
import asyncio
import hashlib
import json
import pathlib
import subprocess
import sys

import edge_tts

ROOT = pathlib.Path(__file__).resolve().parent
PAD = 1.2          # 구간 여유 (초)
MIN_SEG = 6.0      # 구간 최소 길이 (초)
TAIL = 3.0         # 마지막 카드 유지 + 페이드아웃 (초)
LIMIT = 170.0      # 2:50. 규정 "less than three (3) minutes" 에 인코딩 여유
DEFAULT_VOICES = ["en-US-AriaNeural", "en-US-GuyNeural", "en-US-AndrewNeural"]

SAMPLE_SENTENCE = ("Nebius Token Factory serves NVIDIA Nemotron 3 Ultra, Qwen 3, and DeepSeek V4. "
                   "The candidates run in ConTree sandbox branches, and Tavily, Nebius's search API, provides the references. "
                   "Round half of minus twelve point five: minus twelve, or minus thirteen. Gate four dropped one candidate. Pass.")
VARIANTS = {
    "nebius": ["Nebius", "Neh-bee-us", "Nebbius"],
    "nemotron": ["Nemotron", "Nemo-tron", "Nemotron 3 Ultra"],
    "contree": ["ConTree", "con-tree", "Con Tree"],
    "tavily": ["Tavily", "Tav-ih-lee", "Tavilly"],
    "qwen": ["Qwen 3", "Quen 3", "Kwen 3"],
    "deepseek": ["DeepSeek V4", "Deep Seek V four"],
}


def ffprobe_seconds(p: pathlib.Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                          "default=noprint_wrappers=1:nokey=1", str(p)], capture_output=True, text=True).stdout.strip()
    return round(float(out), 3)


def read_lines(path: pathlib.Path) -> list[str]:
    return [l.strip() for l in path.read_text(encoding="utf-8").splitlines() if l.strip() and not l.lstrip().startswith("#")]


def key(text: str, voice: str, rate: str) -> str:
    return hashlib.sha256(f"{voice}|{rate}|{text}".encode("utf-8")).hexdigest()[:16]


async def synth(text: str, voice: str, rate: str, out: pathlib.Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    await edge_tts.Communicate(text, voice, rate=rate).save(str(out))


async def synth_cached(text: str, voice: str, rate: str, out: pathlib.Path, force: bool) -> bool:
    side = out.with_suffix(".txt")
    k = key(text, voice, rate)
    if out.exists() and side.exists() and not force and side.read_text(encoding="utf-8").split(chr(10))[0] == k:
        return False
    await synth(text, voice, rate, out)
    side.write_text(k + chr(10) + text + chr(10), encoding="utf-8")
    return True


def fmt(t: float) -> str:
    h = int(t // 3600); m = int(t % 3600 // 60); s = t % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")


def timeline(durs: list[float]) -> list[dict]:
    t = 0.0; segs = []
    for i, d in enumerate(durs, 1):
        L = max(MIN_SEG, d + PAD)
        segs.append({"cue": i, "start": round(t, 3), "end": round(t + L, 3), "speech": d, "hold": round(L, 3)})
        t += L
    return segs


def rewrite_srt(srt: pathlib.Path, segs: list[dict]) -> None:
    blocks = [b for b in srt.read_text(encoding="utf-8").strip().split(chr(10) + chr(10)) if b.strip()]
    assert len(blocks) == len(segs), f"srt cues {len(blocks)} != tts lines {len(segs)}"
    out = []
    for b, s in zip(blocks, segs):
        lines = b.split(chr(10))
        out.append(chr(10).join([lines[0], f"{fmt(s['start'])} --> {fmt(s['end'])}"] + lines[2:]))
    srt.write_text((chr(10) + chr(10)).join(out) + chr(10), encoding="utf-8", newline=chr(10))


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lines", default=str(ROOT / "video_tts_lines.txt"))
    ap.add_argument("--voices", default=",".join(DEFAULT_VOICES))
    ap.add_argument("--rate", default="-8%")
    ap.add_argument("--tag", default="main", help="durations_<tag>.json")
    ap.add_argument("--samples", action="store_true")
    ap.add_argument("--srt", default=None, help="이 SRT 의 타임코드를 첫 voice 의 실측으로 다시 쓴다")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    voices = [v for v in a.voices.split(",") if v]
    work = ROOT / "work"; work.mkdir(exist_ok=True)

    if a.samples:
        sdir = ROOT / "tts" / "samples"
        for v in voices:
            await synth_cached(SAMPLE_SENTENCE, v, a.rate, sdir / f"{v}_names.mp3", a.force)
            print("sample", sdir / f"{v}_names.mp3")
        v0 = voices[0]
        for name, forms in VARIANTS.items():
            for j, w in enumerate(forms):
                out = sdir / f"{v0}_{name}_{j}.mp3"
                await synth_cached(f"This is {w}. {w} again.", v0, a.rate, out, a.force)
                print("variant", out.name, "<-", repr(w))
        return 0

    texts = read_lines(pathlib.Path(a.lines))
    report = {"lines_file": a.lines, "rate": a.rate, "pad": PAD, "min_seg": MIN_SEG, "tail": TAIL, "limit": LIMIT, "n_lines": len(texts), "voices": {}}
    for v in voices:
        durs = []; made = 0
        for i, t in enumerate(texts, 1):
            out = ROOT / "tts" / a.tag / v / f"line_{i:02d}.mp3"
            made += await synth_cached(t, v, a.rate, out, a.force)
            durs.append(ffprobe_seconds(out))
        segs = timeline(durs)
        total = segs[-1]["end"] + TAIL
        report["voices"][v] = {"generated": made, "speech_total": round(sum(durs), 2), "segments_total": round(segs[-1]["end"], 2),
                               "video_total": round(total, 2), "over_limit_by": round(max(0.0, total - LIMIT), 2),
                               "lines": [{"n": i + 1, "sec": d, "words": len(texts[i].split()), "hold": segs[i]["hold"]} for i, d in enumerate(durs)]}
        print(f"{v}: generated {made}, speech {sum(durs):.1f}s, segments {segs[-1]['end']:.1f}s, video {total:.1f}s (limit {LIMIT:.0f}s, over by {max(0.0, total - LIMIT):.1f}s)")
        (work / f"timeline_{a.tag}_{v}.json").write_text(json.dumps(segs, indent=1), encoding="utf-8")
    (work / f"durations_{a.tag}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    if a.srt:
        first = voices[0]
        segs = json.loads((work / f"timeline_{a.tag}_{first}.json").read_text(encoding="utf-8"))
        rewrite_srt(pathlib.Path(a.srt), segs)
        print("srt timecodes rewritten from", first)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
