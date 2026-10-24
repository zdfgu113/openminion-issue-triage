# What We Learned

Building a GitHub issue-triage agent on OpenMinion 0.1 with a fully local
model (llama.cpp + Qwen2.5-1.5B on a 2-core CPU VM, zero API spend).

## 1. Local-first is viable, but the model size floor is real
A 3B model at ~7 tokens/second of prefill could not finish a single
OpenMinion turn inside 500 seconds; a 1.5B model at ~36 tokens/second could.
OpenMinion turns are multi-call with ~2k-token prompts, so prefill speed —
not decode — is the binding constraint on CPU. "Runs on a laptop" claims
need this arithmetic, and we now do it before choosing a model.

## 2. Reliability is a harness property, not a prompt property
Our first version asked nicely and got: one issue answered in prose (no
tool call, no output), inflated priorities, and "draft replies" that
restated the issue. Few-shot examples and sterner instructions helped only
slightly. What actually fixed the output, measurably (4/5 records with
flaws → 5/5 records matching human ground truth on our fixtures), was:
- forced tool use so the deliverable is a schema-checked tool call;
- verify-and-retry: check the record exists after each run, retry once
  with a blunter instruction when it doesn't;
- deterministic guardrails that correct impossible combinations
  (feature request at P1, how-to question at P0) and log every change;
- a template fallback for reply text that fails a simple quality gate.
"Model proposes, rules verify" is the pattern we would reuse for any
small-model deployment.

## 3. Configuration has layers, and only some of them bite
The 60-second per-request transport timeout did not move when we set
`timeout_seconds` in the agent's provider overrides; it lived in the
top-level `providers` section of agents.json. Two plausible places for the
same key, one effective — we burned real time on this, and it's the kind
of thing a doctor-style diagnostic ("effective timeout for this agent:
60s, from providers.openai") would surface instantly.

## 4. Environment is part of the program
An ambient `http_proxy` in our sandbox made every local-endpoint request
fail with `Invalid port: ':1]'` — an error that blames the URL while the
cause is proxy handling of local addresses. Any SDK that reads proxy env
vars should bypass localhost by default or say "proxy" in the error.

## 5. Interop details decide whether features exist
OpenMinion sends the OpenAI object form of `tool_choice`; llama.cpp's
server expects a string and silently falls back to a default. So
"forced tool calls" degraded to "encouraged tool calls" on our stack —
and OpenMinion's re-prompting when a required tool wasn't called is what
saved several runs. Testing against the servers people actually run
locally (llama.cpp, Ollama) matters as much as testing against the spec.

## 6. What we would do next
- Try OpenMinion's Cortensor Portal provider for the same agent and
  compare hosted-model quality/latency against the local baseline.
- Add a live-GitHub mode demo against a real repository's issue queue
  (the code path exists; fixtures remain the reproducible default).
- Contribute the timeout-layer and tool_choice findings upstream as
  issues (and a fix PR if the maintainers point us at the right layer).
