"""Recover the holdout number that the crashed run never produced.

The record run reached 0/3 -> 3/3 on train and the gate opened correctly; the
holdout then died when the sandbox was emptied mid-flight. The PATCH itself is
preserved verbatim in artifacts/full_run.json, so this rebuilds the pristine
broken patient, re-applies that exact patch, verifies it landed, and runs the
holdout. It re-runs the evaluation, NOT the diagnosis - the swarm's findings
are not reconsidered and nothing here can improve the recorded diagnosis.
"""
import io
import json
import os
import sys

# Resolve from this file, not a hardcoded absolute path: this repo is a
# public fork whose whole point is being run on someone else's checkout.
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)

import agentrx
import bridge
import patcher
import sandbox

ART = os.path.join(HERE, "artifacts", "full_run.json")
report = json.load(io.open(ART, encoding="utf-8"))
patch = report["iterations"][-1]["patch"]

sandbox.acquire_run_lock()
try:
    print("=== rebuilding pristine broken patient ===")
    sandbox.reset()

    print("\n=== re-applying the recorded patch ===")
    verify = patcher.apply_patch(patch)
    print("  ", json.dumps(verify))
    if not verify.get("verified") or not verify.get("applied_anything"):
        raise SystemExit("the recorded patch did not re-apply cleanly; stopping "
                         "rather than reporting a holdout for an unknown state")

    print("\n=== HOLDOUT (T-4471 re-check + T-4490, T-4503) ===")
    raw = bridge.run_eval(trials=3, model=None, holdout=True)
    result = agentrx._split_holdout(raw)
    result["gate"] = "open"
    result["note"] = ("holdout re-run separately after the first attempt was "
                      "killed by a sandbox wipe mid-flight; same patch text, "
                      "re-applied and re-verified from disk first")

    # Through the shared summary, not a hand-rolled "%s/%s" - which rendered
    # None/None on a failed split, on the one script whose entire reason to
    # exist is recovering a missing holdout number, and never printed the
    # error that explained why.
    print()
    for line in agentrx._holdout_summary(result):
        print("  " + line)
    print("  per ticket   :", json.dumps(result.get("per_ticket")))

    report["holdout"] = result
    io.open(ART, "w", encoding="utf-8").write(json.dumps(report, indent=2))
    io.open(os.path.join(HERE, "AUTOPSY.md"), "w", encoding="utf-8").write(
        "".join(agentrx._autopsy_lines(report)))
    print("\nupdated artifacts/full_run.json + AUTOPSY.md")
finally:
    sandbox.release_run_lock()
