#!/usr/bin/env python3
"""video/make_script_doc.py — 최종 대본 문서(video_script.md)를 timeline / assign / srt / tts 에서 생성한다."""
import argparse, json, pathlib

ROOT = pathlib.Path(__file__).resolve().parent


def mmss(t):
    return f"{int(t // 60)}:{t % 60:05.2f}"


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--version", default="v1"); ap.add_argument("--total", type=float, default=None); a = ap.parse_args()
    tl = json.load(open(ROOT / "work" / "timeline.json", encoding="utf-8"))
    asg = json.load(open(ROOT / "work" / "assign.json", encoding="utf-8"))
    srt = (ROOT / "video_subtitles.srt").read_text(encoding="utf-8").strip().split(chr(10) + chr(10))
    subs = {int(b.split(chr(10))[0]): " ".join(b.split(chr(10))[2:]) for b in srt}
    tts = [l for l in (ROOT / "video_tts_lines.txt").read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
    total = a.total if a.total is not None else tl[-1]["end"] + 3.0
    L = [f"# BDG demo video — final script ({a.version} build, 2026-09-11)", "",
         f"Voice en-US-AndrewNeural, rate -4%. Segment = measured TTS + 1.2 s (min 6 s). Closing card 3.0 s with a 2 s fade. Total {total:.1f} s (limit 170 s; rule: less than three minutes).",
         "Audio: loudnorm 2-pass, target -16 LUFS integrated, -1.5 dBTP true peak.",
         "Source take: `C:/Users/ing79/Videos/2026-09-11 19-45-30.mp4` (live7, 54.7 s, 3840x2160). Terminal stills are frames of that take (see work/scenes.json); site stills are headless-Chrome screenshots at 2x of the committed site; cards are rendered HTML.",
         "Stills are 1920x950 content plus a 130 px band in the still's own background colour; subtitles live in the band and never cover content.", "",
         "| cue | start–end | hold s | speech s | stills | subtitle (on screen) | TTS (spoken) |", "|---|---|---|---|---|---|---|"]
    for s in tl:
        c = s["cue"]; g = asg["assign"][str(c)]
        L.append(f"| {c} | {mmss(s['start'])}–{mmss(s['end'])} | {s['hold']:.1f} | {s['speech']:.1f} | {', '.join(g['stills'])} | {subs[c]} | {tts[c-1]} |")
    L += [f"| tail | {mmss(tl[-1]['end'])}–{mmss(tl[-1]['end'] + 3.0)} | 3.0 | – | card_close | – | – |", "",
          "## Stills", "", "| name | source | crop | note |", "|---|---|---|---|"]
    for n, v in asg["stills"].items():
        src = v.get("frame_t", v.get("shot", "html card")); crop = v.get("box", v.get("box_css", "-"))
        L.append(f"| {n} | {src} | {crop} | {v.get('note', '')} |")
    L += ["", "## Files", "",
          "- `video_tts_lines.txt`, `video_subtitles.srt` — 1:1 cues (committed)",
          "- `make_tts.py` (edge-tts + ffprobe + timeline), `shoot_site.py` (site screenshots), `make_stills.py` (crops, cards, assign.json, scenes.json), `make_video.py` (assembly, loudnorm, ASS subtitles, fade), `make_script_doc.py` (this document)",
          "- `work/` — durations_*.json, timeline.json, scenes.json, assign.json, site_bboxes.json, term_geometry.json, stills/, shots/, verify/ (not committed)",
          "- `tts/final/en-US-AndrewNeural/line_NN.mp3` (not committed)", f"- `bdg_demo_{a.version}.mp4` (not committed)"]
    (ROOT / "video_script.md").write_text(chr(10).join(L) + chr(10), encoding="utf-8", newline=chr(10))
    print("video_script.md:", len(L), "lines, total", round(total, 2), "s")


if __name__ == "__main__":
    main()
