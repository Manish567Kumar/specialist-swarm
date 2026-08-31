"""
attending.py — the Attending: a real Managed Agents coordinator whose roster
is the three blind specialists in specialists.py. It dispatches them in
parallel, synthesizes what comes back, and proposes a patch.

Precisely what is and isn't claimed here (an earlier docstring overstated it,
and review caught that):

- Each SPECIALIST is genuinely blind. No specialist prompt names another
  lane, says how many specialists exist, or implies a defect taxonomy.
- The ATTENDING does know its roster's lanes. It has to — routing lane-scoped
  material to the right specialist is the whole coordinator pattern, and you
  cannot delegate to an agent you can't describe. What it is NOT told is what
  defects exist, how many there are, or which lane the real bug lives in. It
  discovers the actual defect set from what independently comes back.

So: the lanes are scaffolded, the defects are discovered. `selfcheck` states
it that way too.

Design choice, stated plainly: the coordinator's fixed toolset
(agent_toolset_20260401) doesn't compose with arbitrary custom function
tools alongside a `multiagent` config, so `propose_patch` /
`report_no_defect_found` are realized as a structured JSON block in the
Attending's own final message, not as tool calls. We parse that block.
It's a text contract instead of a function-call contract — same effect,
simpler under a 2-hour build, and honestly disclosed as such.
"""
import json
import os
import re
from pathlib import Path

from anthropic import Anthropic

import events
import specialists

HERE = Path(__file__).resolve().parent
ENV_ID_PATH = HERE / ".environment_id"
COORDINATOR_ID_PATH = HERE / ".coordinator_id"

BETA_HEADER = {"anthropic-beta": "managed-agents-2026-04-01"}

# The Attending's synthesis step (Opus, after three specialists report) can go
# quiet for minutes at a stretch. The SDK's default read timeout fires in that
# silence and kills a session mid-diagnosis, so the stream gets a generous one.
STREAM_TIMEOUT_SECONDS = 1800.0


def _managed_client(api_key=None):
    return Anthropic(
        api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"),
        default_headers=BETA_HEADER,
        timeout=STREAM_TIMEOUT_SECONDS,
        max_retries=3,
    )

COORDINATOR_SYSTEM = """\
You are the Attending on a diagnostic panel for AI coordinator agents. A \
patient coordinator agent is failing its evaluation tickets. You do not \
know in advance what's wrong with it — that is exactly what your panel is \
for.

# Your roster

- Prompt-Logic Specialist: reviews only the system prompt
- Tool-Spec Specialist: reviews only the tool manifest
- Context Specialist: reviews only token/size stats and context shape

None of them knows about the other two. Each will independently report back \
a suspected defect in its own lane, or say plainly that its lane looks clean.

# How to run a diagnosis

1. Read the patient's materials yourself first (system prompt, tool \
   manifest, Defect Briefs from its failing tickets).
2. Delegate to all three specialists in parallel. Give each ONLY the \
   material relevant to its lane — do not dump everything on everyone:
   - Prompt-Logic gets: the system prompt text + the Defect Briefs
   - Tool-Spec gets: the tool manifest + the Defect Briefs
   - Context gets: the token/size stats + the Defect Briefs
3. Wait for all three verdicts. Do not guess at what a specialist will say.
4. Synthesize: some verdicts may be low-confidence or say "not my lane" — \
   weight accordingly. Rank the candidate fixes by expected impact on the \
   failing graders named in the Defect Briefs.
5. Decide on ONE concrete patch to try this round — the most impactful fix, \
   not everything at once. If, after genuinely considering all three \
   verdicts, none of them point to a defensible concrete fix, say so \
   honestly — do not invent a patch just to have produced one.

# Output contract (read carefully — this is machine-parsed)

End your final message with exactly one fenced block like this:

```agentrx-patch
{
  "no_defect_found": false,
  "system_prompt_find": "<exact substring from the system prompt to replace, or null>",
  "system_prompt_replace": "<the replacement text, or null>",
  "remove_tools": ["<tool name>", "..."],
  "rationale": "<one paragraph: which specialist's finding this comes from, and why>"
}
```

Rules for the block:
- `system_prompt_find` must be an EXACT substring that appears verbatim in \
  the system prompt you were given (copy-paste it, don't paraphrase). If \
  you have no prompt change to propose, both `system_prompt_find` and \
  `system_prompt_replace` must be null.
- `remove_tools` is a list of exact tool names to delete this round. Empty \
  list if none.
- Set `no_defect_found` to true only if you truly found nothing actionable \
  across all three lanes this round — that is a valid, honest outcome, not \
  a failure. If true, the other fields should be null/empty and \
  `rationale` should explain what you checked.
- Output ONLY that one block at the very end. No second block, no \
  alternate JSON elsewhere in your reply.
"""


