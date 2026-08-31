"""
clerk.py — compacts Meridian's raw failing transcripts into short, structured
Defect Briefs before anything reaches the swarm. Haiku tier. This is the
doctor's own context-engineering move, and it's what makes the self-diagnosis
question sharp later: does AgentRx *itself* over-stuff its coordinator?
"""
import json
import os

from anthropic import Anthropic

import bridge
import sandbox

CLERK_MODEL = "claude-haiku-4-5"

BRIEF_SCHEMA_HINT = """Return ONLY a JSON object with these exact keys:
{
  "task_id": "...",
  "failed_graders": ["..."],
  "expected": "one line — what a correct system would have done",
  "observed": "one line — what actually happened",
  "agent_stated_reason": "a short verbatim quote from the transcript, if the agent stated a rule/reason for its behavior, else null",
  "tool_calls": ["tool_name(brief-arg-summary) -> brief-result-summary", ...],
  "suspect_component": "system_prompt | tool_spec | context",
  "evidence_span": "the exact short quote (<=25 words) that best supports suspect_component"
}
No prose outside the JSON."""


def _client():
    return Anthropic()


def _load_raw_traces(ticket):
    traces = bridge.read_traces(ticket)
    if not traces:
        raise SystemExit(f"no traces for {ticket} — run bridge.capture('{ticket}') first")
    return traces


def _parse_brief_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text)


def compact(ticket, graders_failed, max_attempts=3):
    traces = _load_raw_traces(ticket)
    raw_text = json.dumps(traces, indent=None)
    raw_chars = len(raw_text)

    prompt = (
        f"Ticket {ticket} failed these graders: {graders_failed}.\n\n"
        f"Raw transcript (coordinator + any specialists), as JSON:\n{raw_text}\n\n"
        f"{BRIEF_SCHEMA_HINT}\n\n"
        f"Keep every field terse — this MUST fit well within your output budget. "
        f"Truncated JSON is useless; prefer shorter field values over completeness."
    )

    last_err = None
    for attempt in range(1, max_attempts + 1):
        resp = _client().messages.create(
            model=CLERK_MODEL,
            max_tokens=900,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text
        try:
            brief = _parse_brief_json(text)
            break
        except json.JSONDecodeError as e:
            last_err = e
            print(f"  clerk: attempt {attempt}/{max_attempts} produced invalid JSON "
                  f"({e}) — retrying with a stricter ask" if attempt < max_attempts else
                  f"  clerk: attempt {attempt}/{max_attempts} produced invalid JSON ({e}) — giving up")
            prompt = (
                f"{prompt}\n\nYour previous reply did not parse as JSON "
                f"({type(e).__name__}: {e}). Reply with ONLY the JSON object, "
                f"nothing else, and keep field values shorter so it isn't truncated."
            )
    else:
        raise RuntimeError(f"clerk could not produce valid JSON for {ticket} after "
                            f"{max_attempts} attempts: {last_err}")

    brief_chars = len(json.dumps(brief))
    return {
        "brief": brief,
        "raw_chars": raw_chars,
        "raw_approx_tokens": round(raw_chars / 4),
        "brief_chars": brief_chars,
        "brief_approx_tokens": round(brief_chars / 4),
        "usage": {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens},
    }


ALL_TRAIN_GRADERS = ["sso_addressed", "billing_resolved",
                     "no_false_resolution", "no_overclaim"]


def failing_graders(ticket="T-4471"):
    """Which graders ACTUALLY failed on the last harness run, read from the
    per-ticket `checks` the harness logs to runs.jsonl.

    This used to be a hardcoded list of all four graders. That was simply
    false — `sso_addressed` passes in every baseline — so the Defect Brief,
    the one artifact the whole compaction claim rests on, was asserting a
    failure that never happened and steering the Prompt-Logic specialist at
    a defect that isn't there. Measure it instead of asserting it.
    """
    tickets = bridge.last_run_tickets()
    checks = (tickets.get(ticket) or {}).get("checks")
    trials = (tickets.get(ticket) or {}).get("trials")
    if not checks or not trials:
        return list(ALL_TRAIN_GRADERS), "assumed (no per-ticket run history yet)"
    failed = [name for name, passes in checks.items() if passes < trials]
    return failed, "measured from runs.jsonl"


def briefs_for_train():
    """The only train ticket the harness actually has is T-4471 (the real
    harness ships one train ticket + two holdout tickets; the plan's
    ~30-transcript number was aspirational). The failing grader set is read
    from the last run rather than assumed."""
    failed, provenance = failing_graders("T-4471")
    result = compact("T-4471", failed)
    result["failed_graders_provenance"] = provenance
    return [result]


if __name__ == "__main__":
    sandbox.reset()
    bridge.capture("T-4471")
    results = briefs_for_train()
    for r in results:
        print(json.dumps(r["brief"], indent=2))
        print(f"  raw ~{r['raw_approx_tokens']} tok -> brief ~{r['brief_approx_tokens']} tok "
              f"({100 * (1 - r['brief_approx_tokens']/max(r['raw_approx_tokens'],1)):.0f}% compaction)")
