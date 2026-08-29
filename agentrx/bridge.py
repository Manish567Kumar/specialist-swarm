"""
bridge.py — thin adapter over the sandboxed Diagnose_Fix_Brief.py.

We never edit that file (editing it was off-limits in the original exercise,
and it stays off-limits here). We only *call* it as a subprocess and parse
its stdout + runs.jsonl, exactly like a human running it from a terminal would.
"""
import json
import os
import re
import subprocess
import sys

import sandbox

RESOLVED_RE = re.compile(r"RESOLVED\s+(\d+)/(\d+)\s+trials")
COST_RE = re.compile(r"COST\s+\$([\d.]+)\s+total\s+.\s+\$([\d.]+)/trial")


# The gated holdout runs three tickets x N trials against a still-partly-broken
# agent that burns turns; 900s was tight enough that a single rate-limit stall
# would blow it, killing the run AFTER it had been paid for and before the
# artifact was written.
DEFAULT_TIMEOUT = 2400


def _run(args, timeout=DEFAULT_TIMEOUT):
    harness = sandbox.harness_path()
    if not os.path.exists(harness):
        raise SystemExit("sandbox not built yet — run `python agentrx.py reset` first")
    cmd = [sys.executable, harness] + args
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"  # the harness prints ✓/❌ — force UTF-8 stdio, not cp1252
    proc = subprocess.run(
        cmd, cwd=sandbox.SANDBOX_DIR, capture_output=True, text=True,
        timeout=timeout, encoding="utf-8", errors="replace", env=env,
    )
    return proc


def _parse_scoreboard(stdout):
    resolved = RESOLVED_RE.search(stdout)
    cost = COST_RE.search(stdout)
    if resolved is None:
        # Returning None fields here used to surface several frames later as
        # `TypeError: unsupported format string passed to NoneType.__format__`
        # inside a print statement, pointing at the wrong line entirely - after
        # the eval had already been run and paid for.
        raise RuntimeError(
            "could not parse the harness scoreboard (no `RESOLVED n/n trials` line). "
            "Harness stdout tail:\n" + stdout[-1500:]
        )
    return {
        "resolved_trials": int(resolved.group(1)) if resolved else None,
        "total_trials": int(resolved.group(2)) if resolved else None,
        "cost_total": float(cost.group(1)) if cost else None,
        "cost_per_trial": float(cost.group(2)) if cost else None,
        "raw_stdout": stdout,
    }


def run_eval(trials=5, model=None, holdout=False):
    """Runs Diagnose_Fix_Brief.py against the current sandbox contents.
    holdout=True adds T-4490/T-4503 to the run (their own grader sets)."""
    args = ["--trials", str(trials)]
    if model:
        args += ["--model", model]
    if holdout:
        args.append("--holdout")
    proc = _run(args)
    if proc.returncode != 0:
        raise RuntimeError(f"harness failed (rc={proc.returncode}):\n{proc.stdout}\n{proc.stderr}")
    return _parse_scoreboard(proc.stdout)


def capture(ticket, model=None):
    """Runs one ticket once and writes trace-{ticket}-*.json into the sandbox —
    this is the clerk's raw input."""
    args = ["--capture", ticket]
    if model:
        args += ["--model", model]
    proc = _run(args)
    if proc.returncode != 0:
        raise RuntimeError(f"capture failed for {ticket} (rc={proc.returncode}):\n{proc.stdout}\n{proc.stderr}")
    return proc.stdout


def read_traces(ticket):
    """Loads whatever trace-{ticket}-*.json files exist in the sandbox."""
    if not os.path.isdir(sandbox.SANDBOX_DIR):
        raise SystemExit("sandbox not built yet - run `python agentrx.py reset` first")
    traces = {}
    for name in os.listdir(sandbox.SANDBOX_DIR):
        if name.startswith(f"trace-{ticket}-") and name.endswith(".json"):
            with open(os.path.join(sandbox.SANDBOX_DIR, name), "r", encoding="utf-8") as f:
                traces[name] = json.load(f)
    return traces


def runs_history():
    """Structured rows from the sandbox's own runs.jsonl (independent of the
    user's real workshop run history)."""
    path = os.path.join(sandbox.SANDBOX_DIR, "runs.jsonl")
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


TRAIN_TICKETS = ("T-4471",)
HOLDOUT_TICKETS = ("T-4490", "T-4503")


def last_run_tickets():
    """Per-ticket {resolved, trials} from the most recent harness run.

    Needed because `--holdout` runs TASKS + HOLDOUT_TASKS together and the
    scoreboard prints ONE grand total across all three tickets. Scraping that
    total and calling it "holdout" credits the holdout with the very ticket the
    patch was just fitted to - and prints an arithmetically impossible
    denominator (9 for two tickets at three trials). The harness logs the real
    per-ticket split to runs.jsonl; use that instead.
    """
    history = runs_history()
    if not history:
        return {}
    return history[-1].get("tickets", {}) or {}


def tool_manifest_stats():
    """context_stats-equivalent for the tool-spec/context specialists: tool
    count + rough token weight of coordinator-tools.json."""
    path = os.path.join(sandbox.SANDBOX_DIR, "coordinator-tools.json")
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    spec = json.loads(raw)
    tools = spec if isinstance(spec, list) else spec.get("tools", spec)
    names = [t.get("name") for t in tools] if isinstance(tools, list) else list(tools.keys())
    return {
        "tool_count": len(names),
        "tool_names": names,
        "raw_chars": len(raw),
        "approx_tokens": round(len(raw) / 4),
    }


if __name__ == "__main__":
    sandbox.reset()
    print("baseline (train only):")
    print(run_eval(trials=3))