def ensure_environment():
    if ENV_ID_PATH.exists():
        return ENV_ID_PATH.read_text().strip()
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    client = _managed_client(api_key)
    env = client.beta.environments.create(
        name="agentrx-env", config={"type": "cloud", "networking": {"type": "unrestricted"}}
    )
    ENV_ID_PATH.write_text(env.id)
    return env.id


def ensure_coordinator():
    # ensure_specialists() is idempotent AND it is the only caller of
    # push_prompts(). It used to sit BELOW the cached-id early return, so on
    # every run after the first it never executed: editing a specialist prompt
    # changed the local source and nothing else, while the server-side agents
    # kept running whatever text they were created with. That is precisely the
    # failure PROMPT_VERSION was introduced to prevent, and it silently
    # invalidated this build's central claim - the recorded specialists were
    # still carrying the roster leak the prompts had supposedly been fixed to
    # remove. It must run before the early return, not after it.
    ids = specialists.ensure_specialists()
    if COORDINATOR_ID_PATH.exists():
        return COORDINATOR_ID_PATH.read_text().strip()
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    client = _managed_client(api_key)
    coordinator = client.beta.agents.create(
        name="AgentRx Attending",
        model="claude-opus-4-7",
        system=COORDINATOR_SYSTEM,
        tools=[{"type": "agent_toolset_20260401"}],
        multiagent={
            "type": "coordinator",
            "agents": [{"type": "agent", "id": aid} for aid in ids.values()],
        },
        metadata={
            "hackathon": "partner-basecamp-2026",
            "track": "specialist-swarm",
            "project": "agentrx",
            "role": "attending",
        },
    )
    COORDINATOR_ID_PATH.write_text(coordinator.id)
    print(f"  attending created -> {coordinator.id}")
    return coordinator.id


PATCH_BLOCK_RE = re.compile(r"```agentrx-patch\s*(\{.*?\})\s*```", re.DOTALL)


def _extract_patch(text):
    """Pull the patch block out of the Attending's reply.

    Two deliberate choices, both from failures review predicted:

    - Take the LAST block, not the first. `run_round` concatenates every
      `agent.message` in the turn, and the Attending routinely narrates a
      draft block while thinking before committing to its final one. Taking
      the first would hand us the draft and discard the decision.
    - Never let bad JSON escape. `system_prompt_find` is prose copied out of a
      prompt full of quotes and em-dashes, so a malformed block is likely.
      Returning None routes it into the existing retry, whereas a raised
      JSONDecodeError used to abort the whole run — the "no block at all"
      case got two retries, while "block with a trailing comma" got none.
    """
    matches = PATCH_BLOCK_RE.findall(text)
    if not matches:
        return None
    try:
        return json.loads(matches[-1])
    except json.JSONDecodeError:
        return None


def build_user_message(briefs, system_prompt_text, tools_manifest_text, tool_stats, round_note=""):
    briefs_text = "\n\n".join(json.dumps(b["brief"], indent=2) for b in briefs)
    return f"""\
A diagnosis round is starting.{round_note}

===== DEFECT BRIEFS (from failing tickets) =====
{briefs_text}

===== PATIENT SYSTEM PROMPT (full text) =====
{system_prompt_text}

===== PATIENT TOOL MANIFEST (full JSON) =====
{tools_manifest_text}

===== TOOL/CONTEXT STATS =====
tool_count={tool_stats['tool_count']}, approx_tokens={tool_stats['approx_tokens']}
tool_names={tool_stats['tool_names']}

Run the diagnosis process now: delegate to your three specialists in \
parallel with lane-scoped material only, then synthesize per your output \
contract.
"""


