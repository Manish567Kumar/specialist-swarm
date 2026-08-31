"""
patcher.py — applies an Attending-proposed patch to the sandbox, and verifies
it actually landed by re-reading the file from disk. That verification step
exists specifically because a returned "applied" claim, unverified, is
Meridian's own "resolved" bug in miniature — see AUTOPSY's self-diagnosis.

Two things review caught here, both worth stating plainly:

1. Verification used to be `find not in reread`, which reports FAILED for a
   perfectly good patch whenever the replacement CONTAINS the original text
   (e.g. appending a qualifier to a rule) — the single most likely rewrite
   shape. It now compares the re-read file against the exact intended content.

2. `remove_tools` had no guardrail. The broken Meridian never escalates —
   that IS the planted defect — so `escalate_to_human` appears in no brief,
   and a specialist told to flag never-called tools would recommend deleting
   it. That would make holdout T-4503's `pentest_escalated` grader unpassable
   forever. PROTECTED_TOOLS refuses those removals and says so out loud.
"""
import json
import os

import sandbox

# Tools the patient structurally needs to score at all. Removing any of these
# doesn't slim the agent down, it lobotomises it. A refusal here is a better
# outcome than a silent catastrophe, and a better demo beat too.
PROTECTED_TOOLS = {
    "spawn_specialist",     # without it no specialist is ever assigned
    "write_response",       # without it the agent cannot answer the ticket
    "escalate_to_human",    # holdout T-4503 grades on escalation happening
}


def apply_patch(patch):
    """patch: the dict parsed from an ```agentrx-patch block.
    Returns a verification report — never trust the caller's claim, re-read."""
    report = {
        "prompt_changed": False,
        "tools_removed": [],
        "tools_refused": [],
        "verified": False,
        "errors": [],
    }

    prompt_path = os.path.join(sandbox.SANDBOX_DIR, "system-prompt-coordinator.txt")
    find = patch.get("system_prompt_find")
    replace = patch.get("system_prompt_replace")
    intended_prompt = None

    if find and replace is None:
        # The contract says find/replace are null together. A half-filled block
        # would otherwise silently DELETE the rule outright and then verify
        # clean, which is a destructive edit nobody asked for.
        report["errors"].append(
            "system_prompt_find was given with a null system_prompt_replace; "
            "refusing to delete the rule outright (send both, or neither)"
        )
    elif find:
        with open(prompt_path, "r", encoding="utf-8") as f:
            before = f.read()
        if find not in before:
            report["errors"].append(
                f"system_prompt_find not found verbatim in the prompt: {find[:80]!r}..."
            )
        else:
            intended_prompt = before.replace(find, replace, 1)
            with open(prompt_path, "w", encoding="utf-8") as f:
                f.write(intended_prompt)
            report["prompt_changed"] = True

    requested_tools = patch.get("remove_tools") or []
    protected_hits = [t for t in requested_tools if t in PROTECTED_TOOLS]
    remove_tools = [t for t in requested_tools if t not in PROTECTED_TOOLS]
    if protected_hits:
        report["tools_refused"] = protected_hits
        report["errors"].append(
            "refused to remove load-bearing tool(s) "
            f"{protected_hits} — the patient needs these to score at all"
        )

    if remove_tools:
        tools_path = os.path.join(sandbox.SANDBOX_DIR, "coordinator-tools.json")
        with open(tools_path, "r", encoding="utf-8") as f:
            spec = json.load(f)
        tools = spec if isinstance(spec, list) else spec.get("tools", spec)
        if isinstance(tools, list):
            kept = [t for t in tools if t.get("name") not in remove_tools]
            actually_removed = [t.get("name") for t in tools if t.get("name") in remove_tools]
            if isinstance(spec, list):
                new_spec = kept
            else:
                new_spec = dict(spec)
                new_spec["tools"] = kept
            with open(tools_path, "w", encoding="utf-8") as f:
                json.dump(new_spec, f, indent=2)
            report["tools_removed"] = actually_removed
            missed = [t for t in remove_tools if t not in actually_removed]
            if missed:
                report["errors"].append(f"tools requested for removal but not present: {missed}")
        else:
            report["errors"].append("coordinator-tools.json shape not a list — skipped tool removal")

    # Verification pass: re-read from disk, don't trust what we just wrote.
    verified_prompt_ok = True
    if report["prompt_changed"]:
        with open(prompt_path, "r", encoding="utf-8") as f:
            reread = f.read()
        verified_prompt_ok = (reread == intended_prompt)
        if not verified_prompt_ok:
            report["errors"].append("re-read prompt does not match the intended patched content")

    verified_tools_ok = True
    if remove_tools:
        stats = __import__("bridge").tool_manifest_stats()
        verified_tools_ok = all(name not in stats["tool_names"] for name in report["tools_removed"])
        if not verified_tools_ok:
            report["errors"].append("re-read tool manifest still contains a tool we removed")

    report["verified"] = verified_prompt_ok and verified_tools_ok and not report["errors"]

    # Did this patch change anything on disk at all? The caller uses this to
    # avoid paying for a full re-eval of a file it never actually touched, and
    # to avoid recording "patched" in the AUTOPSY when nothing was patched.
    report["applied_anything"] = bool(report["prompt_changed"] or report["tools_removed"])
    return report
