# BDG demo video — final script (v2 build, 2026-09-11)

Voice en-US-AndrewNeural, rate -4%. Segment = measured TTS + 1.2 s (min 6 s). Closing card 3.0 s with a 2 s fade. Total 169.5 s (limit 170 s; rule: less than three minutes).
Audio: loudnorm 2-pass, target -16 LUFS integrated, -1.5 dBTP true peak.
Source take: `C:/Users/ing79/Videos/2026-09-11 19-45-30.mp4` (live7, 54.7 s, 3840x2160). Terminal stills are frames of that take (see work/scenes.json); site stills are headless-Chrome screenshots at 2x of the committed site; cards are rendered HTML.
Stills are 1920x950 content plus a 130 px band in the still's own background colour; subtitles live in the band and never cover content.

| cue | start–end | hold s | speech s | stills | subtitle (on screen) | TTS (spoken) |
|---|---|---|---|---|---|---|
| 1 | 0:00.00–0:14.14 | 14.1 | 12.9 | card_intro | A coding agent writes a patch, runs its tests, and says "done". But the issue never said whether round_half(x=-12.5) is -12 or -13, and the tests never asked. | A coding agent writes a patch, runs its tests, and says: done. But the issue never said whether round half of minus twelve point five is minus twelve or minus thirteen, and the tests never asked. |
| 2 | 0:14.14–0:28.18 | 14.0 | 12.8 | card_arch | The Behavioral Disagreement Gate asks three models for patches through Nebius Token Factory, runs them on the same inputs in isolated ConTree sandboxes, and withholds approval while any two disagree. | The Behavioral Disagreement Gate asks three models for patches through Nebius Token Factory, runs them on the same inputs in isolated ConTree sandboxes, and withholds approval while any two disagree. |
| 3 | 0:28.18–0:44.42 | 16.2 | 15.0 | t1_prompt, s_cands | A live session. Each candidate is one chat completion to Token Factory's OpenAI-compatible endpoint: NVIDIA Nemotron-3 Ultra, Qwen3, DeepSeek-V4, temperature 0.8, fixed seed per slot. | A live session. Each candidate is one chat completion to Token Factory's OpenAI-compatible endpoint: NVIDIA Nemotron 3 Ultra, Qwen 3, DeepSeek V4, temperature zero point eight, fixed seed per slot. |
| 4 | 0:44.42–0:56.98 | 12.6 | 11.4 | t2_round1 | Round 1: the candidates disagree at round_half(x=-12.5): -12 or -13. No model names, no vote counts, always "not sure". | Round one: the candidates disagree at round half of minus twelve point five: minus twelve, or minus thirteen. No model names, no vote counts, always not sure. |
| 5 | 0:56.98–1:09.05 | 12.1 | 10.9 | t3_reference | Before answering, one runtime call to Tavily, Nebius's search API, for the convention behind this disagreement. Sources shown, never read by the verdict. | Before answering, one runtime call to Tavily, Nebius's search API, for the convention behind this disagreement. Sources shown, never read by the verdict. |
| 6 | 1:09.05–1:16.66 | 7.6 | 6.4 | t4_answer | I choose o2, -13. The answer becomes a ledger decision and an acceptance test. | I choose option two, minus thirteen. The answer becomes a ledger decision and an acceptance test. |
| 7 | 1:16.66–1:24.74 | 8.1 | 6.9 | t5_round2 | Round 2: one candidate still violates that test and is dropped by the regression gate. PASS. | Round two: one candidate still violates that test and is dropped by the regression gate. Pass. |
| 8 | 1:24.74–1:32.69 | 7.9 | 6.7 | s_b1_verdict1 | On the public site, the browser re-scores the recorded trace and prints the same decision_core hash as Python. | On the public site, the browser re-scores the recorded trace and prints the same decision hash as Python. |
| 9 | 1:32.69–1:40.63 | 7.9 | 6.7 | s_b2_reference, s_b3_ledger | The Tavily block is labelled "not read by the verdict"; in the ledger it sits between question and answer. | The Tavily block is labelled not read by the verdict; in the ledger it sits between question and answer. |
| 10 | 1:40.63–1:50.54 | 9.9 | 8.7 | s_c_verdict2, s_g_scorer | Round 2 on the site: PASS, with one candidate dropped. All 84 committed runs re-score identically in the browser: 84/84. | Round two on the site: pass, with one candidate dropped. All eighty-four committed runs re-score identically in the browser. |
| 11 | 1:50.54–2:00.05 | 9.5 | 8.3 | s_a_hero | Same candidates, same seed, local subprocess and Nebius ConTree sandbox: three runs, one decision hash. | Same candidates, same seed, local subprocess and Nebius ConTree sandbox: three runs, one decision hash. |
| 12 | 2:00.05–2:08.50 | 8.4 | 7.2 | s_d_g4_on | If every candidate violates a past decision the same way, they agree, and the disagreement gate is silent. | If every candidate violates a past decision the same way, they agree, and the disagreement gate is silent. |
| 13 | 2:08.50–2:15.86 | 7.4 | 6.2 | s_e_g4_off | Regression gate G4 off: case A passes with three violators approved. | Regression gate, gate four, off: case A passes with three violators approved. |
| 14 | 2:15.86–2:21.86 | 6.0 | 4.5 | s_d_g4_on | G4 on: CODE_INCOMPLETE. Only the ledger catches this. | Gate four on: code incomplete. Only the ledger catches this. |
| 15 | 2:21.86–2:28.18 | 6.3 | 5.1 | s_c2_pass_close | PASS means no counterexample in the searched range; what was not searched is grey. | Pass means no counterexample in the searched range; what was not searched is grey. |
| 16 | 2:28.18–2:38.93 | 10.8 | 9.6 | s_f_provenance, s_f2_limits | The limits are stated: eight pure Python functions, live sessions on one task, a pre-alpha sandbox SDK, a search that can be wrong. | The limits are stated: eight pure Python functions, live sessions on one task, a pre-alpha sandbox SDK, a search that can be wrong. |
| 17 | 2:38.93–2:46.54 | 7.6 | 6.4 | card_close | Code, data and the demo are public. When candidates disagree, ask; never guess. | Code, data and the demo are public. When candidates disagree, ask. Never guess. |
| tail | 2:46.54–2:49.54 | 3.0 | – | card_close | – | – |

