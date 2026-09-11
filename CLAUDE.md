# CLAUDE.md — Behavioral Disagreement Gate (BDG)

## 프로젝트

코딩 에이전트용 **완료 게이트**. 에이전트가 후보 패치 N개를 내면 격리된 샌드박스 분기에서
같은 입력으로 실행하고, 둘 이상이 다르게 행동하면 완료를 승인하지 않고 그 입력을 사용자에게
질문으로 되돌린다. 사용자 답은 append-only ledger 에 기록되어 acceptance test 로 컴파일되고,
다음 라운드의 회귀 스위트에 합류한다.

- 대회: Nebius x NVIDIA Global AI Hackathon, Coding & Agentic Engineering 트랙
- 마감: 2026-10-30. 솔로 참가. 제출 후 3.5개월 무보수 유지가 전제다.
- 스택: Nebius Token Factory (Nemotron / Qwen / DeepSeek), ConTree sandboxes, Python 3.10, 바닐라 JS (ES module)
- 핵심 로직과 실측 데이터는 이미 완성되어 있다. **Claude 의 작업 범위는 `site/` 아래 UI 다듬기로 한정한다.**

## 작업 범위 규칙

- **UI(`site/`) 외의 변경은 반드시 사용자 승인을 먼저 받는다.** 파이썬 코어, 데이터 디렉터리,
  빌드 스크립트의 검사 로직은 UI 작업에서 손댈 일이 없다. 필요해 보이면 이유를 적어 제안만 한다.
- `site/` 안에서도 `site/scorer.js` 는 아래 금지 목록 1번이다.
- 커밋·푸시는 사용자가 요청할 때만 한다.
- 응답과 보고는 한국어로 한다. 코드·명령·파일명은 원문 그대로.

## 절대 건드리지 마라 (승인 없이 수정 금지)

1. **`site/scorer.js`** — `scorer.py` 의 JS 이식본. `decision_core_sha256` 가 Python 과 바이트 단위로
   일치해야 하며, 현재 86/86 해시 일치가 검증되어 있다. 다음 중 하나라도 깨지면 프로젝트의 핵심 주장이 무너진다.
   - Python `json.dumps(sort_keys=True, separators=(",",":"), ensure_ascii=False)` 재현
   - Python `repr(float)`: `2.0 → "2.0"`, `1e-9 → "1e-09"`, `1e16 → "1e+16"`, `-0.0 → "-0.0"`
   - Python `round()` = ties-to-even (JS `Math.round` 와 다르다)
   - Python `repr(str)` 의 따옴표 선택 규칙
   - `JSON.parse` 금지 (`2.0` 을 `2` 로 만든다). 자체 파서 `parsePy` 를 쓴다.
   - "정리", "현대화", "최적화", 린트 자동 수정 모두 금지.
2. **게이트 코어 파이썬** — `scorer.py`, `runner.py`, `backends.py`, `ledger.py`, `acceptance.py`,
   `representative.py`, `question.py`, `loop.py`, `grader.py`, `canary_check.py`. UI 작업에서 손댈 일이 없다.
   (`research.py`, `providers.py` 는 판정 경로 밖이라 이 목록에 없지만, 아래 "참고 검색 불변식" 을 지킨다.)
3. **`build_site_data.py` 의 `leak_scan()`** — 정답 테스트(`gold/`)가 공개 사이트로 새는지 검사한다.
   빌드가 거부되면 이유가 있다. 검사를 우회하거나 완화하지 마라.
4. **`runs/ ledger/ clarify/ grades/ experiments/`** — 실측 데이터. 재생성 비용이 크다
   (LLM 210+ 호출, 약 90분). 수정·삭제 금지. `gold/` 는 정답 테스트라 사이트로 내보내면 안 된다.

## 참고 검색 (research.py, Tavily) 불변식

