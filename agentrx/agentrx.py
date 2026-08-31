#!/usr/bin/env python
"""
agentrx.py — single entry point for the AgentRx build.

python agentrx.py reset       # sandbox <- pristine broken Meridian (git show HEAD:)
python agentrx.py baseline    # bank the bad number + capture T-4471 transcripts
python agentrx.py briefs      # show the compaction: raw transcript -> defect brief
python agentrx.py diagnose    # dispatch the specialist panel + Attending synthesis, live
python agentrx.py record      # end-to-end -> artifacts/ + AUTOPSY.md
python agentrx.py demo        # replays artifacts/. zero network calls
python agentrx.py demo --live #     same beats, but re-runs the diagnosis for real
python agentrx.py selfcheck   # AgentRx audited against its own taxonomy (measured half)
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

# The harness reconfigures its own stdio for this reason; agentrx.py prints the
# same em-dashes and arrows, and without this piping the output to a file on a
# cp1252 console mangles or crashes it.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import events
import sandbox
import bridge
import clerk
import patcher
import attending

ARTIFACTS_DIR = HERE / "artifacts"


def _write_artifact(name, data):
    """Write via a temp file and rename.

    This used to json.dump straight into the destination handle, which meant a
    Ctrl+C or a full disk during the final write left a HALF-WRITTEN
    full_run.json on top of the good one. The checkpoints exist precisely so a
    late failure cannot cost the earlier stages; writing in place gave that
    guarantee away at the last step, and full_run.json is the file the stage
    command reads."""
    ARTIFACTS_DIR.mkdir(exist_ok=True)
    path = ARTIFACTS_DIR / name
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)
    return path


def _read_artifact(name):
    path = ARTIFACTS_DIR / name
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        # A corrupt artifact used to surface as a raw traceback, two lines
        # above the friendly "run `record` first" message that handles the
        # much less confusing case of the file simply being absent.
        raise SystemExit(
            f"artifacts/{name} is present but unreadable ({type(e).__name__}: {e}).\n"
            f"Re-run `python agentrx.py record`, or restore it from artifacts_best/.")


def cmd_reset(args):
    sandbox.reset()


def cmd_baseline(args, trials=None):
    # `agentrx.py baseline --trials 5` used to silently run 3: the subcommand
    # dispatcher calls this with no `trials`, so the default won.
    trials = trials if trials is not None else args.trials
    sandbox.reset()
    stats = bridge.tool_manifest_stats()
    result = bridge.run_eval(trials=trials, model=args.model)
    bridge.capture("T-4471", model=args.model)
    out = {"trials": trials, "eval": result, "tool_stats": stats, "ts": time.time()}
    print(f"BASELINE  resolved {result['resolved_trials']}/{result['total_trials']} trials  "
          f"cost {_fmt_cost(result.get('cost_total'))}  tools {stats['tool_count']}")
    return out


def cmd_briefs(args):
    results = clerk.briefs_for_train()
    for r in results:
        print(json.dumps(r["brief"], indent=2))
        pct = 100 * (1 - r["brief_approx_tokens"] / max(r["raw_approx_tokens"], 1))
        print(f"  raw ~{r['raw_approx_tokens']} tok -> brief ~{r['brief_approx_tokens']} tok ({pct:.0f}% compaction)")
    return results


def _split_holdout(raw):
    """Recover the true holdout-only score from a `--holdout` run.

    The harness runs TASKS + HOLDOUT_TASKS together and prints ONE grand total
    across all three tickets, so the scraped headline includes T-4471 — the
    very ticket the patch was just fitted to. Reporting that as "holdout"
    inflates the generalization claim by up to a third and prints an
    impossible denominator (9 for two tickets at three trials). The harness
    logs the real per-ticket split; use it.
    """
    tickets = bridge.last_run_tickets()
    missing = [t for t in bridge.HOLDOUT_TICKETS if t not in tickets]
    if missing:
        # Return the grand total under DIFFERENT keys. It used to be returned
        # as-is, which left `resolved_trials` holding the train-contaminated
        # total - and every consumer keys on `"resolved_trials" in h`, so the
        # headline bullet in AUTOPSY.md, the demo scoreboard and the front-desk
        # model's context all read "Holdout (T-4490, T-4503 ONLY): 9/9". Nine is
        # an impossible denominator for two tickets at three trials, which is
        # exactly what this function's docstring says must never be printed.
        out = dict(raw)
        out["mixed_resolved_trials"] = raw["resolved_trials"]
        out["mixed_total_trials"] = raw["total_trials"]
        out.pop("resolved_trials", None)
        out.pop("total_trials", None)
        out["holdout_split_error"] = (
            f"per-ticket rows missing for {missing}; the harness grand total "
            f"across train+holdout was {out['mixed_resolved_trials']}/"
            f"{out['mixed_total_trials']}, which is NOT the holdout number and "
            f"is not reported as one")
        return out
    out = dict(raw)
    out["mixed_resolved_trials"] = raw["resolved_trials"]
    out["mixed_total_trials"] = raw["total_trials"]
    out["resolved_trials"] = sum(tickets[t]["resolved"] for t in bridge.HOLDOUT_TICKETS)
    out["total_trials"] = sum(tickets[t]["trials"] for t in bridge.HOLDOUT_TICKETS)
    out["per_ticket"] = tickets
    out["train_recheck"] = {t: tickets[t] for t in bridge.TRAIN_TICKETS if t in tickets}
    return out


def _carryover_note(iter_record):
    """What the next outer iteration needs to know about this one. Each
    attending.diagnose() call opens a FRESH session, so without this the
    Attending has no memory of its own last patch and re-proposes a
    `system_prompt_find` string that its own edit already removed."""
    patch = iter_record.get("patch") or {}
    verify = iter_record.get("patch_verify") or {}
    ea = iter_record.get("eval_after")
    lines = [
        "",
        "",
        "(Context from your PREVIOUS attempt in this diagnosis — you are in a "
        "fresh session and cannot see it otherwise:",
        f"- you proposed: remove_tools={patch.get('remove_tools')}, "
        f"prompt_change={'yes' if patch.get('system_prompt_find') else 'no'}",
        f"- it applied to disk: {verify.get('applied_anything')}; verified: {verify.get('verified')}",
    ]
    if verify.get("errors"):
        lines.append(f"- errors: {verify['errors']}")
    if ea:
        lines.append(f"- score after it: resolved {ea['resolved_trials']}/{ea['total_trials']}")
    lines.append("The system prompt and tool manifest shown below are the CURRENT, "
                 "already-patched versions. Quote `system_prompt_find` from what you "
                 "see now, not from what you remember.)")
    return "\n".join(lines)


def cmd_diagnose(args, max_outer_iters=None, attending_rounds=2, holdout=None):
    max_outer_iters = max_outer_iters if max_outer_iters is not None else args.iterations
    holdout = args.holdout if holdout is None else holdout

    sandbox.acquire_run_lock()
    try:
        return _diagnose_locked(args, max_outer_iters, attending_rounds, holdout)
    finally:
        sandbox.release_run_lock()


def _diagnose_locked(args, max_outer_iters, attending_rounds, holdout):
    baseline = cmd_baseline(args, trials=args.trials)
    briefs = cmd_briefs(args)

    iterations = []
    carryover = ""

    for i in range(max_outer_iters):
        print(f"\n=== ITERATION {i + 1}/{max_outer_iters} ===")
        prompt_path = os.path.join(sandbox.SANDBOX_DIR, "system-prompt-coordinator.txt")
        tools_path = os.path.join(sandbox.SANDBOX_DIR, "coordinator-tools.json")
        with open(prompt_path, "r", encoding="utf-8") as f:
            system_prompt_text = f.read()
        with open(tools_path, "r", encoding="utf-8") as f:
            tools_manifest_text = f.read()
        tool_stats = bridge.tool_manifest_stats()

        try:
            diag = attending.diagnose(briefs, system_prompt_text, tools_manifest_text, tool_stats,
                                       max_rounds=attending_rounds,
                                       carryover=carryover)
        except Exception as e:
            # A network/stream failure this late shouldn't throw away the
            # baseline, the briefs, and any earlier iterations.
            print(f"  iteration {i + 1} failed: {type(e).__name__}: {e}")
            iterations.append({"iteration": i + 1, "outcome": "error",
                               "error": f"{type(e).__name__}: {e}", "rounds": []})
            break

        last_round = diag["rounds"][-1]
        patch = last_round["patch"]

        iter_record = {
            "iteration": i + 1,
            "session_id": diag["session_id"],
            "rounds": diag["rounds"],
            "patch": patch,
        }

        if patch is None:
            print("  no parseable patch after retries — stopping iteration loop")
            iter_record["outcome"] = "unparseable_patch"
            iterations.append(iter_record)
            break

        if patch.get("no_defect_found"):
            print(f"  honest exit: {patch.get('rationale', '')[:200]}")
            iter_record["outcome"] = "no_defect_found"
            iterations.append(iter_record)
            break

        verify = patcher.apply_patch(patch)
        iter_record["patch_verify"] = verify

        if verify.get("tools_refused"):
            print(f"  REFUSED to remove load-bearing tool(s): {verify['tools_refused']}")

        # If the patch touched nothing on disk (a paraphrased `find` string is
        # the common cause), a re-eval measures the unchanged agent. Recording
        # that as "patched" with a score attached would present pure trial
        # noise as a fix, so skip the paid run and feed the error back instead.
        if not verify.get("applied_anything"):
            print(f"  patch changed NOTHING on disk — skipping the re-eval. "
                  f"errors={verify['errors']}")
            iter_record["outcome"] = "patch_did_not_apply"
            iterations.append(iter_record)
            _write_artifact("iterations_partial.json", iterations)
            carryover = _carryover_note(iter_record)
            continue

        try:
            eval_after = bridge.run_eval(trials=args.trials, model=args.model)
        except Exception as e:
            print(f"  post-patch eval failed: {type(e).__name__}: {e}")
            iter_record["outcome"] = "eval_error"
            iter_record["eval_error"] = f"{type(e).__name__}: {e}"
            iterations.append(iter_record)
            _write_artifact("iterations_partial.json", iterations)
            break

        iter_record["eval_after"] = eval_after
        print(f"  after patch: resolved {eval_after['resolved_trials']}/{eval_after['total_trials']} "
              f"(verify={'ok' if verify['verified'] else 'FAILED: ' + str(verify['errors'])})")

        iterations.append(iter_record)
        _write_artifact("iterations_partial.json", iterations)  # checkpoint
        carryover = _carryover_note(iter_record)
        prior_resolved, prior_total = eval_after["resolved_trials"], eval_after["total_trials"]

        if prior_resolved == prior_total:
            print("  fully resolved on train — stopping")
            break

    # Only iterations that actually re-evaluated carry a score. Taking
    # `iterations[-1]` blindly meant an honest `no_defect_found` final round —
    # which carries no eval — silently reset the headline to the baseline and
    # slammed the holdout gate shut on a run that had genuinely improved.
    scored = [it["eval_after"] for it in iterations if "eval_after" in it]
    final_train = scored[-1] if scored else baseline["eval"]
    train_improved = final_train["resolved_trials"] > baseline["eval"]["resolved_trials"]

    final_tool_stats = bridge.tool_manifest_stats()
    report = {
        "baseline": baseline,
        "briefs": briefs,
        "iterations": iterations,
        "final_train": final_train,
        "holdout": None,
        "tool_stats_before": baseline["tool_stats"],
        "tool_stats_after": final_tool_stats,
        "ts": time.time(),
    }

    # Bank everything BEFORE the holdout. The holdout is the longest, most
    # timeout-prone call in the run, and it used to be able to throw away a
    # complete, already-paid-for diagnosis on its way out.
    _write_artifact("full_run_pre_holdout.json", report)

    holdout_result = None
    if holdout:
        if train_improved:
            print("\n=== HOLDOUT (train improved — gate open) ===")
            try:
                raw = bridge.run_eval(trials=args.trials, model=args.model, holdout=True)
                holdout_result = _split_holdout(raw)
                holdout_result["gate"] = "open"
                for line in _holdout_summary(holdout_result):
                    print(f"  {line}")
            except Exception as e:
                print(f"  holdout failed: {type(e).__name__}: {e}")
                holdout_result = {"gate": "open", "error": f"{type(e).__name__}: {e}"}
        else:
            print("\n=== HOLDOUT SKIPPED — train did not improve, gate stays closed ===")
            holdout_result = {"gate": "closed", "reason": "train did not improve over baseline"}

    report["holdout"] = holdout_result
    return report


def _holdout_summary(h):
    """Short, human-readable lines describing a holdout result - including the
    case where it did not produce one.

    Both AUTOPSY.md and the demo scoreboard used to render a failed holdout as
    `f"{h}"`, which prints the entire captured harness stdout: thousands of
    characters of turn-by-turn logging, as a Python dict repr, onto a
    projector. One shared helper so the two cannot drift apart again."""
    if not h:
        return ["Holdout: not run"]
    if "resolved_trials" in h:
        lines = [f"Holdout (T-4490, T-4503 ONLY): resolved "
                 f"{h['resolved_trials']}/{h['total_trials']}"]
        if h.get("train_recheck"):
            tr = ", ".join(f"{k} {v['resolved']}/{v['trials']}"
                           for k, v in h["train_recheck"].items())
            lines.append(f"train re-check that same run: {tr} - kept separate on purpose")
        if h.get("holdout_split_error"):
            lines.append(f"WARNING: {h['holdout_split_error']}")
        if h.get("note"):
            lines.append(f"note: {h['note']}")
        return lines
    if h.get("error"):
        err = [ln.strip() for ln in h["error"].splitlines() if ln.strip()]
        lines = [f"Holdout: gate {h.get('gate', '?')} - DID NOT COMPLETE."]
        if err:
            lines.append(f"failed with: {err[0][:200]}")
            if len(err) > 1:
                lines.append(f"underlying cause: {err[-1][:200]}")
        lines.append("full harness output: artifacts/full_run.json -> holdout.error")
        lines.append("NO holdout score is reported here, because none was produced.")
        return lines
    if h.get("holdout_split_error"):
        # This is the branch where the operator most needs to be told WHY there
        # is no holdout score. It used to render as "gate open - no result" and
        # drop the diagnostic entirely; AUTOPSY.md and the demo scoreboard print
        # only summary[0], so the explanation never reached a human at all.
        # The grand total is NOT repeated here. `holdout_split_error` already
        # states it once, wrapped in the words that make it safe to read; a
        # second bare rendering of the same digits is one projector glance away
        # from being mistaken for the holdout score.
        return ["Holdout: NO SCORE - the per-ticket split could not be computed.",
                f"reason: {h['holdout_split_error']}"]
    return [f"Holdout: gate {h.get('gate', '?')} - {h.get('reason', 'no result')}"]


def _fmt_cost(value):
    """`_parse_scoreboard` raises when RESOLVED is missing but still returns
    None for cost when only the COST line fails to match. Formatting that with
    :.4f raises TypeError AFTER a paid run - and in cmd_record it fires after
    full_run.json is written but before AUTOPSY.md is, so the run is billed and
    the report never appears."""
    return f"${value:.4f}" if isinstance(value, (int, float)) else "cost unparsed"


def _autopsy_lines(report):
    """The AUTOPSY body, as a pure function of the report.

    Pulled out of cmd_record so the interesting case - a run where the holdout
    did NOT complete - can be tested without a real API run. It could not be,
    before, which is why the raw-stdout dump shipped."""
    autopsy_lines = ["# AUTOPSY — AgentRx vs Meridian\n"]
    b = report["baseline"]["eval"]
    ft = report["final_train"]
    autopsy_lines.append(f"- Baseline (train, T-4471): resolved {b['resolved_trials']}/{b['total_trials']}, "
                          f"cost {_fmt_cost(b.get('cost_total'))}\n")
    autopsy_lines.append(f"- Final (train, T-4471): resolved {ft['resolved_trials']}/{ft['total_trials']}\n")
    summary = _holdout_summary(report["holdout"])
    autopsy_lines.append(f"- {summary[0]}\n")
    for extra in summary[1:]:
        autopsy_lines.append(f"  - {extra}\n")
    tb, ta = report["tool_stats_before"], report["tool_stats_after"]
    autopsy_lines.append(f"- Patient tools: {tb['tool_count']} -> {ta['tool_count']}\n")
    autopsy_lines.append(f"- Iterations run: {len(report['iterations'])}\n")
    for it in report["iterations"]:
        outcome = it.get("outcome", "patched")
        autopsy_lines.append(f"  - iteration {it['iteration']}: {outcome}\n")

    return autopsy_lines


def cmd_record(args):
    print("=== RECORD: full run, real API calls, checkpointing to artifacts/ ===")
    # The chat front desk can legitimately ask for a run without the holdout;
    # this used to hardcode True and silently ignore it.
    report = cmd_diagnose(args, holdout=getattr(args, "holdout", True))
    _write_artifact("full_run.json", report)

    autopsy_path = HERE / "AUTOPSY.md"
    autopsy_path.write_text("".join(_autopsy_lines(report)), encoding="utf-8")
    print(f"\nwrote {autopsy_path}")
    return report


def cmd_demo(args):
    if args.live:
        print("=== DEMO --live: re-running the full diagnosis for real ===")
        report = cmd_record(args)
    else:
        report = _read_artifact("full_run.json")
        if report is None:
            raise SystemExit("no recorded run found — run `python agentrx.py record` first")
        print("=== DEMO: replaying artifacts/full_run.json — zero network calls ===\n")

    def pause(s=0.6):
        if not args.live:
            time.sleep(s)

    b = report["baseline"]["eval"]
    print(f"1. Patient (Meridian) baseline: resolved {b['resolved_trials']}/{b['total_trials']} trials"); pause()
    briefs = report["briefs"]
    if briefs:
        r0 = briefs[0]
        print(f"2. Clerk compaction: raw ~{r0['raw_approx_tokens']} tok -> "
              f"brief ~{r0['brief_approx_tokens']} tok "
              f"({100*(1 - r0['brief_approx_tokens']/max(r0['raw_approx_tokens'],1)):.0f}% smaller)"); pause()
    for it in report["iterations"]:
        print(f"3. Iteration {it['iteration']}: outcome={it.get('outcome','patched')}"); pause()
        if "eval_after" in it:
            ea = it["eval_after"]
            print(f"   -> resolved {ea['resolved_trials']}/{ea['total_trials']} after patch")
    ft = report["final_train"]
    print(f"4. Final train score: {ft['resolved_trials']}/{ft['total_trials']}"); pause()
    summary = _holdout_summary(report["holdout"])
    print(f"5. {summary[0]}")
    for extra in summary[1:]:
        print(f"   ({extra})")
    tb, ta = report["tool_stats_before"], report["tool_stats_after"]
    print(f"6. Patient tools: {tb['tool_count']} -> {ta['tool_count']}")


def cmd_selfcheck(args):
    report = _read_artifact("full_run.json")
    if report is None:
        raise SystemExit("no recorded run found — run `python agentrx.py record` first")

    print("=== SELFCHECK — MEASURED ===")
    per_round_chars = []
    for it in report["iterations"]:
        for rnd in it.get("rounds", []):
            per_round_chars.append(rnd["input_chars"])
    print(f"Attending prompt size per round (chars): {per_round_chars}")

    # What this does and does NOT show. `input_chars` is the length of the
    # message we construct each round; the Attending's real context also
    # includes the server-side session history, which this build cannot read
    # from the event types it streams. An earlier version compared first vs
    # last `input_chars` and printed "growing - it commits the context defect
    # it diagnoses", which the data never supported: the loop is single-round
    # by design, and across outer iterations each call opens a FRESH session
    # built from the already-patched (smaller) files, so the number usually
    # goes DOWN. Reporting that as evidence of unbounded growth would have been
    # exactly the confident-unverified-claim failure this section exists to
    # warn about, printed under the heading "MEASURED".
    briefs_chars = sum(len(json.dumps(b.get("brief", {}))) for b in report.get("briefs", []))
    if per_round_chars:
        print(f"  of which Defect Briefs: ~{briefs_chars} chars "
              f"({100 * briefs_chars / max(per_round_chars[0], 1):.0f}% of round 1's prompt)")
        print("  NOTE: the rest is the patient's full system prompt + tool manifest, "
              "pasted uncompacted. The clerk's ~95% compaction is real but it applies "
              "to the TRANSCRIPTS only, not to the Attending's whole input.")
    print("  Per-round growth across a session: NOT MEASURED (see UNVERIFIED #5) — "
          "each outer iteration opens a fresh session, so these numbers are not a curve.")

    no_defect_fired = any(
        it.get("outcome") == "no_defect_found" for it in report["iterations"]
    )
    print(f"Honest-exit (no_defect_found) ever fired: {no_defect_fired}"
          f"{' — UNTESTED PATH, treat with caution' if not no_defect_fired else ''}")

    # Count only iterations that actually applied something, and never default
    # a missing verdict to True — that used to print "True" for a run in which
    # nothing was ever verified because nothing was ever patched.
    applied = [it for it in report["iterations"]
               if (it.get("patch_verify") or {}).get("applied_anything")]
    verified_all = bool(applied) and all(
        (it.get("patch_verify") or {}).get("verified", False) for it in applied
    )
    print(f"Patches that changed the patient on disk: {len(applied)}")
    print(f"All of those re-read-and-verified: "
          f"{verified_all if applied else 'n/a — no patch ever applied'}")
    refused = [t for it in report["iterations"]
               for t in ((it.get("patch_verify") or {}).get("tools_refused") or [])]
    if refused:
        print(f"Load-bearing tool removals refused by the harness: {sorted(set(refused))}")

    tb, ta = report["tool_stats_before"], report["tool_stats_after"]
    print(f"Patient tool count: {tb['tool_count']} -> {ta['tool_count']} (source: coordinator-tools.json, not a claim)")

    print("\nNOTE: Managed Agents-side token/cost usage is not exposed in the event "
          "types this build reads, so Attending/specialist cost-per-diagnosis is "
          "NOT measured here — only reported for the patient side (bridge/runs.jsonl). "
          "Said plainly rather than estimated.")

    print("\n=== SELFCHECK — UNVERIFIED (opinion, no eval behind these) ===")
    print("""\