## Stills

| name | source | crop | note |
|---|---|---|---|
| t1_prompt | 10.0 | [0, 47, 3800, 2041] | command entered, waiting; full terminal minus tab bar and taskbar |
| t2_round1 | 19.5 | [0, 47, 3800, 2041] | round 1 verdict + question card + reference search pending |
| t3_reference | 41.5 | [0, 70, 2060, 1120] | 'reference search ... ok · 3 results · 3.0s' + 3 sources + fetched line (small font, before the o2 line) |
| t4_answer | 41.5 | [0, 70, 2090, 1210] | same block + '> option id: o2' + survivors line |
| t5_round2 | 51.5 | [0, 380, 2062, 1165] | sources 2-3 … o2 … round 2 PASS ['REGRESSION_FAILURE'] … SESSION live7: PASS |
| s_a_hero | site_a_hero_full.png | [800, 1072, 800, 450] | hero: three environments, one decision_core; reveal finished |
| s_cands | site_bc_live7_full.png | [830, 315, 747, 420] | live7 round 1 candidates: c1 Nemotron-3-Ultra, c2 Qwen3-235B, c3 DeepSeek-V4 with their patch sources |
| s_b1_verdict1 | site_bc_live7_full.png | [690, 795, 782, 440] | live7 round 1 verdict: NEEDS_CLARIFICATION, witness, decision_core matches Python |
| s_b2_reference | site_bc_live7_full.png | [700, 1645, 747, 420] | live7 question card: Tavily reference block header + sources |
| s_b3_ledger | site_bc_live7_full.png | [700, 3300, 747, 420] | live7 ledger: question → reference → answer → decision |
| s_c_verdict2 | site_bc_live7_full.png | [700, 2790, 747, 420] | live7 round 2: PASS, dropped c3, acceptance test in force |
| s_c2_pass_close | site_bc_live7_full.png | [800, 2815, 640, 360] | round 2 PASS block close-up: 'no counterexample found in the searched range', not searched |
| s_d_g4_on | site_d_g4_on_full.png | [700, 470, 747, 420] | evidence G4 ablation, toggle ON: case A CODE_INCOMPLETE |
| s_e_g4_off | site_e_g4_off_full.png | [700, 470, 747, 420] | same box, toggle OFF: case A PASS (three violators approved) |
| s_f_provenance | site_f_provenance_full.png | [700, 480, 747, 420] | provenance: cross-backend table + canary table |
| s_f2_limits | site_f_provenance_full.png | [830, 2530, 747, 420] | provenance: what this site can and cannot show |
| s_g_scorer | site_g_scorer_full.png | [0, 100, 747, 420] | scorer_test: 84/84 runs |
| card_intro | html card | - |  |
| card_arch | html card | - |  |
| card_close | html card | - |  |

## Files

- `video_tts_lines.txt`, `video_subtitles.srt` — 1:1 cues (committed)
- `make_tts.py` (edge-tts + ffprobe + timeline), `shoot_site.py` (site screenshots), `make_stills.py` (crops, cards, assign.json, scenes.json), `make_video.py` (assembly, loudnorm, ASS subtitles, fade), `make_script_doc.py` (this document)
- `work/` — durations_*.json, timeline.json, scenes.json, assign.json, site_bboxes.json, term_geometry.json, stills/, shots/, verify/ (not committed)
- `tts/final/en-US-AndrewNeural/line_NN.mp3` (not committed)
- `bdg_demo_v2.mp4` (not committed)