명료화 질문이 만들어진 뒤, 답을 받기 전에, 그 불일치 축의 표준 관례를 Tavily 로 한 번 검색해 답하는 사람에게
참고 정보로 보여준다. Best Use of Tavily 보너스상 자격("functional, runtime call to the Tavily API")을 채우지만
장식이 아니라 제품 기능이다. 다음을 깨면 안 된다.

- 검색 결과는 verdict, `decision_core_sha256`, acceptance test, `question_sha256` 에 절대 들어가지 않는다.
  question dict 와 `clarify/*.question*.json` 에는 넣지 않는다. provider.ask 의 두 번째 인자는 `fetch_reference` callable 이고,
  provider 가 질문을 다 보여준 다음에 부르는 시점에 실제 호출이 일어난다 (화면: "reference search via Tavily ... ok · 3 results · 3.3s").
  한 질문에 한 번만 검색·기록되며, provider 가 안 불러도 loop 가 answer 기록 전에 불러 순서를 보장한다.
- 선택지를 고르거나 기본값으로 제시하지 않는다. 표시 위치는 선택지 아래, 입력 프롬프트 위.
- 질의는 `ambiguity_axis` + `issue_text` + 질문의 대표 입력·관측 선택지만으로 만든다 (`research.build_query`, 순수 함수).
  축 이름별 검색어 표를 만들지 마라 — 정답을 미리 아는 셈이 된다. 사용자 답(raw_input)은 코드로 거부된다.
- Tavily 의 LLM 생성 `answer` 필드는 요청하지 않는다. URL 이 없는 결과는 버린다 (캐시를 읽을 때도 다시 거른다).
- ledger 에 `reference` 레코드로 question 다음, answer 앞에 기록한다 (seq 순서가 "답보다 먼저 보였다" 의 증거).
  `fold()` 는 이 kind 를 무시한다 → active view 와 `active_view_sha256` 불변.
- 키 없음·HTTP 오류·타임아웃은 예외가 아니라 `status: unavailable` + reason 이고 루프는 계속된다. `--research off` 는 `status: off` 로 남는다.
- API 키는 어디에도 기록하지 않는다. 캐시(`research_cache/`, gitignore)는 편의 장치이지 정확성 장치가 아니다 — `cached: true` 로 표시.
- HTTP 직접 호출(httpx, 이미 고정)이며 tavily-python 을 넣지 않는다 (tiktoken 컴파일 의존성). `requirements.txt` 불변.
- 사이트: `session.html` 이 ledger 의 `reference` 를 질문 카드 맨 아래에 그린다. 텍스트 노드로만 그리고 http(s) 링크만 허용한다.
  "not read by the verdict" 문구와 출처 URL 은 항상 함께 보인다. unavailable/off 는 `.unmeasured` 칩(측정하지 않음).
- 검증: `python research.py --selftest` 가 전부 ok 여야 한다 (네트워크 없음).
- 검색 파라미터(advanced, 3결과, 청크 3, 도메인 필터 없음)는 2026-09-11 에 3 질의 × 3 설정을 실측해 고른 값이다.
  basic 은 튜토리얼·잡음(score 0.17~0.24), 도메인 부스트는 위키 리비전 diff·LaTeX 조각을 냈다. 바꾸려면 다시 실측해라.
- 키는 사용자 수준 환경변수에 있다. 이 도구의 Bash 셸은 못 보고, PowerShell 에서
  `$env:TAVILY_API_KEY = [Environment]::GetEnvironmentVariable("TAVILY_API_KEY","User")` 로 올려서 실행한다.

## 디자인 원칙 (이미 적용됨 — 유지해라)

이 사이트의 목적은 "무엇을 검사했고 무엇을 검사하지 않았는가"를 정직하게 보여주는 것이다.
흔한 AI 대시보드처럼 만들면 주장 자체가 무너진다.

