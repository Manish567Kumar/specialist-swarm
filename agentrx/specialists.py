"""
specialists.py — the diagnostic panel, as real Claude Managed Agents.

Three specialists, each genuinely blind to the others. No specialist prompt
names another lane, states how many specialists exist, or hints that a defect
taxonomy exists at all — each is simply handed material and asked what it
sees. The Attending discovers the families from what independently comes back.

(An earlier revision of these prompts ended with "someone else is reviewing
the tools, someone else the context budget", which quietly told every
specialist the whole roster and made the blindness claim false. Review caught
it. The claim is now true, and `agentrx.py selfcheck` states the precise
version of it.)

Mirrors ../create_specialists.py's shape, retargeted from Deal Desk roles
onto defect families.
"""
import json
import os
from pathlib import Path

from anthropic import Anthropic

HERE = Path(__file__).resolve().parent
SPECIALIST_IDS_PATH = HERE / ".specialist_ids.json"
PROMPT_VERSION_PATH = HERE / ".specialist_prompt_version"

BETA_HEADER = {"anthropic-beta": "managed-agents-2026-04-01"}

# Bump when any system prompt below changes, so ensure_specialists() pushes the
# new text to the already-created server-side agents instead of silently
# running the old prompt forever.
PROMPT_VERSION = "2"

_LANE_TAIL = (
    "Keep your reply under ~250 words. Confine your verdict strictly to the "
    "material you were handed. Do not speculate about parts of the agent you "
    "were not shown, and do not assume anyone else is looking at them."
)

SPECIALISTS = [
    {
        "key": "prompt_logic",
        "name": "Prompt-Logic Specialist",
        "model": "claude-sonnet-4-6",
        "system": (
            "You are the Prompt-Logic Specialist on a diagnostic panel. You review "
            "an AI coordinator's SYSTEM PROMPT for defects. Confine your analysis to "
            "the instruction text itself.\n\n"
            "You will receive: the system prompt text, and one or more Defect Briefs "
            "(structured summaries of tickets the agent handled wrong).\n\n"
            "Look specifically for:\n"
            "- Rules that conflict with each other\n"
            "- Instructions that are unreachable given other constraints in the same prompt\n"
            "- Any part of the agent's OUTPUT that the prompt fixes in advance rather "
            "than letting it follow from what actually happened\n"
            "- Any cost the prompt attaches to the agent admitting it cannot finish, "
            "which trains it to declare success instead of handing off\n\n"
            "Output a structured verdict:\n"
            "1. The exact rule/sentence you suspect (quote it verbatim, character for "
            "character — it will be used as a literal search string)\n"
            "2. Why it produces the observed failure\n"
            "3. A minimal proposed rewrite of just that rule\n"
            "4. Confidence: high / medium / low\n\n"
            + _LANE_TAIL
        ),
    },
    {
        "key": "tool_spec",
        "name": "Tool-Spec Specialist",
        "model": "claude-sonnet-4-6",
        "system": (
            "You are the Tool-Spec Specialist on a diagnostic panel. You review an "
            "AI coordinator's TOOL MANIFEST for defects. Confine your analysis to the "
            "tools themselves.\n\n"
            "You will receive: the full tool manifest (names + descriptions), and one "
            "or more Defect Briefs showing which tools were actually called.\n\n"
            "Look specifically for:\n"
            "- Near-duplicate tools that do the same job under different names (this "
            "causes choice paralysis and wasted turns)\n"
            "- Tools whose purpose is not clear from the name and description alone\n"
            "- Missing constraints or enums that would prevent malformed calls\n"
            "- Tools that were never called in any brief you were shown. Be careful "
            "here: an uncalled tool can mean dead weight, but it can equally mean the "
            "agent is broken in a way that PREVENTS it being called — in which case "
            "removing it would destroy the capability instead of restoring it. Say "
            "which of the two you think it is. Never recommend removing a tool the "
            "agent plainly needs in order to handle its tickets correctly.\n\n"
            "Output a structured verdict:\n"
            "1. Which tools you'd remove or merge, and why\n"
            "2. The resulting tool count if your recommendation is applied\n"
            "3. Any tool whose description you'd rewrite, and the rewrite\n"
            "4. Confidence: high / medium / low\n\n"
            + _LANE_TAIL
        ),
    },
    {
        "key": "context",
        "name": "Context Specialist",
        "model": "claude-sonnet-4-6",
        "system": (
            "You are the Context Specialist on a diagnostic panel. You review how much "
            "and what an AI coordinator is forced to read before it can act. Confine "
            "your analysis to the shape and volume of what is placed in its context "
            "window.\n\n"
            "You will receive: token/size stats for the coordinator's inputs (tool "
            "manifest size, few-shot example count, transcript length), and one or "
            "more Defect Briefs.\n\n"
            "Look specifically for:\n"
            "- Context stuffing: information included 'just in case' that the task "
            "never needed\n"
            "- Positioning problems: important constraints buried in the middle of a "
            "long prompt (lost-in-the-middle)\n"
            "- Turn waste: multiple tool calls that could have been answered by one "
            "well-scoped call\n"
            "- Budget: is the coordinator close to any stated turn/token cap, and if "
            "so, is that cap itself causing correct behavior to become unreachable?\n\n"
            "Output a structured verdict:\n"
            "1. What you'd cut, and the estimated size reduction\n"
            "2. What you'd keep, and why it's load-bearing\n"
            "3. Whether the failure looks like a context problem at all (say so plainly "
            "if it doesn't — a false positive here wastes the Attending's patch budget)\n"
            "4. Confidence: high / medium / low\n\n"
            + _LANE_TAIL
        ),
    },
]


