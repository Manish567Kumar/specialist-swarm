"""
chat.py — AgentRx's front desk: a real tool-using chat agent (plain Messages
API, not Managed Agents — this layer is a thin dispatcher, not the swarm
itself) that lets you pick which agent to diagnose and reports back in
plain language once the swarm has run.

    python chat.py            # terminal REPL
    python server.py          # two-pane console with the live swarm sidebar

The registry (AGENTS) is deliberately generalized — Meridian is the only
wired-up target today, but adding another is a registry entry plus a
sandbox/bridge adapter, not a rewrite of this file.
"""
import json
import sys
from pathlib import Path

from anthropic import Anthropic

import agentrx
import replay

HERE = Path(__file__).resolve().parent

AGENTS = {
    "meridian": {
        "name": "Meridian",
        "description": (
            "Day-1 Ex-04's coordinator agent — a claims/support triage system. "
            "Broken on T-4471 (SSO + billing ticket): routes to only one "
            "specialist per ticket and claims tickets resolved when they aren't."
        ),
    },
}

SYSTEM_PROMPT = """\
You are the front desk for AgentRx, a diagnostic swarm that finds and patches \
defects in other AI agents. You do not diagnose anything yourself — you \
dispatch to the real diagnostic pipeline (a coordinator plus three blind \
specialists: prompt-logic, tool-spec, context) via your tools, and relay \
what it actually found.

When a user asks which agents are available, call list_agents.

When they pick one, call replay_diagnosis. It replays the most recent
recorded run into the live sidebar in seconds, with zero API calls. Say
plainly that it is a replay of a recorded run — never imply it just ran.

Only call run_diagnosis if the user explicitly asks for a live, real, or
fresh run. It takes ten to twelve minutes and costs real money; say so
before you start, and warn that the sidebar is quiet during the blocking
evaluation phases.

Refer to the thing being diagnosed as "the agent" or by its name — never
as "the patient".

When reporting results, always give:
1. The headline: resolved X/Y before -> X/Y after (and holdout if it ran)
2. What specifically got patched, and which specialist's finding it came from
3. The context number: the agent's tool count before -> after
4. Never claim a number you weren't given by the tool result. If a holdout
   didn't run because the gate stayed closed, say that plainly — it is not
   a failure, it is the anti-gaming mechanism working as designed.

If asked to diagnose an agent not in the registry, say so honestly and
list what IS available — don't improvise a fake diagnosis.
"""

TOOLS = [
    {
        "name": "list_agents",
        "description": "List every agent AgentRx knows how to diagnose.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "run_diagnosis",
        "description": (
            "Runs a FRESH LIVE diagnosis: banks a baseline, compacts failing "
            "transcripts, dispatches the specialist panel, applies the Attending's "
            "patch, re-evaluates, and holdout-gates. Real API calls, real money, "
            "and TEN TO TWELVE MINUTES during which most phases are blocking "
            "harness subprocesses. Only call this when the user explicitly asks "
            "for a live/real/fresh run - otherwise use replay_diagnosis."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "enum": list(AGENTS.keys())},
                "trials": {"type": "integer", "default": 3, "description": "harness trials per eval"},
                "iterations": {"type": "integer", "default": 3, "description": "max diagnose/patch/eval rounds"},
                "holdout": {"type": "boolean", "default": True},
            },
            "required": ["agent_id"],
        },
    },
    {
        "name": "replay_diagnosis",
        "description": (
            "Replays the most recent RECORDED diagnosis into the live swarm sidebar "
            "at demo pace, with zero API calls. Returns in seconds. This is the "
            "DEFAULT way to show a diagnosis - prefer it unless the user explicitly "
            "asks for a live/real/fresh run."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "enum": list(AGENTS.keys())},
                "speed": {"type": "number", "default": 1.0,
                          "description": "1.0 = demo pace, 2.0 = twice as fast"},
            },
            "required": ["agent_id"],
        },
    },
    {
        "name": "get_last_report",
        "description": "Reads the most recently recorded diagnosis report, without re-running anything.",
        "input_schema": {
            "type": "object",
            "properties": {"agent_id": {"type": "string", "enum": list(AGENTS.keys())}},
            "required": ["agent_id"],
        },
    },
]


