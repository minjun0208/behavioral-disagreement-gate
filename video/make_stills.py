#!/usr/bin/env python3
"""
video/make_stills.py — 스틸 제작: 터미널 4K 프레임 크롭(+터미널 배경색 패딩으로 16:9), 사이트 full-page 크롭, 카드 3장 렌더.
출력: video/work/stills/<name>.png (전부 1920x1080), video/work/assign.json (cue → stills), video/work/scenes.json

크롭 원칙: 좌표는 실측(term_geometry.json, site_bboxes.json)에서 정했다. 16:9 가 안 맞는 영역은 자르지 않고
배경색(터미널 #0B0B0B, 사이트 #FBFAF7)으로 패딩한다 — 글자를 잘라내지 않기 위해서다. 작업표시줄(y>=2088)·탭바(y<47)는 제외.
"""
import json, pathlib, subprocess
from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parent
W4, H4 = ROOT / "work" / "frames" / "4k", None
OUT = ROOT / "work" / "stills"; OUT.mkdir(parents=True, exist_ok=True)
SITE = ROOT / "work" / "shots"
TERM_BG = (11, 11, 11)
PAPER = (251, 250, 247)
FINAL = (1920, 1080)
CONTENT = (1920, 950)   # 아래 130px 은 자막 띠 (스틸의 배경색). 자막이 화면 글자를 가리지 않는다
BAND = FINAL[1] - CONTENT[1]

# 원본 녹화의 장면 시각 (Enter = 3.9 s 추정; round-1 출력 18.2 s 실측)
SCENES = {
    "source": "C:/Users/ing79/Videos/2026-09-11 19-45-30.mp4",
    "enter_s": 3.9,
    "events": [
        {"t": 10.0, "what": "command entered, waiting (no output yet)", "font": "large"},
        {"t": 18.2, "what": "round 1 verdict line + question card + 'reference search via Tavily ...' pending appear"},
        {"t": 19.5, "what": "still: question card (large font, no pointer)", "font": "large"},
        {"t": 21.2, "what": "'ok · 3 results · 3.0s' + reference block appear (pointer over the block from 21.5 to 27.5)"},
        {"t": 24.0, "what": "user zooms the terminal font out (twice, by ~27 s)"},
        {"t": 31.5, "what": "still: '> option id: o2' typed, before Enter (small font, no pointer)", "font": "small"},
        {"t": 32.6, "what": "Enter → survivors=['c1', 'c2'] classes_left=1; round 2 starts"},
        {"t": 41.5, "what": "still: reference block + o2 + survivors (small font, no pointer)", "font": "small"},
        {"t": 48.8, "what": "round 2 PASS ['REGRESSION_FAILURE'] + SESSION live7: PASS appear"},
        {"t": 51.5, "what": "still: round 2 + SESSION lines (small font, no pointer)", "font": "small"},
    ],
}


