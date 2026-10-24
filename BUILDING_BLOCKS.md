# Building Blocks Tested — findings log (Cortensor Hackathon #5)

Running log of what we tested in OpenMinion 0.1.10, what worked, what didn't.
Environment: CPU-only Linux VM (2 cores, ~7.7 GB RAM, no GPU), Python 3.12,
OpenMinion installed from source (pip install -e of github.com/openminion/openminion).

## 1. Install & provider setup (2026-10-23)
- `pip install -e .` from the repo worked cleanly; `openminion --version` → 0.1.10.
- `openminion setup --list-providers` lists 20+ presets including Ollama (no
  credential), Cortensor Portal/Router, and `custom-openai-compatible`
  (requires --base-url). Setup is fully scriptable with
  `setup --provider ... --model ... --base-url ... --no-chat` and writes
  `.openminion/agents.json` when OPENMINION_HOME/OPENMINION_DATA_ROOT point at
  the project dir. 

## 2. Local model serving: llama.cpp instead of Ollama
- Ollama's official Linux bundle is now ~1.4 GB (zstd tarball) — heavy for a
  small VM. We used llama.cpp's prebuilt CPU binary (21 MB) and its
  OpenAI-compatible `llama-server` instead, wired through the
  `custom-openai-compatible` preset. Worked with no code changes.
- Models tried (GGUF Q4_K_M, local files):
  - Qwen2.5-3B-Instruct: prefill ≈ 7 tok/s on 2 threads in this VM → a single
    OpenMinion turn (multiple calls, ~2k-token prompts) could not finish
    inside even a 500 s window. Too slow for CPU-only demos.
  - Qwen2.5-1.5B-Instruct: prefill ≈ 36 tok/s → full tool-agent turn
    completed (see §4). Small local models are the practical floor for
    CPU-only OpenMinion use; model choice matters more than any config knob.

## 3. GOTCHA: ambient HTTP(S)_PROXY env breaks local endpoints
- First runs failed with `ProviderError: InvalidURL: Invalid port: ':1]'`
  before any request reached the local server. Cause: this environment sets
  `http_proxy/https_proxy/ALL_PROXY` (an egress proxy) and OpenMinion's
  urllib-based transport picks them up; the local `127.0.0.1` request then
  goes through proxy parsing and dies with a misleading port error, even
  though `NO_PROXY` listed 127.0.0.1.
- Workaround: unset all proxy variables for the OpenMinion process when the
  provider endpoint is local. Worth documenting for anyone running
  OpenMinion inside sandboxes/CI with forced egress proxies — the error
  message points at the URL, not the proxy.

## 4. First successful end-to-end run (2026-10-23)
- Official example `examples/sdk/tool_agent.py` pattern (decorated `@tool`,
  `forced_tools=["lookup_order"]`, `Agent(...).run(...)`) completed against
  the local llama-server: the model emitted the forced tool call, the tool
  returned `order A-104: ready`, and the agent answered in one sentence:
  "The order A-104 is currently ready." Total wall time 245 s.
- `agent.run(..., timeout_seconds=600)` is needed on slow local models; the
  default 90 s turn budget is calibrated for hosted APIs.

## 5. Transport retries & the 60 s per-request timeout
- With `OPENMINION_LLM_DEBUG=1` the JSONL debug log showed 6 requests for one
  turn: 3 timed out ("error: timed out", per-request `timeout_seconds: 60`
  in the debug record) and were retried before succeeding. The 60 s
  per-request cap appears independent of the provider
  `timeout_seconds: 900` we set in `provider_config_overrides` — on slow
  local inference the first (largest) prompt can exceed 60 s of prefill, so
  the retry logic, not the config, is what saved the run. Finding the right
  knob for the per-request transport timeout is an open item.

## 6. tool_choice format mismatch with llama.cpp
- llama-server logs: `Wrong type supplied for parameter 'tool_choice',
  using default value: type must be string, but is object`.
  OpenMinion sends the OpenAI object form (`{"type":"function",...}`);
  llama.cpp's server expects a string for the simple cases. Forced-tool runs
  still worked here (llama.cpp fell back to a default), but strict
  OpenAI-compatible servers may behave differently. Interop note for
  local-first deployments.