- **판정에 색을 쓰지 않는다.** PASS 는 "성공"이 아니라 "지정된 탐색 범위에서 반례를 찾지 못함"이다.
  초록=통과 / 빨강=실패 는 제품의 주장을 정면으로 배반한다. 상태는 글자와 위치로만 구분한다.
- **강조색은 `--annotate` (#2F5FD8) 하나뿐이다.** "측정된 것"에만 쓴다. 장식으로 쓰지 마라.
  (`--annotate-wash` #EAF0FC 는 그 배경 톤이다.)
- **`--redact` (#E8E4DA) 회색은 "측정하지 않은 영역"이다.** not searched, k_unique=0, 녹화되지 않은 경로 등.
  미완성이나 버그가 아니라 의도된 시각 장치다. 지우지 마라. (`.unmeasured`, `.redact-block`, `.transition-redact`)
- **활자가 화자를 구분한다.** IBM Plex Mono (`--mono`) = 기계가 계산한 것 (판정, 수치, 코드, probe 값).
  Source Serif 4 (`--serif`) = 사람이 읽는 서술. 이 대응을 섞지 마라.
- **레이아웃은 실험노트의 여백 주석이다.** 왼쪽 좁은 열 = 출처·측정 조건, 오른쪽 = 내용, 괘선으로 구분
  (`.entry`, `--gutter`, `--measure: 66ch`). 카드로 자르지 않는다.
- **모션 (apple-design 원칙).** 스프링은 damping 1.0, response 0.3~0.4. 바운스 없음.
  중단 가능성이 가장 중요하다 — 진행 중 애니메이션은 목표값이 아니라 현재 화면에 그려진 값에서
  다시 출발해야 한다 (`ui.js` 의 `createSpring` 이 이미 그렇게 되어 있다. `pressable`, `makeToggle`,
  `openFrom` 이 그 위에 있다). `pointerdown` 에 반응한다 (`click` 이 아니라).
  `prefers-reduced-motion` 을 반드시 존중한다 (`ui.js` 의 `REDUCED`, `app.css` 의 media query).
  자동 재생 모션은 `index.html` 의 해시 reveal (`revealHash`) 하나뿐이다. 늘리지 마라.
  단 `session.html` 의 질문 카드 `openFrom()` 은 예외다 — 이것은 장식이 아니라 apple-design 의
  spatial consistency(카드가 트리거 위치에서 열린다) 적용이며, 사용자 동작에 대한 응답이다. 유지한다.

### 금지 목록

초록/빨강 상태색 · 보라-파랑 그라디언트 · 카드 그림자 · 이모지 아이콘 · 헤딩 위 대문자 eyebrow 라벨 ·
화살표 붙인 CTA 버튼 · 히어로 카피 애니메이션 · 글래스모피즘/`backdrop-filter`
(경계를 흐리면 "측정/미측정" 구분이 죽는다) · 프레임워크 도입 (React/Next/Tailwind — 3.5개월 무보수 유지가 조건이다) ·
새 의존성·빌드 도구·번들러 추가.

`.github/workflows/pages.yml` 은 이 금지와 충돌하지 않는다: 배포 인프라(커밋된 `site/` 를 그대로 Pages 에 올린다)이지 빌드 도구가 아니며,
사이트는 여전히 빌드 없는 정적 파일이다. 워크플로가 `site/data` 를 다시 만들게 하지 마라.

## 변경 후 반드시 통과해야 하는 검증

UI 를 고친 뒤 아래를 전부 돌리고 결과를 보고한다. 하나라도 실패하면 되돌린다.
(저장소 루트 `C:\nebius\bdg` 에서 실행. Node 24 는 사용자 PATH 에 있다.)

```
python build_site_data.py --exclude-sessions demo1
  → "lite==full decision_core: N/N" 에서 두 수가 같아야 한다 (현재 88/88 — 2026-09-11 ref1 세션 2라운드 추가)
  → "leak scan: clean"
  → "canary checks: 49  all clear: True"

node site/test/scorer_test.mjs site/data
  → "runs: N/N decision_core identical"   (현재 82/82 = 세션 라운드 23 + 코호트 run 58 + crossbackend.json trace 1. 빌드의 lite==full 88/88 과는 다른 수치다)

python -m http.server 8000
  → 아래 페이지를 브라우저로 직접 열어 콘솔 에러가 없는지 확인
    /site/
    /site/session.html?id=live5
    /site/session.html?id=ref1        (reference 블록이 있는 세션)
    /site/session.html?id=sw_live
    /site/evidence.html
    /site/provenance.html
    /site/test/scorer_test.html
```

`site/data/` 는 `build_site_data.py` 의 산출물이다. 손으로 고치지 말고 빌드로 다시 만든다.

## 배포 (GitHub Pages)

`.github/workflows/pages.yml` 이 main 푸시(또는 Actions 탭의 수동 실행)마다 **커밋된 `site/` 만** 아티팩트로 올린다
(checkout → configure-pages → upload-pages-artifact `path: site` → deploy-pages). Settings → Pages → Source 는 "GitHub Actions".
저장소 루트를 서빙하지 않는 이유: `gold/`(정답 테스트)와 파이썬 소스가 데모 도메인에서 열리면 provenance.html 의
"Reference tests and their inputs are not served at all" 이 거짓이 된다. 워크플로는 `site/data` 를 다시 만들지 않는다
(빌드에는 runs/ ledger/ 실측 데이터와 API 키가 필요하다). Jekyll 은 돌지 않으므로 `.nojekyll` 은 필요 없다.

배포 후 확인 (앞부분 `https://minjun0208.github.io/behavioral-disagreement-gate/`):

| 경로 | 기대 |
|---|---|
| `/` | 200, 히어로가 "identical across 3 environments" |
| `session.html?id=live5` | 200, 콘솔 에러 없음 |
| `evidence.html` | 200 |
| `provenance.html` | 200, cross-backend 해시 세 줄이 한 줄씩 |
| `test/scorer_test.html` | 200, "82/82 runs" |
| `gold/mean.json` | **404** — 서빙 범위가 site/ 뿐임을 증명한다 |
| `scorer.py` | **404** — 같은 이유 |

마지막 두 줄이 404 가 아니면 배포 방식이 바뀐 것이다(브랜치 루트 배포 등). 그 상태로 두지 마라.
Pages 는 `Cache-Control: max-age=600` 을 붙이므로 재배포 후 최대 10분간 옛 JSON 이 보일 수 있다. 확인은 10분 뒤 또는 시크릿 창에서 한다.

## 파일 구조 개요

```
site/                       공개 사이트 (정적, 빌드 없음, ES module)
  index.html                히어로 + 세션 목록 + built 시각(UTC). 해시 reveal 이 유일한 자동 모션.
  session.html?id=<sid>     한 세션의 라운드별 trace / verdict / 질문·답변. 토글·프레스 스프링 사용.
  evidence.html             diversity 코호트, compliance 실험, G4 ablation, g4_* 세션 비교.
  provenance.html           lite==full 대조, cross-backend 그룹, canary 결과.
  app.css                   토큰(--annotate, --redact, --mono, --serif, --measure, --gutter), .entry 레이아웃,
                            .measured/.unmeasured/.redact-block, 버튼·토글, .scroll(표 가로 스크롤), reduced-motion.
  ui.js                     공용 유틸: loadPy(parsePy 기반 fetch), el/q/esc, createSpring, pressable,
                            makeToggle, revealHash, openFrom, header, renderVerdict. scorer.js 를 재수출.
  scorer.js                 ★ 수정 금지. scorer.py 이식본 (parsePy, canon, score, pyFloatRepr, pyRound …).
  test/scorer_test.mjs      Node: site/data 의 모든 run 에 대해 JS 해시 == Python 해시 대조.
  test/scorer_test.html     같은 검증의 브라우저판.
  test/scorer_test_fixture.mjs
  data/                     빌드 산출물 (build_site_data.py)
    index.json              세션 목록, has_crossbackend
    crossbackend.json       index.html 히어로가 읽는 cross-backend 그룹 1개 (run 별 backend·decision_core) + 브라우저가 재채점할 trace 1건. build_crossbackend() 산출.
    provenance.json         lite==full, cross_backend, canary_checks
    cfg_full.json           게이트 설정 (G2/G3/G4, normalizer)
    sessions/<sid>.json     세션별 라운드 trace + verdict_python (demo1, live1…, live5, sw_live, g4_* 등)
    experiments/            diversity.json, compliance.json, ablation.json

build_site_data.py          runs/ledger/clarify/grades/experiments → site/data. leak_scan, lite==full 검증, canary, build_crossbackend() 포함.
scorer.py …                 게이트 코어 (수정 금지 목록 2번)
research.py                 Tavily 참고 검색 (질의 생성은 순수 함수, --selftest 있음). 판정 경로 밖. 위 "참고 검색 불변식".
patch_v013.py               v0.12 → v0.13 패치 스크립트 (experiment_g4_ablation.py 질문 파일명, app.css 왼쪽 여백). 재실행 안전.
conformance_contree.py      ConTree SDK(contree-sdk 0.3.6 고정) 적합성 검사 8종. SDK 버전이 바뀌면 먼저 돌린다.
experiment_compliance.py    프롬프트 제약 준수 통제 실험 (7 조건 x 3 모델 x reps) → experiments/compliance_full.jsonl
experiment_g4_ablation.py   G4(회귀 게이트) paired ablation, 케이스 A~D, LLM 0회 → experiments/g4_ablation*.json
requirements.txt            고정 버전 의존성 (contree-sdk==0.3.6, openai, httpx 등)
cfg_*.json                  게이트 설정 변형 (cfg_full 이 기본)
tasks/ gold/                과제 정의 / 정답 테스트 (gold 는 사이트로 내보내지 않는다)
examples/                   e2e·ablation 예시 task/answers
runs/ ledger/ clarify/ grades/ experiments/   실측 데이터 (수정·삭제 금지, 일부는 .gitignore)
.github/workflows/pages.yml  GitHub Pages 배포 — 커밋된 site/ 만 업로드, 빌드 없음. 배포 인프라이지 빌드 도구가 아니다.
```

## 지금 알려진 미해결 항목 (다음 작업 후보)

1. witness 표에서 후보 id 와 값 사이 간격이 과도하다 (`c1 …… -13`).
2. built 시각이 UTC 라 헷갈린다 (로컬 시각 병기 검토).
3. `evidence.html`, `provenance.html`, `session.html` 은 아직 시각 검토를 못 했다.
4. **Tavily 통합 — 구현·실측 완료 (2026-09-11).** `ref1`(safe_div, hardcoded, 답 "raises ZeroDivisionError")이 실제 호출 기록을 갖고
   사이트에 있다. 남은 것: 사용자가 터미널에서 돌리는 LLM 라이브 세션 `live6` (영상 촬영용). 촬영 전 `research_cache/` 를 지워야 캐시가 아닌 실제 호출이 찍힌다.
   Rules 원문: Best Use of Tavily $3,000, "All Eligible Submissions that make a functional, runtime call to the Tavily API as part of its solution."
   "Each Project is eligible for one (1) Overall Award OR one (1) Track Award and one (1) Bonus Award."
5. **3분 데모 영상 — 대본과 촬영 순서.** 심사 4축이 전부 이 영상으로 전달된다. 히어로(브라우저 재채점 해시 일치) → live5 재생 → provenance 순이 후보.
6. **Devpost 제출.** 마감 2026-10-30 10:00 PT (한국시간 10/31 02:00). 필수: 공개 repo + README(완료) + 데모 URL(완료) + 3분 영상(5번).
