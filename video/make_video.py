#!/usr/bin/env python3
"""
video/make_video.py — 조립: 스틸 + 구간 음성 → 영상 트랙/음성 트랙 → 합치기 → ASS 자막 번인 → 페이드아웃.
  python video/make_video.py [--out video/bdg_demo_v1.mp4] [--voice en-US-AndrewNeural] [--tag final]

입력: work/timeline.json (cue, start, end, hold), work/assign.json (cue → stills, 자막 위치), video_subtitles.srt,
      tts/<tag>/<voice>/line_NN.mp3, work/stills/*.png (1920x1080)
원칙: 구간 길이는 timeline.json 의 hold 그대로. 스틸이 여러 개면 hold 를 균등 분할. 마지막에 TAIL 초 카드 + 페이드아웃.
자막: drawtext 대신 ASS(libass). 스틸 아래 130px 띠 안에만 놓이고, 스타일은 스틸 종류로 정한다 (터미널 Light, 종이 Dark). cue 가 스틸 여러 장이면 스틸 경계에서 Dialogue 를 나눈다.
"""
import argparse, json, pathlib, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent
WORK = ROOT / "work"
FPS = 30
TAIL = 3.0
FADE = 2.0


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        print("FAILED:", " ".join(str(c) for c in cmd)); print(r.stderr[-3000:]); sys.exit(1)
    return r


def probe_duration(p):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(p)],
                                capture_output=True, text=True).stdout.strip())