def pad_to_16_9(im: Image.Image, bg, align_x="left") -> Image.Image:
    """콘텐츠 비율 1920:950 에 맞춰 패딩(배경색), 그 뒤 아래에 자막 띠를 붙여 16:9 로."""
    w, h = im.size
    cw, ch = CONTENT
    if w / h < cw / ch:
        tw, th = round(h * cw / ch), h
    else:
        tw, th = w, round(w * ch / cw)
    canvas = Image.new("RGB", (tw, th), bg)
    x = 0 if align_x == "left" else (tw - w) // 2
    canvas.paste(im, (x, (th - h) // 2))
    canvas = canvas.resize(CONTENT, Image.LANCZOS)
    full = Image.new("RGB", FINAL, bg); full.paste(canvas, (0, 0))
    return full


def term_still(name, frame_t, box, note):
    """box = (x, y, w, h) in 4K px. 잘라내고 배경 패딩 → 1920x1080."""
    src = Image.open(W4 / f"f_{frame_t}.png").convert("RGB")
    x, y, w, h = box
    assert y >= 47 and y + h <= 2088, f"{name}: box touches tab bar or taskbar"
    im = src.crop((x, y, x + w, y + h))
    scale = CONTENT[1] / max(h, w * CONTENT[1] / CONTENT[0])
    im = pad_to_16_9(im, TERM_BG, "left")
    im.save(OUT / f"{name}.png")
    return {"kind": "terminal", "frame_t": frame_t, "box": box, "scale_to_1080p": round(scale, 3), "note": note}


def site_still(name, shot, box_css, note):
    """box_css = (x, y, w, h) in CSS px on the full-page shot (dSF 2 → ×2). 페이지 밖은 종이색 패딩."""
    src = Image.open(SITE / shot).convert("RGB")
    x, y, w, h = [v * 2 for v in box_css]
    canvas = Image.new("RGB", (w, h), PAPER)
    part = src.crop((x, y, min(x + w, src.size[0]), min(y + h, src.size[1])))
    canvas.paste(part, (0, 0))
    scale = CONTENT[1] / max(h, w * CONTENT[1] / CONTENT[0])
    pad_to_16_9(canvas, PAPER, "center").save(OUT / f"{name}.png")
    return {"kind": "site", "shot": shot, "box_css": box_css, "scale_to_1080p": round(scale, 3), "note": note}


CARD_CSS = """
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&display=swap">
<style>
  html,body{margin:0;background:#FBFAF7;color:#1A1A17;width:1920px;height:950px;overflow:hidden}
  .wrap{position:absolute;left:200px;top:0;width:1520px;height:950px;display:flex;flex-direction:column;justify-content:center;gap:28px}
  .mono{font-family:"IBM Plex Mono",monospace}.serif{font-family:"Source Serif 4",Georgia,serif}
  .big{font-size:88px;font-weight:500;letter-spacing:-0.02em}.mid{font-size:44px}.small{font-size:30px;color:#5C5C55}
  .rule{border-top:1px solid #1A1A17;width:100%}.ann{color:#2F5FD8}.red{background:#E8E4DA;padding:.1em .35em;border-radius:2px;color:#5C5C55}
  .cols{display:flex;gap:48px}.col{flex:1;border-left:2px solid #1A1A17;padding-left:22px}
  .col h3{font-family:"IBM Plex Mono",monospace;font-size:40px;margin:0 0 10px;font-weight:500}.col p{font-family:"Source Serif 4",Georgia,serif;font-size:30px;margin:0;line-height:1.35}
</style>"""

CARDS = {
    "card_intro": CARD_CSS + """
<div class="wrap">
  <div class="mono big">round_half(x=-12.5)</div>
  <div class="mono mid">→ <span class="ann">-12</span>&nbsp;&nbsp;or&nbsp;&nbsp;<span class="ann">-13</span> ?</div>
  <div class="rule"></div>
  <div class="serif mid">Tests pass. The agent says: <i>done</i>.</div>
  <div class="serif small">The issue never said which. Two plausible patches disagree, and the tests never asked.</div>
</div>""",
    "card_arch": CARD_CSS + """
<div class="wrap">
  <div class="serif" style="font-size:52px;font-weight:600;letter-spacing:-0.02em">Behavioral Disagreement Gate</div>
  <div class="cols">
    <div class="col"><h3>runner</h3><p>three candidate patches from Nebius Token Factory, executed on the same generated inputs in isolated ConTree sandbox branches; every observation goes to a trace</p></div>
    <div class="col"><h3>scorer</h3><p>reads only the trace; deterministic verdict and a decision hash; any two candidates behaving differently withholds approval</p></div>
    <div class="col"><h3>loop</h3><p>the disagreeing input goes back to the user as a question; the answer becomes a ledger decision and an acceptance test for the next round</p></div>
  </div>
  <div class="mono small">Nebius Token Factory · NVIDIA Nemotron-3 Ultra · Qwen3 · DeepSeek-V4 · ConTree sandboxes</div>
</div>""",
    "card_close": CARD_CSS + """
<div class="wrap">
  <div class="serif" style="font-size:52px;font-weight:600;letter-spacing:-0.02em">When candidates disagree, ask. Never guess.</div>
  <div class="rule"></div>
  <div class="mono mid">github.com/minjun0208/behavioral-disagreement-gate</div>
  <div class="mono mid">minjun0208.github.io/behavioral-disagreement-gate/</div>
  <div class="mono small">session.html?id=live7 — the session in this video</div>
  <div class="serif small">Apache-2.0 · static site, no build step · available until judging ends, 15 December 2026</div>
</div>""",
}


def render_cards():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome")
        ctx = b.new_context(viewport={"width": 1920, "height": 950}, device_scale_factor=2)
        pg = ctx.new_page()
        for name, html in CARDS.items():
            pg.set_content("<!doctype html><html><head><meta charset='utf-8'>" + html + "</html>")
            pg.wait_for_timeout(1500)   # web fonts
            pg.screenshot(path=str(OUT / f"{name}_4k.png"))
            im = Image.open(OUT / f"{name}_4k.png").convert("RGB").resize(CONTENT, Image.LANCZOS)
            full = Image.new("RGB", FINAL, PAPER); full.paste(im, (0, 0)); full.save(OUT / f"{name}.png")
        b.close()


def main():
    stills = {}
    # ── terminal (4K px boxes; measured: tab bar ends y=47, taskbar starts y=2088; large font pitch 72, small 42)
    stills["t1_prompt"] = term_still("t1_prompt", "10.0", (0, 47, 3800, 2041), "command entered, waiting; full terminal minus tab bar and taskbar")
    stills["t2_round1"] = term_still("t2_round1", "19.5", (0, 47, 3800, 2041), "round 1 verdict + question card + reference search pending")
    stills["t3_reference"] = term_still("t3_reference", "41.5", (0, 70, 2060, 1120), "'reference search ... ok · 3 results · 3.0s' + 3 sources + fetched line (small font, before the o2 line)")
    stills["t4_answer"] = term_still("t4_answer", "41.5", (0, 70, 2090, 1210), "same block + '> option id: o2' + survivors line")
    stills["t5_round2"] = term_still("t5_round2", "51.5", (0, 380, 2062, 1165), "sources 2-3 … o2 … round 2 PASS ['REGRESSION_FAILURE'] … SESSION live7: PASS")
    # ── site (CSS px boxes on full-page shots)
    stills["s_a_hero"] = site_still("s_a_hero", "site_a_hero_full.png", (800, 1072, 800, 450), "hero: three environments, one decision_core; reveal finished")
    stills["s_cands"] = site_still("s_cands", "site_bc_live7_full.png", (830, 315, 747, 420), "live7 round 1 candidates: c1 Nemotron-3-Ultra, c2 Qwen3-235B, c3 DeepSeek-V4 with their patch sources")
    stills["s_b1_verdict1"] = site_still("s_b1_verdict1", "site_bc_live7_full.png", (690, 795, 782, 440), "live7 round 1 verdict: NEEDS_CLARIFICATION, witness, decision_core matches Python")
    stills["s_b2_reference"] = site_still("s_b2_reference", "site_bc_live7_full.png", (700, 1645, 747, 420), "live7 question card: Tavily reference block header + sources")
    stills["s_b3_ledger"] = site_still("s_b3_ledger", "site_bc_live7_full.png", (700, 3300, 747, 420), "live7 ledger: question → reference → answer → decision")
    stills["s_c_verdict2"] = site_still("s_c_verdict2", "site_bc_live7_full.png", (700, 2790, 747, 420), "live7 round 2: PASS, dropped c3, acceptance test in force")
    stills["s_c2_pass_close"] = site_still("s_c2_pass_close", "site_bc_live7_full.png", (800, 2815, 640, 360), "round 2 PASS block close-up: 'no counterexample found in the searched range', not searched")
    stills["s_d_g4_on"] = site_still("s_d_g4_on", "site_d_g4_on_full.png", (700, 470, 747, 420), "evidence G4 ablation, toggle ON: case A CODE_INCOMPLETE")
    stills["s_e_g4_off"] = site_still("s_e_g4_off", "site_e_g4_off_full.png", (700, 470, 747, 420), "same box, toggle OFF: case A PASS (three violators approved)")
    stills["s_f_provenance"] = site_still("s_f_provenance", "site_f_provenance_full.png", (700, 480, 747, 420), "provenance: cross-backend table + canary table")
    stills["s_f2_limits"] = site_still("s_f2_limits", "site_f_provenance_full.png", (830, 2530, 747, 420), "provenance: what this site can and cannot show")
    stills["s_g_scorer"] = site_still("s_g_scorer", "site_g_scorer_full.png", (0, 100, 747, 420), "scorer_test: 84/84 runs")
    render_cards()
    for c in CARDS: stills[c] = {"kind": "card"}

    # cue → stills (여러 개면 hold 를 균등 분할). 자막 위치: top = 핵심 줄이 화면 아래에 있을 때
    assign = {
        "1": {"stills": ["card_intro"], "sub": "dark"},
        "2": {"stills": ["card_arch"], "sub": "dark"},
        "3": {"stills": ["t1_prompt", "s_cands"], "sub": "light"},
        "4": {"stills": ["t2_round1"], "sub": "light"},
        "5": {"stills": ["t3_reference"], "sub": "light"},
        "6": {"stills": ["t4_answer"], "sub": "light"},
        "7": {"stills": ["t5_round2"], "sub": "light"},
        "8": {"stills": ["s_b1_verdict1"], "sub": "dark"},
        "9": {"stills": ["s_b2_reference", "s_b3_ledger"], "sub": "dark"},
        "10": {"stills": ["s_c_verdict2", "s_g_scorer"], "sub": "dark"},
        "11": {"stills": ["s_a_hero"], "sub": "dark"},
        "12": {"stills": ["s_d_g4_on"], "sub": "dark"},
        "13": {"stills": ["s_e_g4_off"], "sub": "dark"},
        "14": {"stills": ["s_d_g4_on"], "sub": "dark"},
        "15": {"stills": ["s_c2_pass_close"], "sub": "dark"},
        "16": {"stills": ["s_f_provenance", "s_f2_limits"], "sub": "dark"},
        "17": {"stills": ["card_close"], "sub": "dark"},
        "tail": {"stills": ["card_close"]},
    }
    json.dump({"stills": stills, "assign": assign}, open(ROOT / "work" / "assign.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(SCENES, open(ROOT / "work" / "scenes.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for k, v in stills.items():
        print(f"{k:16s} {v.get('kind'):8s} scale={v.get('scale_to_1080p','-')}  {v.get('note','')[:70]}")


if __name__ == "__main__":
    main()