1. Structural rule risk: max_outer_iters is a hard cap, same family as Meridian's
   own "exactly ONE specialist" rule — a cap that can make a correct diagnosis
   unreachable if the real defect needs more rounds than the cap allows.
2. God-tool smell: inspect-style introspection in this build is done by just
   reading whole files into the prompt rather than a scoped tool with views —
   a context-engineering smell in AgentRx's own design, not fixed here.
3. Overfitting to one patient: every number in this build is tuned on Meridian.
   Generalization to a different broken agent is unmeasured.
4. Taxonomy as a scaffold — PARTLY resolved, and worth stating precisely
   rather than generously. What is true: no SPECIALIST prompt names another
   lane, says how many specialists exist, or implies a defect taxonomy — each
   is handed material and asked what it sees. What is NOT true, and an earlier
   version of this very line claimed it was: the ATTENDING does know its
   roster's three lanes. It has to, in order to route lane-scoped material —
   that is inherent to the coordinator pattern. So: the lanes are scaffolded,
   the defects are discovered. That is a weaker claim than "nobody was told
   anything", and it is the one the code supports.
5. Attending context growth is UNMEASURED. The pitch-friendly line would be
   "it commits the context defect it diagnoses". This build cannot show that:
   its diagnosis loop is single-round, each outer iteration opens a fresh
   session, and Managed Agents session-side token usage is not exposed in the
   event types read here. Measuring it needs a multi-round session and usage
   data. Until then it is a hypothesis, not a finding.
