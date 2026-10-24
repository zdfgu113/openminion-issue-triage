"""Issue triage agent built on the OpenMinion 0.1 Python SDK.

For each GitHub issue the agent must call the `record_triage` tool exactly
once with:
  - category:   bug | feature | question | docs | other
  - priority:   P0 (blocking/outage) | P1 (major) | P2 (normal) | P3 (minor)
  - labels:     2-4 short suggested labels
  - draft_reply: a polite first-response comment for the issue author,
                 acknowledging the report and stating the next step.

Issues come from a local fixture file (default, fully reproducible) or
live from the GitHub API for a public repo (`--repo owner/name`).

Results are appended to a JSONL store and rendered into a Markdown report.

Run (from the project root, with the OpenMinion env configured):
    python agent/triage.py                      # fixture issues
    python agent/triage.py --repo octocat/Hello-World
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from openminion import Agent, tool

HERE = Path(__file__).resolve().parent
STORE = HERE / "output" / "triage_store.jsonl"
REPORT = HERE / "output" / "triage_report.md"

INSTRUCTIONS = """You are an issue triage assistant for an open-source project.
You will be given ONE GitHub issue (title and body). Your FIRST action must
be to call the record_triage tool exactly once with your assessment. Do not
write any analysis before the tool call.

How to choose the fields:
- category: "bug" = something is broken or errors; "feature" = request for
  a new capability; "question" = the author asks how to do something or
  where an option is; "docs" = documentation is wrong or outdated;
  otherwise "other".
- priority: "P0" = users are completely blocked or the service is down;
  "P1" = a major malfunction affecting many users but a workaround exists;
  "P2" = a normal issue with limited impact; "P3" = minor, cosmetic, or a
  nice-to-have request. A feature request is never P0/P1 unless it reports
  an outage. A how-to question is P2 or P3.
- labels: 2 to 4 short lowercase labels a maintainer would apply.
- draft_reply: write it TO the author. Start with "Thanks for ...", confirm
  in one sentence what was understood, and say what happens next (for
  example: "A maintainer will look into this"). Never restate the whole
  issue, never promise a fix date. 2-3 sentences maximum.

Examples:
- "App crashes on startup after upgrade, nobody can work" -> bug, P0.
- "Please add dark mode" -> feature, P3.
- "How do I export to CSV?" -> question, P3.
- "README install command is outdated" -> docs, P2.
- "Uploads over 10MB fail with 500 for some paying users" -> bug, P1.