def run_round(session_id, coordinator_id, environment_id, client, user_message, print_events=True):
    """Sends one user message into an existing (or brand-new) session,
    streams events, returns (final_text, event_log, input_char_count)."""
    final_text_parts = []
    events.publish("swarm", {"label": "mode", "actor": "live"})
    event_log = []

    with client.beta.sessions.events.stream(session_id) as stream:
        client.beta.sessions.events.send(
            session_id,
            events=[{"type": "user.message", "content": [{"type": "text", "text": user_message}]}],
        )
        for event in stream:
            t = event.type
            # Record the actor alongside the type. Storing bare type strings
             # meant a recorded run could never be replayed with its lanes
             # attributed - the sidebar's whole point - and the only
             # alternative would be guessing actor names at replay time,
             # which is fabricating provenance in the one panel that exists
             # to show what really happened.
            event_log.append({"type": t})

            # Mirror every interesting event onto the bus so the sidebar UI
            # shows the REAL swarm activity, not a re-enactment.
            # These attributes EXIST on the event objects and are typed
            # Optional[str], so they are frequently None (the primary agent has
            # no `from_agent_name`). A plain getattr default never fires for
            # those, which used to print "[reply <-] None" and leave the
            # sidebar's roster LEDs permanently dark.
            label, actor = None, None
            if t == "session.thread_created":
                label, actor = "spawned", getattr(event, "agent_name", None)
            elif t == "session.thread_status_running":
                label, actor = "running", getattr(event, "agent_name", None)
            elif t == "agent.thread_message_received":
                label, actor = "reply", getattr(event, "from_agent_name", None)
            elif t == "agent.thread_message_sent":
                label, actor = "delegate", getattr(event, "to_agent_name", None)
            elif t == "agent.tool_use":
                label, actor = "tool", getattr(event, "name", None)
            if label:
                actor = actor or "attending"
                event_log[-1].update({"label": label, "actor": actor})
                events.publish("swarm", {"label": label, "actor": actor})

            # A rate-limited or overloaded coordinator emits one of these and
            # then goes quiet. Without handling them the stream loop simply
            # keeps waiting until STREAM_TIMEOUT_SECONDS (30 minutes) — i.e. a
            # dead terminal and a spinning sidebar, on stage, on conference
            # wifi. Fail fast and let the caller record it as a real outcome.
            if t in ("session.error", "session.status_terminated", "session.deleted"):
                events.publish("swarm", {"label": "idle", "actor": "session"})
                detail = getattr(event, "error", None)
                raise RuntimeError(f"session ended early: {t}{f' ({detail})' if detail else ''}")

            if t == "agent.message":
                for block in event.content:
                    if getattr(block, "type", None) == "text":
                        final_text_parts.append(block.text)
            elif t == "session.status_idle":
                events.publish("swarm", {"label": "idle", "actor": "session"})
                break

            if print_events and label:
                arrow = {"spawned": "[thread spawned]", "running": "[running]        ",
                         "reply": "[reply <-]       ", "delegate": "[delegate ->]    ",
                         "tool": "[tool]           "}.get(label, f"[{label}]")
                print(f"    {arrow} {actor}", flush=True)

    return "".join(final_text_parts), event_log, len(user_message)


def diagnose(briefs, system_prompt_text, tools_manifest_text, tool_stats,
             max_rounds=3, carryover=""):
    """Runs up to max_rounds of diagnosis. Returns a dict with the patch
    history and per-round context-size accounting (feeds selfcheck).

    `carryover` is what the caller learned from the PREVIOUS outer iteration —
    the patch it tried, whether it actually applied, and what the score did.
    Each call opens a fresh session, so without this the Attending has no
    memory of its own last attempt and will happily re-propose a
    `system_prompt_find` string that its own earlier patch already deleted.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    client = _managed_client(api_key)

    environment_id = ensure_environment()
    coordinator_id = ensure_coordinator()

    session = client.beta.sessions.create(
        agent=coordinator_id, environment_id=environment_id, title="AgentRx diagnosis"
    )

    rounds = []
    round_note = carryover or ""
    for i in range(max_rounds):
        print(f"\n  -- round {i + 1}/{max_rounds} --")
        msg = build_user_message(briefs, system_prompt_text, tools_manifest_text, tool_stats, round_note)
        final_text, event_log, input_chars = run_round(session.id, coordinator_id, environment_id, client, msg)
        patch = _extract_patch(final_text)
        rounds.append({
            "round": i + 1,
            "input_chars": input_chars,
            "cumulative_session_events": sum(len(r["event_log"]) for r in rounds) + len(event_log),
            "event_log": event_log,
            "final_text": final_text,
            "patch": patch,
        })
        if patch is None:
            print("  WARNING: no parseable ```agentrx-patch block in this round's reply")
            # Append rather than overwrite — the caller's carryover about the
            # previous iteration is still true and still needed.
            round_note = (carryover or "") + (
                "\n\n(Your previous reply did not include a valid ```agentrx-patch "
                "block, or its JSON would not parse. Retry with the exact fenced "
                "block format and strictly valid JSON.)"
            )
            continue
        if patch.get("no_defect_found"):
            print("  Attending: no defect found this round (honest exit)")
            break
        print(f"  patch proposed: remove_tools={patch.get('remove_tools')}, "
              f"prompt_change={'yes' if patch.get('system_prompt_find') else 'no'}")
        break  # apply-and-eval loop lives in the caller (agentrx.py); one shot per call

    return {"session_id": session.id, "rounds": rounds}