## 8. Where the per-request timeout actually lives (found 2026-10-23)
- Setting `timeout_seconds` in an agent's `provider_config_overrides`
  (model_connections) did NOT change the transport timeout — the debug log
  still showed 60 s. The effective value sits in the top-level `providers`
  section of agents.json (`providers.openai.timeout_seconds`, written as 60
  by setup). Raising that to 900 stopped the timeout/retry cycle and cut the
  per-issue time noticeably. Two config layers that look alike but only one
  drives the transport is an easy trap; worth a docs note.

## 9. forced_tools vs llama.cpp tool_choice (interop, 2026-10-23)
- OpenMinion's `forced_tools` sends the OpenAI object form of tool_choice;
  llama.cpp logs "Wrong type supplied for parameter 'tool_choice'... type
  must be string, but is object" and falls back to its default. So on
  llama.cpp the "forced" call is really just a strong suggestion.
- OpenMinion's runtime compensates: when a turn ends without the required
  tool call it re-prompts ("None of the tools required for this task have
  been called yet...") and the model then complies. Net effect: forced
  tools still guarantee a record, but can cost extra slow turns on local
  models. In our first (unforced) run, 1 of 5 issues produced no record at
  all and 2 were misclassified; with forced_tools + few-shot instructions,
  the crash report was classified bug/P0 correctly.

## 10. Small-model calibration limits (2026-10-23)
- Qwen2.5-1.5B repeatedly over-prioritises feature requests (dark mode →
  P1) even with an explicit few-shot rule "a feature request is never
  P0/P1 unless it reports an outage" and a matching worked example.
  Prompt-only calibration has a ceiling at this model size; our agent
  design therefore adds a deterministic guardrail layer in the recording
  tool (rules demote impossible combinations and log the adjustment) —
  "model proposes, rules verify" — which made the pipeline output stable
  enough to demo. Details in agent/triage.py.

## 11. Guardrail iteration, measured (2026-10-23)
Three versions of the same triage pipeline over the same 5 fixture issues:
- v1.0 (free-form, no forced tool): 4/5 records, multiple misclassifications,
  one issue answered in prose with no tool call at all.
- v1.1 (forced_tools + few-shot instructions): 4/5 records (a how-to
  question was still answered in prose — see finding #9), dark-mode feature
  over-prioritised to P1, README issue filed as bug, every draft_reply a
  restatement of the issue rather than a reply to its author.
- v1.2 (+ deterministic guardrails in the recording tool, + verify-and-
  retry loop, + author-addressing template fallback for draft replies):
  5/5 records; final results bug/P1, feature/P3 (guardrail-demoted),
  docs/P2 (guardrail-corrected), bug/P1; one partial miss (the CSV how-to
  question ended as docs/P2 because it mentions "docs" while searching
  them — the model's raw call was bug/P0, so the guardrail still improved
  it). A question-shape rule added in v1.3 (interrogative title + no
  malfunction keywords) fixes that last case in offline validation against
  the recorded raw model decisions: 5/5 match the human ground truth.
Takeaway: with a 1.5B local model, reliability came from the harness —
forced tool use, output verification with retry, and transparent
deterministic corrections — not from more prompt engineering.
Final v1.3 confirmation run (2026-10-23): 5/5 records, 5/5 matching the
human ground truth (bug/P0, feature/P3, question/P3, docs/P2, bug/P1).
The run itself was interrupted once by an environment restart; the
driver's idempotent resume (skip already-recorded issues) completed it
without duplicates — an accidental but genuine resilience test. Known
cosmetic residue: suggested labels are the weakest output (one issue got
an empty label list); classification and priority are the load-bearing
fields and those are what the guardrails protect.

## 7. Prompt weight in SDK mode
- The debug payload shows the runtime sends a large system prompt including
  a "Runtime Grounding" section listing the full enabled-tool catalog
  (browser, code, exec, git, github, graph, ...) even for an SDK agent
  restricted to one custom tool. On hosted models that's a minor cost; on
  local CPU models it's the dominant latency term (~2k tokens of prefill
  per call). A "lean prompt" option for embedded/SDK use would materially
  improve local-first viability.
