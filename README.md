# Behavioral Disagreement Gate

A completion gate for coding agents. The agent writes several candidate patches; they run in isolated sandbox branches on the same generated inputs. If any two candidates behave differently, completion is withheld and the exact input goes back to the user as a question. The answer is appended to a ledger, compiled into an acceptance test, and enforced on the next repair round. Every verdict is computed by deterministic code; language models only propose and explain.

**Demo:** <https://minjun0208.github.io/behavioral-disagreement-gate/> (static site, no login, kept online through 15 December 2026).
In 30 seconds: the front page re-scores an execution trace recorded in a ConTree sandbox *in your browser* and prints the same decision hash Python produced; [session live7](https://minjun0208.github.io/behavioral-disagreement-gate/session.html?id=live7) replays the two-round clarification shown in the demo video, Tavily reference included; [provenance](https://minjun0208.github.io/behavioral-disagreement-gate/provenance.html) lists what was checked and what was not.

Nebius x NVIDIA Global AI Hackathon, Coding & Agentic Engineering track. Solo entry. Apache-2.0.

## The problem

An agent that writes a patch and then reports "done" is grading its own homework. Its tests encode its own reading of the issue. Where the issue is ambiguous (how to round a tie, what an empty list means, whether a boundary is inclusive) the agent picks one reading silently, and its tests pass.

This gate does not try to judge correctness. It checks whether independent attempts at the same issue agree on behavior, and treats a disagreement as a question the user has to answer rather than a defect the agent has to fix. The techniques are established ones: differential testing between candidates, seeded generator-based probing, regression from confirmed decisions, and mutation testing as an advisory signal. What this project does is operationalize them on a branchable sandbox runtime and measure what they catch.

## What PASS means

> **PASS** = on the searched input range (generator, seed and budget are recorded in the verdict as `g3_search`) no two candidates disagreed, and every candidate passed the confirmed acceptance tests. It is not a proof of correctness. If all candidates share the same wrong reading, the gate is silent (see Limitations).

| Status | Meaning |
|---|---|
| `PASS` | no counterexample found in the searched range; regression suite passed |
| `NEEDS_CLARIFICATION` | an input on which candidates return different values or exception types (the *witness*) |
| `CODE_INCOMPLETE` | every candidate fails a confirmed acceptance test |
| `UNVERIFIABLE` | fail-closed: runner failure, incomplete trace, capture failure, a non-deterministic candidate, fewer than two distinct implementations, or no valid probe. Infrastructure problems are never reported as code defects. |

The demo site shows unsearched ranges in grey and uses no green/red, for the same reason.

## Architecture: three programs that cannot see each other's data

| Program | Does | Does not | Why the restriction |
|---|---|---|---|
| **Runner** (`runner.py`, `backends.py`) | generates candidates (LLM or fixtures), builds seeded probe inputs, executes every candidate on every probe in a sandbox branch, records raw observations (return value `repr`, exception type and message, exit code, timeout) to an append-only `trace.jsonl` | never writes a verdict; the scorer rejects observation records that contain verdict-like fields | one execution yields one trace that can be re-scored offline under any gate configuration. That is the precondition for the G4 ablation: same trace, gates on and off. |
| **Scorer** (`scorer.py`, ported byte-for-byte to `site/scorer.js`) | pure function `trace × config → verdict`; computes G4, G2, G3 and `decision_core_sha256` over the decision-relevant fields only | never reads `gold/`, never executes code | if the verdict and the grade came from the same source, measuring the gate against the grade would be a tautology. |
| **Grader** (`grader.py`, `canary_check.py`) | scores candidates against `gold/` reference tests after the fact; refuses to grade if any gate test input overlaps a gold input; writes a random canary string into `grades/<run_id>/` and greps the entire gate output tree for it | never reads `trace.jsonl` | a grade contaminated by the gate's own observations is not an independent measurement. Isolation is proven by the canary, not by directory layout. |

Gates, in the order the scorer applies them:

- **G4 regression.** Candidates that fail a confirmed acceptance test are dropped from the pool. Empty pool means `CODE_INCOMPLETE`.
- **G2 mutation (advisory).** Mutants in the changed region, killed by the baseline suite vs the agent's own tests (`K_base`, `K_agent`, `K_unique`). Reported in the verdict; never changes the status.
- **G3 disagreement.** Diversity is counted in distinct patch hashes, not candidates. Over the probes on which every pooled candidate exits 0 without timeout, normalized outputs are compared (float tolerance 1e-9, whitespace stripped, JSON key order ignored, exceptions compared by type). The first differing probe becomes the witness and the status is `NEEDS_CLARIFICATION`.

```
issue --> Runner --> trace.jsonl --> Scorer --> verdict + decision_core_sha256
           | sandbox branch per candidate       |
           | seeded probes                      v  NEEDS_CLARIFICATION
           v                       representative input --> question --> user
       candidates                                                        |
                    ledger (append-only) <-- answer <--------------------+
                          |
                          +--> acceptance tests --> next round's regression suite (G4)

Grader + canary: offline, reads gold/, never the trace
```

## The clarification loop

One session is one task and up to five rounds (`loop.py`). Each round compiles the ledger's active decisions into typed acceptance tests, merges them into the task's regression suite, runs the runner and the scorer. On `NEEDS_CLARIFICATION`, `representative.py` collects every disagreeing probe from the trace and picks the simplest *separating* input (no extra execution); `question.py` builds a deterministic, LLM-free question whose options are the candidates' outputs in sorted display order, without model names or vote counts, plus open options (a different value, a different exception, not sure). The answer is validated as a literal or an exception name (never concatenated into code) and appended as a `decision`; `supersede`, `revoke` and `defer` are records, not edits. Seeds for all rounds are fixed at session start, before any answer.

Termination: `PASS`, `MAX_ROUNDS_EXCEEDED`, `NO_PROGRESS` (same question hash again), `LEDGER_CONFLICT`, `DEFERRED`, `UNVERIFIABLE`, `CODE_INCOMPLETE`.

Example, session `live5` (`round_half`, three models): round 1 witness `x = -39.5`, options `-39` / `-40`, user typed `-40`; round 2 witness `x = 8.5`, options `8` / `9`, user chose `8`; round 3 `PASS` with one candidate dropped by G4. Two decisions active at the end. [Replay it](https://minjun0208.github.io/behavioral-disagreement-gate/session.html?id=live5) and pick a different answer to see where the recorded path diverged.

## Reference search (Tavily)

A question like "`round_half(x=-39.5)`: `-39` or `-40`?" gives the person answering no ground to stand on. So after the question is built and before the answer is taken, `research.py` runs one Tavily search for the convention behind that disagreement and prints the results, with their URLs, under the options. The query is assembled only from the task definition (`ambiguity_axis`, `issue_text`) and the question (representative input, observed options); there is no hand-written table of search terms per axis, because that would mean the gate already knew the answer. Example query, session `ref1` round 1 (`safe_div`, fixture candidates that raise or return `None` on `b == 0`):

```
Python divides a by b: error policy convention. For a=0.0, b=0.0, raises ZeroDivisionError or None?
```

The recorded reference block is on the [ref1 session page](https://minjun0208.github.io/behavioral-disagreement-gate/session.html?id=ref1). Search parameters: `search_depth: advanced` (2 credits), 3 results, 3 chunks per source, no domain filter. They were chosen by comparing three settings on three queries on 2026-09-11; `basic` returned tutorials and off-topic pages (relevance 0.17 to 0.24), and boosting `docs.python.org` / `en.wikipedia.org` pulled in revision diffs and formula fragments. Sessions recorded before this feature (`live1` to `live5`, `sw_live`, `g4_*`) carry no `reference` record; the live LLM session `live7`, recorded for the demo video, carries one.

What the search does not do: it is not read by the scorer, it is not part of `decision_core_sha256` or `question_sha256`, it does not pre-select or rank an option, and the acceptance test still comes only from the user's answer. Tavily's LLM-written `answer` field is not requested; only results that carry a URL are kept. The call and its results are appended to the ledger as a `reference` record between the `question` and the `answer`, so the sequence numbers show that the person saw the search before answering. A missing key, an HTTP error or a timeout is recorded as `status: unavailable` with the reason and the loop goes on; `--research off` is recorded as `status: off`. Responses are cached under `research_cache/` (not committed) as a convenience, not as a correctness device: a cached record says `cached: true` and keeps the original fetch time. The session page shows the block under each question, labelled as not read by the verdict, with the sources.

## Nebius stack

- **Token Factory inference.** Candidates come from three models, one per slot: `nvidia/Nemotron-3-Ultra-550b-a55b`, `Qwen/Qwen3-235B-A22B-Instruct-2507`, `deepseek-ai/DeepSeek-V4-Pro`, temperature 0.8. On the toy task, three models disagreed in 5/5 runs; one model sampled three times disagreed in 1/5.
- **ConTree sandboxes.** The runner prepares one base node (`python:3.12-slim`) and branches it per candidate and per mutant. Workers only execute; the runner is the single trace writer; the execution envelope echoes an `exec_id`, and a mismatch marks the observation `capture_ok=false`. SDK exceptions are recorded as `sandbox_error`, never disguised as results. There is no local fallback. The run header records the measured egress probe result rather than assuming isolation.
- **Same decision on different machines.** Task `mean`, fixture candidates, probe seed 7, budget 20, executed on the local subprocess backend (`cmp_local`, `v6_local`) and in a ConTree sandbox (`v6_contree`): identical `decision_core_sha256` `b03b4dd4...8b54cf6e`. The demo re-scores the sandbox trace in the browser and gets the same hash; the command to reproduce it locally is under Results.
- **SDK conformance.** `conformance_contree.py` exercises the 8 SDK behaviours the backend depends on (`contree-sdk` 0.3.6, pre-alpha). Run it before `--backend contree`.
- **Tavily search (Tavily by Nebius).** One runtime search per clarification question, shown to the person answering with its sources; never read by the verdict. See [Reference search](#reference-search-tavily).

## Setup

Python 3.10 (tested on 3.10.11). Node.js (tested on v24) only for the JavaScript cross-check.

```sh
pip install -r requirements.txt      # pinned versions; contree-sdk==0.3.6
export NEBIUS_API_KEY=...            # Token Factory inference (llm_gen.py) and ConTree IAM auth
export NEBIUS_PROJECT_ID=...         # ConTree: Nebius project id, read by contree-sdk IAMAuth
export TAVILY_API_KEY=...            # optional: reference search shown with each question (research.py). Without it the loop records "unavailable" and continues
python conformance_contree.py        # first: must print 8/8 passed
```

The SDK also accepts an `auth.ini` profile; environment variables take precedence. Everything below that uses fixture candidates and `--backend local` runs without credentials.

## Run

Offline quickstart, no API key. Two fixture implementations of `round_half` that differ on ties:

```sh
python runner.py --task tasks/round_half.json --run-id quick --gen hardcoded --backend local
python scorer.py runs/quick/trace.jsonl cfg_full.json   # NEEDS_CLARIFICATION, witness x=24.5: c1 -> 24, c2 -> 25
python grader.py quick                                  # post-hoc grade against gold/, plants the canary
python canary_check.py quick                            # exit 0: canary not found anywhere in the gate's output
```

<details>
<summary>LLM candidates in ConTree sandboxes</summary>

```sh
python runner.py --task tasks/mean.json --run-id mean_llm --gen llm --backend contree \
  --models nvidia/Nemotron-3-Ultra-550b-a55b,Qwen/Qwen3-235B-A22B-Instruct-2507,deepseek-ai/DeepSeek-V4-Pro
python scorer.py runs/mean_llm/trace.jsonl cfg_full.json
```

`--n-candidates` (default 3) fills slots round-robin from `--models`. `--probe-budget` (default 100), `--probe-seed` (default 7) and `--repeats` (default 2, for the non-determinism check) are recorded in the trace. Gate variants: `cfg_no_g3.json`, `cfg_no_g4.json`, `cfg_exc_type_message.json` (compare exception messages too).
</details>

<details>
<summary>Clarification loop</summary>

```sh
# interactive: the question is printed, the answer is typed at the CLI
python loop.py --task tasks/round_half.json --session s1 --gen llm \
  --models nvidia/Nemotron-3-Ultra-550b-a55b,Qwen/Qwen3-235B-A22B-Instruct-2507,deepseek-ai/DeepSeek-V4-Pro

# scripted replay, offline: answers come from a JSON list
python loop.py --task tasks/round_half.json --session s1_replay --gen hardcoded \
  --answers three_conventions.answers.json --quiet
```

Outputs: `ledger/<session>/ledger.jsonl`, `clarify/<session>/round_<n>.{task,question_1,verdict}.json`, `runs/<session>_r<n>/`. `python summarize.py` prints a verdict-plus-gold table for every local run. `--research off` skips the Tavily reference search (recorded in the ledger as `status: off`); `python research.py --selftest` checks the query builder and the failure paths without network.
</details>

<details>
<summary>Reproduce the experiments and the site</summary>

```sh
python experiment_g4_ablation.py                                              # offline, deterministic, LLM-free
python experiment_compliance.py --summarize experiments/compliance_full.jsonl # recompute the summary from the committed raw records
python experiment_compliance.py --reps 10 --tag full                          # regenerate: 7 conditions x 3 models x 10 = 210 LLM calls
python build_site_data.py --exclude-sessions demo1   # needs the raw runs/ ledger/ clarify/ grades/ directories (not in git)
node site/test/scorer_test.mjs site/data             # JavaScript scorer vs Python on every committed trace
python -m http.server 8000                           # then open http://localhost:8000/site/
```
</details>

## Results

Every number below is computed from files in this repository. "Reproduce" is the command that produces it. The raw LLM runs behind the first two rows live in `runs/` (not committed), but their traces and Python verdicts are committed under `site/data/` and re-scored by the scorer-fidelity row.

| What | Result | On the site | Reproduce |
|---|---|---|---|
| Disagreement detected, 8 tasks x 3 seeds, three models, probe budget 60 | 14/24 rounds `NEEDS_CLARIFICATION` (trace schema v2) and 15/24 (schema v1). Per task: `mean` 3/3 and 2/3, `median` 1/3 and 3/3, `percent_change` 3/3, `round_half` 3/3, `safe_div` 3/3, `is_palindrome` 1/3, `in_range` 0/3, `top_k` 0/3 | [evidence](https://minjun0208.github.io/behavioral-disagreement-gate/evidence.html) | one `python runner.py --task tasks/<task>.json --gen llm --probe-budget 60 --gen-seed <1001,2001,3001> --models <three models>` per task and seed; verify the committed traces with `node site/test/scorer_test.mjs site/data` |
| One model sampled repeatedly vs three models, toy `round_half`, 5 runs each | 1/5 vs 5/5 detected | evidence | same, `--models` with one vs three entries |
| Prompt-constraint compliance, 7 conditions x 3 models x 10 reps | 210 generations, 209 parsed. Baseline conventions: Qwen 10/10 away-from-zero, DeepSeek 10/10 half-up, Nemotron 9/9 away-from-zero. With one confirmed example in the prompt the pooled satisfy rate was 13 to 19 of 30 depending on the target convention; with two examples, 13 to 24 of 30. 7 generations satisfied the examples but implemented a different policy | evidence | `python experiment_compliance.py --summarize experiments/compliance_full.jsonl` |
| G4 ablation, 4 constructed cases, same traces scored with G4 on and off | Case A (all candidates violate a confirmed decision the same way): G4 off gives `PASS` with three violators approved, G4 on gives `CODE_INCOMPLETE`. G4 was the only gate that caught it. B (all comply): no false block. C, D: caught by G3 and G4 alike | evidence | `python experiment_g4_ablation.py` (output identical to `experiments/g4_ablation.json`) |
| Same decision across backends | 3 runs, 2 backends (local subprocess, ConTree), 1 hash `b03b4dd4...` | [provenance](https://minjun0208.github.io/behavioral-disagreement-gate/provenance.html) and front page | `python runner.py --task tasks/mean.json --run-id xb --gen hardcoded --backend local --probe-budget 20 && python scorer.py runs/xb/trace.jsonl cfg_full.json`; repeat with `--backend contree` |
| Answer-key isolation | 49 canary checks, 0 hits | provenance | `python grader.py <run_id> && python canary_check.py <run_id>` |
| Scorer fidelity | stripped trace vs full trace: 90/90 identical decision hashes; JavaScript vs Python: 84/84 in Node and [84/84 in the browser](https://minjun0208.github.io/behavioral-disagreement-gate/test/scorer_test.html) | provenance | `python build_site_data.py --exclude-sessions demo1`; `node site/test/scorer_test.mjs site/data` |
| Live sessions | 7 LLM sessions on `round_half`: 6 `PASS` (five in 2 rounds, one in 3), 1 `UNVERIFIABLE` (`sw_live`: fewer than two distinct implementations left after G4). `live7` is the session in the demo video: witness `x = -12.5`, answer `-13`, one candidate dropped by G4 in round 2. 4 fixture sessions from the ablation. 1 fixture session `ref1` (`safe_div`) with a recorded Tavily reference block, `UNVERIFIABLE` after the answer left one implementation | [front page](https://minjun0208.github.io/behavioral-disagreement-gate/) | `python loop.py --gen llm ...` as above; `python loop.py --task tasks/safe_div.json --session ref1 --gen hardcoded --answers examples/safe_div_ref1.answers.json` |

## Limitations

1. **Every live LLM clarification session ran on one task, `round_half`.** Disagreement detection (G3) was measured on 8 tasks x 3 seeds = 24 runs per cohort, and one other axis (`safe_div`, error policy) went through the loop only as the scripted fixture session `ref1`; it is the live loop itself (LLM candidates, question, typed answer, acceptance test, repair) whose evidence is confined to rounding ties.
2. **If every candidate deviates the same way, G3 is silent by construction.** G4 catches that only for behaviors that were already confirmed (ablation case A). Unconfirmed, unanimous misreadings pass.
3. **One answer on one input does not identify a policy.** `round_half(-39.5) == -40` is consistent with away-from-zero and with half-down. The loop asks again when the next witness appears (live5 needed two rounds), and 7 of the 180 constrained compliance generations satisfied the given examples while implementing a different convention.
4. **Tasks are 8 pure Python functions**, one prompt format, temperature 0.8 throughout. No I/O, no state, no multi-file patches.
5. **Compliance cells are n = 10.** Differences between low counts (0/10, 1/10, 3/10) are not resolved.
6. **G4's unique catch was shown on a constructed case.** How often unanimous regression occurs in natural sessions was not measured.
7. **ConTree SDK is pre-alpha** (0.3.6 pinned; `conformance_contree.py` guards the 8 behaviours used). 89 of the 90 committed runs were executed on the local subprocess backend and 1 in a ConTree sandbox; the cross-backend hash identity rests on that one run.
8. **The reference search can be wrong.** Search snippets are third-party text; a misleading result can steer the answer, and the gate cannot tell. The mitigations are procedural, not technical: every snippet is shown with its source, the block sits under the options and never selects one, and the ledger records what was shown before the answer so a decision taken on bad information can be found and superseded.

<details>
<summary>Repository layout</summary>

```
runner.py  scorer.py  backends.py        the gate: execute and record / score / execution backends (local, contree)
loop.py  ledger.py  acceptance.py        clarification loop: orchestration / append-only decision ledger / typed acceptance tests
question.py  representative.py           deterministic question text / simplest separating witness
research.py                              Tavily reference search shown with each question; ledger 'reference' record; outside the verdict path
grader.py  canary_check.py  summarize.py post-hoc grading against gold/, canary isolation check, run table
llm_gen.py  providers.py                 candidate generation via Nebius Token Factory / answer providers (cli, scripted)
conformance_contree.py                   8 SDK behaviour checks (run first)
experiment_compliance.py                 prompt-constraint compliance experiment -> experiments/compliance_full.jsonl
experiment_g4_ablation.py                G4 paired ablation -> experiments/g4_ablation.json
build_site_data.py                       runs/ ledger/ clarify/ grades/ -> site/data (leak scan, lite==full check, canary summary)
tasks/                                   8 task definitions: probe generator, regression suite, fixture candidates
gold/                                    reference tests, grader only; not served by the site; isolation proven by the canary
examples/  cfg_*.json                    e2e and ablation fixtures; gate configuration variants
site/                                    the demo: static HTML + ES modules, no build step; site/data is committed
.github/workflows/pages.yml              publishes site/ only (never the repository root) to GitHub Pages
```
</details>

## License

Apache-2.0. See [LICENSE](LICENSE).