""")


COMMAND_NAMES = ["reset", "baseline", "briefs", "diagnose", "record", "demo", "selfcheck"]


def _add_flags(parser, suppress):
    """The same flags on the top-level parser and on every subparser, so both
    `agentrx.py --trials 5 record` and `agentrx.py record --trials 5` work.
    This module's own docstring used the second form, which used to exit with
    `error: unrecognized arguments`.

    The subparser copies default to SUPPRESS so that an unspecified flag after
    the subcommand does not overwrite a value given before it — the standard
    argparse parent/child default-clobbering trap."""
    d = argparse.SUPPRESS if suppress else None
    parser.add_argument("--model", default=d,
                        help="model id passed through to the harness (patient side)")
    parser.add_argument("--trials", type=int, default=argparse.SUPPRESS if suppress else 3,
                        help="harness trials per eval call (default 3)")
    parser.add_argument("--holdout", action="store_true", default=argparse.SUPPRESS if suppress else False,
                        help="run the code-gated holdout after diagnosis")
    parser.add_argument("--iterations", type=int, default=argparse.SUPPRESS if suppress else 3,
                        help="outer diagnose/patch/eval cap (default 3)")
    parser.add_argument("--live", action="store_true", default=argparse.SUPPRESS if suppress else False,
                        help="demo only: re-run diagnosis for real")


def build_argparser():
    ap = argparse.ArgumentParser(description="AgentRx — diagnose and patch a broken Meridian")
    _add_flags(ap, suppress=False)
    sub = ap.add_subparsers(dest="command", required=True)
    for name in COMMAND_NAMES:
        _add_flags(sub.add_parser(name), suppress=True)
    return ap


COMMANDS = {
    "reset": cmd_reset,
    "baseline": cmd_baseline,
    "briefs": cmd_briefs,
    "diagnose": cmd_diagnose,
    "record": cmd_record,
    "demo": cmd_demo,
    "selfcheck": cmd_selfcheck,
}


if __name__ == "__main__":
    args = build_argparser().parse_args()
    COMMANDS[args.command](args)
