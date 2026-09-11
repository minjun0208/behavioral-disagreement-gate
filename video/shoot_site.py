#!/usr/bin/env python3
"""
video/shoot_site.py — 브라우저 파트 스틸: 헤드리스 Chrome 으로 site/ 를 열어 full-page PNG(dSF 2) 와 요소 좌표를 뽑는다.
  python video/shoot_site.py [--base http://127.0.0.1:8000/site/]
출력: video/work/shots/site_<name>_full.png, video/work/site_bboxes.json (CSS px; 픽셀 = ×2)
크롭은 이 좌표를 보고 다음 단계(assign)에서 정한다. 뷰포트 1920x1080, deviceScaleFactor 2.
"""
import argparse, json, pathlib
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent
OUT = ROOT / "work" / "shots"; OUT.mkdir(parents=True, exist_ok=True)


def bbox(pg, sel, idx=0):
    return pg.evaluate("""([sel, idx]) => { const es = document.querySelectorAll(sel); const e = es[idx]; if (!e) return null;
        const r = e.getBoundingClientRect(); return {x: r.x + window.scrollX, y: r.y + window.scrollY, w: r.width, h: r.height, text: (e.innerText||'').slice(0, 60)}; }""", [sel, idx])


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--base", default="http://127.0.0.1:8000/site/"); a = ap.parse_args()
    B = a.base; res = {}
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome")
        ctx = b.new_context(viewport={"width": 1920, "height": 1080}, device_scale_factor=2, reduced_motion="no-preference")
        pg = ctx.new_page(); errs = []
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)

        # a. hero — reveal 이 끝난 뒤 (pending 점이 없어야 한다)
        pg.goto(B + "index.html", wait_until="networkidle"); pg.wait_for_timeout(4500)
        pending = pg.locator("#hero-table .pending").count()
        rows = pg.locator("#hero-table tbody tr").count()
        pg.screenshot(path=str(OUT / "site_a_hero_full.png"), full_page=True)
        res["a_hero"] = {"pending_spans": pending, "rows": rows, "hero_section": bbox(pg, "section.entry", 0), "verdict": bbox(pg, "#hero-verdict"),
                         "table": bbox(pg, "#hero-table"), "summary": bbox(pg, "#hero-summary"), "page_h": pg.evaluate("document.documentElement.scrollHeight")}

        # b/c. session live7
        pg.goto(B + "session.html?id=live7", wait_until="networkidle"); pg.wait_for_timeout(1500)
        pg.screenshot(path=str(OUT / "site_bc_live7_full.png"), full_page=True)
        res["bc_live7"] = {"round1_section": bbox(pg, "#rounds > section.entry", 0), "round2_section": bbox(pg, "#rounds > section.entry", 1),
                           "question_card": bbox(pg, ".question", 0), "reference": bbox(pg, ".reference", 0),
                           "verdict_r1": bbox(pg, "#rounds .verdict", 0), "verdict_r2": bbox(pg, "#rounds .verdict", 1),
                           "opts": bbox(pg, ".options", 0), "acceptance_h3": bbox(pg, "#rounds h3", -1),
                           "ledger_section": bbox(pg, "#ledger-entry"), "ledger_table": bbox(pg, "#ledger"),
                           "page_h": pg.evaluate("document.documentElement.scrollHeight")}
        # ledger rows: question/reference/answer
        res["bc_live7"]["ledger_rows"] = pg.evaluate("""() => [...document.querySelectorAll('#ledger tbody tr')].map(t => { const r = t.getBoundingClientRect(); return {kind: t.children[1].textContent, y: r.y + window.scrollY, h: r.height}; })""")

        # d/e. evidence G4 ablation: ON then OFF, same page layout (full-page shots → same crop box)
        pg.goto(B + "evidence.html", wait_until="networkidle"); pg.wait_for_timeout(1000)
        pg.locator(".tabs button[data-tab='abl']").click(); pg.wait_for_timeout(1200)
        pg.screenshot(path=str(OUT / "site_d_g4_on_full.png"), full_page=True)
        tog = pg.locator(".toggle").first
        res["de_g4"] = {"toggle": bbox(pg, ".toggle"), "tab_panel_h2": bbox(pg, "h2", 0), "page_h": pg.evaluate("document.documentElement.scrollHeight"),
                        "tables": pg.evaluate("""() => [...document.querySelectorAll('table')].map(t => { const r = t.getBoundingClientRect(); return {y: r.y + window.scrollY, h: r.height, w: r.width, head: (t.innerText||'').slice(0,80)}; })"""),
                        "on_text": pg.locator("body").inner_text()[:0]}
        # 케이스 A 상태 텍스트 (ON)
        res["de_g4"]["on_statuses"] = pg.evaluate("""() => [...document.querySelectorAll('td, .status')].map(e => e.textContent).filter(t => /PASS|CODE_INCOMPLETE|NEEDS/.test(t)).slice(0, 12)""")
        tog.dispatch_event("pointerdown"); pg.wait_for_timeout(900)
        res["de_g4"]["aria_after"] = tog.get_attribute("aria-checked")
        res["de_g4"]["off_statuses"] = pg.evaluate("""() => [...document.querySelectorAll('td, .status')].map(e => e.textContent).filter(t => /PASS|CODE_INCOMPLETE|NEEDS/.test(t)).slice(0, 12)""")
        res["de_g4"]["page_h_off"] = pg.evaluate("document.documentElement.scrollHeight")
        pg.screenshot(path=str(OUT / "site_e_g4_off_full.png"), full_page=True)

        # f. provenance
        pg.goto(B + "provenance.html", wait_until="networkidle"); pg.wait_for_timeout(1200)
        pg.screenshot(path=str(OUT / "site_f_provenance_full.png"), full_page=True)
        res["f_prov"] = {"xb_section": bbox(pg, "section.entry", 1), "xb": bbox(pg, "#xb"), "canary_section": bbox(pg, "section.entry", 2),
                         "canary_table": bbox(pg, "#canary"), "page_h": pg.evaluate("document.documentElement.scrollHeight")}

        # g. scorer test
        pg.goto(B + "test/scorer_test.html", wait_until="networkidle"); pg.wait_for_timeout(7000)
        pg.screenshot(path=str(OUT / "site_g_scorer_full.png"), full_page=True)
        res["g_scorer"] = {"sum": bbox(pg, "#sum"), "sum_text": pg.locator("#sum").inner_text(), "table": bbox(pg, "#t"), "page_h": pg.evaluate("document.documentElement.scrollHeight")}
        res["console_errors"] = errs
        b.close()
    (ROOT / "work" / "site_bboxes.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=1)[:6000])


if __name__ == "__main__":
    main()