def ass_time(t):
    h = int(t // 3600); m = int(t % 3600 // 60); s = t % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def measure_loudness(path, I=-16.0, TP=-1.5, LRA=11.0):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", f"loudnorm=I={I}:TP={TP}:LRA={LRA}:print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True)
    return json.loads(r.stderr[r.stderr.rfind("{"):r.stderr.rfind("}") + 1])


def loudnorm_two_pass(src, dst, I=-16.0, TP=-1.5, LRA=11.0):
    """1차: 측정. 2차: 측정값을 넣어 linear 정규화 (유튜브 -14 LUFS 정규화 대비 -16 LUFS / -1.5 dBTP 목표)."""
    m = measure_loudness(src, I, TP, LRA)
    af = (f"loudnorm=I={I}:TP={TP}:LRA={LRA}:measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
          f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true:print_format=json")
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-y", "-i", str(src), "-af", af, "-ar", "48000", "-c:a", "pcm_s16le", str(dst)], capture_output=True, text=True)
    if r.returncode:
        print(r.stderr[-2000:]); sys.exit(1)
    m2 = json.loads(r.stderr[r.stderr.rfind("{"):r.stderr.rfind("}") + 1])
    return m, m2


def srt_texts(path):
    blocks = [b for b in path.read_text(encoding="utf-8").strip().split(chr(10) + chr(10)) if b.strip()]
    return {int(b.split(chr(10))[0]): " ".join(b.split(chr(10))[2:]).strip() for b in blocks}


def build_ass(tl, assign, texts, out, stills_meta=None):
    # 1080p 기준. 자막은 스틸 아래 130px 띠(배경색) 안에 놓인다. Light = 흰 글자+검은 외곽선(터미널), Dark = 잉크색 글자(종이).
    head = [
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 1920", "PlayResY: 1080", "WrapStyle: 0", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Light,Arial,34,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,1.6,0,2,70,70,26,1",
        "Style: Dark,Arial,34,&H00171A1A,&H000000FF,&H00FBFAF7,&H00FBFAF7,0,0,0,0,100,100,0,0,1,0,0,2,70,70,26,1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    ev = []
    for seg in tl:
        c = str(seg["cue"]); stills = assign[c]["stills"]; per = seg["hold"] / len(stills)
        txt = texts[seg["cue"]].replace("{", "(").replace("}", ")")
        for k, name in enumerate(stills):
            kind = (stills_meta or {}).get(name, {}).get("kind", "site")
            style = "Light" if kind == "terminal" else "Dark"        # 스틸의 배경에 따라: 터미널은 흰 글자, 종이는 잉크색
            t0 = seg["start"] + k * per; t1 = seg["start"] + (k + 1) * per - (0.05 if k == len(stills) - 1 else 0.0)
            ev.append(f"Dialogue: 0,{ass_time(t0)},{ass_time(t1)},{style},,0,0,0,,{txt}")
    out.write_text(chr(10).join(head + ev) + chr(10), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "bdg_demo_v1.mp4"))
    ap.add_argument("--voice", default="en-US-AndrewNeural")
    ap.add_argument("--tag", default="final")
    ap.add_argument("--no-subs", action="store_true")
    a = ap.parse_args()
    tl = json.loads((WORK / "timeline.json").read_text(encoding="utf-8"))
    assign_all = json.loads((WORK / "assign.json").read_text(encoding="utf-8")); assign = assign_all["assign"]; stills_meta = assign_all["stills"]
    texts = srt_texts(ROOT / "video_subtitles.srt")
    seg_dir = WORK / "segs"; seg_dir.mkdir(exist_ok=True)

    # 1. 영상 트랙: concat demuxer (이미지 + duration)
    lines = []
    for seg in tl:
        c = str(seg["cue"]); stills = assign[c]["stills"]; per = seg["hold"] / len(stills)
        for s in stills:
            p = (WORK / "stills" / f"{s}.png").resolve()
            assert p.exists(), p
            lines += [f"file '{p.as_posix()}'", f"duration {per:.3f}"]
    tail_p = (WORK / "stills" / f"{assign['tail']['stills'][0]}.png").resolve()
    lines += [f"file '{tail_p.as_posix()}'", f"duration {TAIL:.3f}", f"file '{tail_p.as_posix()}'"]   # 마지막 항목 반복: concat demuxer 규칙
    (WORK / "concat_video.txt").write_text(chr(10).join(lines) + chr(10), encoding="utf-8")
    total = tl[-1]["end"] + TAIL
    run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(WORK / "concat_video.txt"),
         "-vf", f"fps={FPS},format=yuv420p", "-t", f"{total:.3f}", "-c:v", "libx264", "-preset", "medium", "-tune", "stillimage", "-crf", "18", str(WORK / "video_track.mp4")])
    print("video track", round(probe_duration(WORK / "video_track.mp4"), 3), "s (target", round(total, 3), ")")

    # 2. 음성 트랙: 구간별 mp3 → hold 길이로 패딩/자르기 → concat, 마지막 TAIL 무음
    alist = []
    for seg in tl:
        i = seg["cue"]; src = ROOT / "tts" / a.tag / a.voice / f"line_{i:02d}.mp3"; dst = seg_dir / f"a_{i:02d}.wav"
        run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-af", f"apad,atrim=0:{seg['hold']:.3f}", "-ar", "48000", "-ac", "2", str(dst)])
        alist.append(dst)
    sil = seg_dir / "a_tail.wav"
    run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", f"{TAIL:.3f}", str(sil)])
    alist.append(sil)
    (WORK / "concat_audio.txt").write_text(chr(10).join(f"file '{p.resolve().as_posix()}'" for p in alist) + chr(10), encoding="utf-8")
    run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(WORK / "concat_audio.txt"), "-c:a", "pcm_s16le", str(WORK / "audio_track.wav")])
    print("audio track", round(probe_duration(WORK / "audio_track.wav"), 3), "s")
    m1, m2 = loudnorm_two_pass(WORK / "audio_track.wav", WORK / "audio_norm.wav")
    print(f"loudnorm pass 1 (measured): I={m1['input_i']} LUFS  TP={m1['input_tp']} dBTP  LRA={m1['input_lra']}  thresh={m1['input_thresh']}  offset={m1['target_offset']}")
    print(f"loudnorm pass 2 (applied, {m2['normalization_type']}): I={m2['output_i']} LUFS  TP={m2['output_tp']} dBTP  LRA={m2['output_lra']}")

    # 3. 자막 ASS + 합치기 + 페이드아웃
    build_ass(tl, assign, texts, WORK / "subs.ass", stills_meta)
    ass_path = (WORK / "subs.ass").resolve().as_posix().replace(":", r"\:")
    vf = f"fade=t=out:st={total - FADE:.3f}:d={FADE:.3f}"
    if not a.no_subs:
        vf = f"ass='{ass_path}'," + vf
    run(["ffmpeg", "-v", "error", "-y", "-i", str(WORK / "video_track.mp4"), "-i", str(WORK / "audio_norm.wav"),
         "-vf", vf, "-af", f"afade=t=out:st={total - FADE:.3f}:d={FADE:.3f}", "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-shortest", a.out])
    d = probe_duration(a.out)
    mf = measure_loudness(a.out)
    print(f"FINAL loudness (after fade + AAC): I={mf['input_i']} LUFS  TP={mf['input_tp']} dBTP  LRA={mf['input_lra']}")
    print("FINAL", a.out, round(d, 3), "s", "| limit 170:", "OK" if d <= 170 else "OVER")


if __name__ == "__main__":
    main()
