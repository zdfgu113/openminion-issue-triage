# Issue Triage Agent — OpenMinion on a fully local model

An AI agent built with the **OpenMinion 0.1 Python SDK** that triages
GitHub issues: for each issue it records a **category**
(bug / feature / question / docs / other), a **priority** (P0–P3),
**suggested labels**, and a **draft first reply** to the author, then
renders a Markdown triage report.

It runs **entirely on a local model** — llama.cpp serving
Qwen2.5-1.5B-Instruct on a 2-core CPU VM — through OpenMinion's
custom OpenAI-compatible provider. No API key, no cloud spend.

Built for **Cortensor Hackathon #5** (DoraHacks).

## Why it is built this way

A 1.5B local model is a weak classifier on its own. This project is about
the harness that makes it reliable — *model proposes, rules verify*:

- **Forced tool use** — the deliverable is a schema-checked
  `record_triage` tool call, not free text.
- **Verify-and-retry** — after each issue the driver checks the record
  actually landed; if the model answered in prose instead, it retries
  once with a fresh session and a blunter instruction.
- **Deterministic guardrails** — impossible combinations are corrected
  in the recording tool (a feature request can't stay P1, a how-to
  question can't stay P0, a README complaint becomes `docs`), and every
  correction is logged inside the record and shown in the report.
- **Reply quality gate** — if the model's draft reply doesn't address
  the author, a category template is used and the model's draft is kept
  in the record for audit.

Measured on the bundled fixtures (human-labelled): the raw model alone
gets 2–3 of 5 fully right and occasionally produces nothing; the full
pipeline produces 5/5 records matching the ground truth. The iteration
history is in [BUILDING_BLOCKS.md](BUILDING_BLOCKS.md) and the analysis
in [WHAT_WE_LEARNED.md](WHAT_WE_LEARNED.md).

## Quick start

Prerequisites: Python 3.11+, a machine that can run a 1.5B GGUF model.

```bash
# 1. OpenMinion
python3 -m venv .venv && source .venv/bin/activate
pip install openminion          # or: pip install -e <openminion checkout>

# 2. Local model server (llama.cpp) — see scripts/setup_local.sh
./scripts/setup_local.sh        # downloads llama.cpp + Qwen2.5-1.5B GGUF
./tools/llama.cpp/llama-server -m tools/models/qwen2.5-1.5b-instruct-q4_k_m.gguf \
    --host 127.0.0.1 --port 8090 -c 8192

# 3. Point OpenMinion at it
export OPENMINION_HOME="$PWD" OPENMINION_DATA_ROOT="$PWD/.openminion"
export OPENAI_COMPATIBLE_API_KEY="local-no-key"
openminion setup --provider custom-openai-compatible \
    --base-url http://127.0.0.1:8090/v1 --model qwen2.5-1.5b-instruct --no-chat
# NOTE: raise the effective per-request timeout (top-level providers
# section, not the agent overrides) for slow local models:
python3 scripts/set_timeout.py 900

# 4. Run the triage agent on the bundled fixture issues
python agent/triage.py
#    -> agent/output/triage_report.md

# 5. Or triage a real public repo's open issues
python agent/triage.py --repo owner/name --limit 5
```

> If your environment forces an HTTP(S) proxy (common in sandboxes/CI),
> unset `http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY` for the
> agent process — otherwise local-endpoint requests fail with a
> misleading `Invalid port` error (see BUILDING_BLOCKS.md, finding #3).

## Layout

```
agent/triage.py            the agent: SDK tools, guardrails, driver, report
agent/fixtures/            labelled sample issues (reproducible default)
agent/output/              triage store (JSONL) + generated report
scripts/setup_local.sh     llama.cpp + model download
scripts/set_timeout.py     patches the effective provider timeout
BUILDING_BLOCKS.md         what we tested in OpenMinion, incl. 11 findings
WHAT_WE_LEARNED.md         retrospective
```

## Notes

- Fixtures are the default so anyone can reproduce the exact run in the
  demo video; `--repo` switches to live GitHub issues (unauthenticated
  API, public repos).
- On this hardware each issue takes ~3–6 minutes of local inference.
  The demo video is sped up; the report and JSONL store are the real,
  unedited outputs.