After the tool call, reply with one short sentence summarising the triage."""


# --- Deterministic guardrails -------------------------------------------------
# Small local models mis-calibrate in predictable ways (see BUILDING_BLOCKS.md
# finding #10). The model proposes; these rules verify impossible or
# implausible combinations and log every adjustment in the record itself, so
# the report stays transparent about what was machine-corrected.

_DOCS_HINTS = ("readme", "documentation", "docs", "install instructions", "installation instructions")
_OUTAGE_HINTS = ("crash", "cannot start", "won't start", "down", "outage", "blocked", "data loss")


def apply_guardrails(record: dict, issue: dict) -> dict:
    title = issue.get("title", "")
    text = (title + " " + issue.get("body", "")).lower()
    adjustments: list[str] = []
    category, priority = record["category"], record["priority"]

    # Question shape: an interrogative title ("How do I ...?", "Is ...?",
    # "Where ...?") or an explicit ask in the body, with no malfunction
    # reported. Checked first: how-to issues often mention the docs while
    # searching them, which otherwise trips the docs rule below.
    import re as _re

    interrogative = bool(_re.match(r"^(how|is|are|can|could|where|what|why|does|do)\b", title.strip().lower()))
    asks = interrogative and ("?" in title or "is this supported" in text or "where is" in text)
    malfunction = any(h in text for h in ("crash", "error", "500", "fails", "exception", "broken", "bug"))
    if asks and not malfunction and category != "question":
        record["category"] = "question"
        adjustments.append(f"category {category}->question (interrogative issue, no malfunction)")
        category = "question"

    # Documentation wording with no app malfunction described -> docs.
    if category == "bug" and any(h in text for h in _DOCS_HINTS) and not any(
        h in text for h in ("crash", "error", "500", "fails", "broken")
    ):
        record["category"] = "docs"
        adjustments.append(f"category bug->docs (documentation wording, no malfunction)")

    # Feature requests and how-to questions are capped at P2/P3 unless the
    # text itself reports an outage or total blockage.
    has_outage = any(h in text for h in _OUTAGE_HINTS)
    if record["category"] == "feature" and priority in ("P0", "P1") and not has_outage:
        record["priority"] = "P3"
        adjustments.append(f"priority {priority}->P3 (feature request without outage)")
    elif record["category"] == "question" and priority in ("P0", "P1"):
        record["priority"] = "P3"
        adjustments.append(f"priority {priority}->P3 (how-to question)")
    elif record["category"] == "docs" and priority in ("P0", "P1"):
        record["priority"] = "P2"
        adjustments.append(f"priority {priority}->P2 (documentation issue)")

    # Draft-reply quality gate: small models tend to paste a restatement of
    # the issue into draft_reply instead of addressing the author. If the
    # draft does not speak to the author, replace it with a category-based
    # template and keep the model's text for audit.
    draft = (record.get("draft_reply") or "").strip()
    addresses_author = any(
        w in draft.lower()[:80] for w in ("thank", "thanks", "hi ", "hello", "we ", "we've", "sorry")
    )
    if not addresses_author or len(draft) < 40:
        templates = {
            "bug": "Thanks for reporting this. We've logged it as a {priority} bug and a maintainer will investigate; we'll update this issue as soon as we know more.",
            "feature": "Thanks for the suggestion. We've recorded it as a feature request for the maintainers to consider; feel free to add any extra detail that would help.",
            "question": "Thanks for asking. A maintainer will follow up with guidance shortly; in the meantime, the project documentation may already cover part of this.",
            "docs": "Thanks for pointing this out. We've flagged the documentation for correction and a maintainer will pick it up.",
            "other": "Thanks for raising this. We've triaged it for the maintainers, who will follow up here.",
        }
        record["model_draft_reply"] = draft
        record["draft_reply"] = templates.get(record["category"], templates["other"]).format(
            priority=record["priority"]
        )
        adjustments.append("draft_reply replaced by template (model draft did not address the author)")

    if adjustments:
        record["guardrail_adjustments"] = adjustments
    return record


@tool
def record_triage(
    issue_number: int,
    category: str,
    priority: str,
    labels: list[str],
    draft_reply: str,
) -> str:
    """Record the triage decision for one issue."""
    record = {
        "issue_number": issue_number,
        "category": category,
        "priority": priority,
        "labels": labels,
        "draft_reply": draft_reply,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    issue = CURRENT_ISSUE.get("issue") or {}
    record = apply_guardrails(record, issue)
    STORE.parent.mkdir(parents=True, exist_ok=True)
    with STORE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return f"recorded triage for issue #{issue_number}: {record['category']}/{record['priority']}"


CURRENT_ISSUE: dict = {}


def load_fixture_issues() -> list[dict]:
    return json.loads((HERE / "fixtures" / "sample_issues.json").read_text())


def load_live_issues(repo: str, limit: int) -> list[dict]:
    url = f"https://api.github.com/repos/{repo}/issues?state=open&per_page={limit}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "openminion-triage-agent", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    return [
        {
            "number": it["number"],
            "title": it["title"],
            "body": (it.get("body") or "")[:2000],
            "author": it["user"]["login"],
            "labels": [lb["name"] for lb in it.get("labels", [])],
        }
        for it in data
        if "pull_request" not in it
    ]


def render_report(issues: list[dict]) -> None:
    by_number = {it["number"]: it for it in issues}
    rows = []
    if STORE.exists():
        for line in STORE.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    lines = ["# Issue Triage Report", ""]
    lines.append(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("")
    for r in sorted(rows, key=lambda x: x["issue_number"]):
        issue = by_number.get(r["issue_number"], {})
        title = issue.get("title", "(title unavailable)")
        lines.append(f"## #{r['issue_number']} — {title}")
        lines.append("")
        lines.append(f"- **Category:** {r['category']}  **Priority:** {r['priority']}")
        lines.append(f"- **Suggested labels:** {', '.join(r['labels'])}")
        for adj in r.get("guardrail_adjustments", []):
            lines.append(f"- **Guardrail:** {adj}")
        lines.append("")
        lines.append("**Draft first reply:**")
        lines.append("")
        lines.append(f"> {r['draft_reply']}")
        lines.append("")
    REPORT.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=None, help="owner/name for live GitHub issues")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=900.0)
    args = parser.parse_args()

    issues = load_live_issues(args.repo, args.limit) if args.repo else load_fixture_issues()
    print(f"triaging {len(issues)} issues")

    def already_recorded(number: int) -> bool:
        if not STORE.exists():
            return False
        return any(
            json.loads(line).get("issue_number") == number
            for line in STORE.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )

    for issue in issues:
        prompt = (
            f"Issue #{issue['number']}: {issue['title']}\n"
            f"Author: {issue['author']}\n\n{issue['body']}"
        )
        print(f"--- issue #{issue['number']}: {issue['title']}", flush=True)
        CURRENT_ISSUE["issue"] = issue

        # Idempotent resume: a previous (interrupted) run's records are
        # kept, so re-running the driver never duplicates work.
        if already_recorded(issue["number"]):
            print("already recorded in a previous run, skipping", flush=True)
            continue

        def recorded() -> bool:
            return already_recorded(issue["number"])

        # Small local models sometimes answer in prose instead of calling the
        # tool (see BUILDING_BLOCKS.md finding #9). Verify the record exists;
        # if not, retry once with a fresh session and a blunter instruction.
        for attempt in (1, 2):
            run_prompt = prompt
            if attempt == 2:
                run_prompt = (
                    "Call the record_triage tool NOW with your best assessment "
                    "of the issue below. Do not answer the issue, do not "
                    "explain anything in text first.\n\n" + prompt
                )
            with Agent(
                instructions=INSTRUCTIONS,
                tools=[record_triage],
                forced_tools=["record_triage"],
                session_id=f"triage-{issue['number']}-a{attempt}",
            ) as agent:
                result = agent.run(run_prompt, timeout_seconds=args.timeout)
            print(result.text.strip()[:200], flush=True)
            if recorded():
                break
            print(f"WARNING: attempt {attempt} recorded nothing for issue #{issue['number']}", flush=True)

    render_report(issues)
    print(f"report written to {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