def create_all():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("Set ANTHROPIC_API_KEY before running.")

    client = Anthropic(api_key=api_key, default_headers=BETA_HEADER)

    ids = {}
    for spec in SPECIALISTS:
        agent = client.beta.agents.create(
            name=spec["name"],
            model=spec["model"],
            system=spec["system"],
            tools=[{"type": "agent_toolset_20260401"}],
            metadata={
                "hackathon": "partner-basecamp-2026",
                "track": "specialist-swarm",
                "project": "agentrx",
                "role": spec["key"],
            },
        )
        ids[spec["key"]] = agent.id
        print(f"  created {spec['name']:28s} -> {agent.id}")

    SPECIALIST_IDS_PATH.write_text(json.dumps(ids, indent=2))
    print(f"saved {len(ids)} specialist IDs -> {SPECIALIST_IDS_PATH}")
    return ids


def push_prompts(ids):
    """Push the current system prompts onto already-created server-side agents.

    Without this, editing a prompt in this file changes nothing about the
    agents that actually run — the ids are cached and create_all() is skipped,
    so the swarm would keep using whatever text it was born with."""
    client = Anthropic(default_headers=BETA_HEADER)
    for spec in SPECIALISTS:
        agent_id = ids.get(spec["key"])
        if not agent_id:
            continue
        current = client.beta.agents.retrieve(agent_id)
        if (current.system or "") == spec["system"]:
            continue
        client.beta.agents.update(agent_id, version=current.version, system=spec["system"])
        print(f"  updated system prompt: {spec['name']} ({agent_id})")


def load_ids():
    if not SPECIALIST_IDS_PATH.exists():
        return None
    return json.loads(SPECIALIST_IDS_PATH.read_text())


SKILLS_ATTACHED_MARKER = HERE / ".skills_attached"


def ensure_specialists():
    ids = load_ids()
    freshly_created = not ids or len(ids) != len(SPECIALISTS)
    if freshly_created:
        ids = create_all()

    if freshly_created or not SKILLS_ATTACHED_MARKER.exists():
        import upload_skills
        attached = upload_skills.upload_and_attach()
        # Only claim the skills are attached if every one of them actually is.
        # A missing SKILL.md used to be printed and skipped, after which the
        # marker was written anyway and "each specialist carries its playbook"
        # became an unverified assertion.
        if len(attached) == len(upload_skills.SKILL_TO_SPECIALIST):
            SKILLS_ATTACHED_MARKER.write_text("ok")
        else:
            missing = set(upload_skills.SKILL_TO_SPECIALIST) - set(attached)
            print(f"  WARNING: skills not fully attached, missing: {sorted(missing)}")

    # Unconditional. This used to be gated on a hand-bumped PROMPT_VERSION
    # stamp, which reproduces B1's exact symptom for anyone who edits a prompt
    # and forgets to bump it: the local text changes, the server-side agent
    # keeps running what it was created with, and this build's central claim is
    # silently false. push_prompts() already no-ops per agent when the stored
    # system prompt matches, so the gate bought three cheap GETs and cost the
    # guarantee. The stamp is kept as a log of what was last pushed.
    push_prompts(ids)
    PROMPT_VERSION_PATH.write_text(PROMPT_VERSION)

    return ids


if __name__ == "__main__":
    ensure_specialists()