def _tool_list_agents(_args):
    return {aid: a["description"] for aid, a in AGENTS.items()}


def _tool_run_diagnosis(args):
    agent_id = args["agent_id"]
    if agent_id not in AGENTS:
        return {"error": f"unknown agent_id {agent_id!r}. Known: {list(AGENTS.keys())}"}
    if agent_id != "meridian":
        return {"error": f"{agent_id} is registered but has no wired sandbox/bridge adapter yet"}

    class Args:
        trials = args.get("trials", 3)
        iterations = args.get("iterations", 3)
        holdout = args.get("holdout", True)
        model = None
        live = False

    print(f"\n  [running real diagnosis against {agent_id} — this takes several minutes]\n", flush=True)
    report = agentrx.cmd_record(Args())
    # Trim to what the chat agent actually needs — don't hand it 30KB of raw event logs.
    summary = _summarize(report)
    summary["mode"] = "live"
    return summary


def _tool_replay_diagnosis(args):
    agent_id = args["agent_id"]
    if agent_id not in AGENTS:
        return {"error": f"unknown agent_id {agent_id!r}. Known: {list(AGENTS.keys())}"}
    report = agentrx._read_artifact("full_run.json")
    if report is None:
        return {"error": "no recorded run to replay yet - run a live diagnosis first "
                         "(`python agentrx.py record`), or ask me to run one live."}
    meta = replay.replay_report(report, speed=args.get("speed", 1.0))
    summary = _summarize(report)
    summary.update(meta)
    return summary


def _scoreboard(block):
    """Strip `raw_stdout` before handing an eval block to the model.

    _summarize's comment claimed it trimmed the payload; it did not. Each of
    the three eval blocks carries the harness's full captured stdout, which
    measured 14.9KB of the 22.3KB tool result on the recorded run — pasted
    into the context window of a build whose thesis is context engineering."""
    if not isinstance(block, dict):
        return block
    keep = ("resolved_trials", "total_trials", "cost_total", "cost_per_trial",
            "gate", "error", "note", "per_ticket", "train_recheck",
            "mixed_resolved_trials", "mixed_total_trials", "holdout_split_error")
    return {k: v for k, v in block.items() if k in keep}


def _summarize(report):
    return {
        "baseline": _scoreboard(report["baseline"]["eval"]),
        "final_train": _scoreboard(report["final_train"]),
        "holdout": _scoreboard(report["holdout"]),
        "tool_stats_before": report["tool_stats_before"],
        "tool_stats_after": report["tool_stats_after"],
        "iterations": [
            {
                "iteration": it["iteration"],
                "outcome": it.get("outcome", "patched"),
                "patch_rationale": (it.get("patch") or {}).get("rationale"),
                "eval_after": _scoreboard(it.get("eval_after")),
            }
            for it in report["iterations"]
        ],
    }


def _tool_get_last_report(args):
    report = agentrx._read_artifact("full_run.json")
    if report is None:
        return {"error": "no recorded run found yet — call run_diagnosis first"}
    return {
        "baseline": _scoreboard(report["baseline"]["eval"]),
        "final_train": _scoreboard(report["final_train"]),
        "holdout": _scoreboard(report["holdout"]),
        "tool_stats_before": report["tool_stats_before"],
        "tool_stats_after": report["tool_stats_after"],
    }


DISPATCH = {
    "list_agents": _tool_list_agents,
    "run_diagnosis": _tool_run_diagnosis,
    "replay_diagnosis": _tool_replay_diagnosis,
    "get_last_report": _tool_get_last_report,
}


def run_repl():
    # Uses the same brain as the web console (chat_backend) so the two
    # front-ends can't drift apart.
    import chat_backend

    print("AgentRx front desk. Ask which agents are available, or ask to diagnose one. Ctrl+C to quit.")
    print("(for the two-pane view with the live swarm sidebar: python server.py)\n")

    while True:
        try:
            user_input = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user_input:
            continue
        print("agentrx>", chat_backend.ask(user_input))


if __name__ == "__main__":
    run_repl()
