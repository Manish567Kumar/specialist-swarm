"""
replay.py — re-emits a recorded run's swarm activity into the live sidebar.

Why this exists: the console's `diagnose meridian` used to call cmd_record,
which is the full pipeline — baseline eval, clerk, panel, patch, re-eval,
holdout. That is ten to twelve minutes, and worse, most of it is spent inside
two blocking harness subprocesses that publish nothing, so the sidebar shows
dead air for minutes at a stretch. On a projector that reads as "it's hung".

The rest of this build already answered that question: `record` does the real
work once, `demo` replays it with zero network calls. The console was the one
component still ignoring its own principle. This module fixes that.

A replay is labelled as a replay, in the sidebar and in the tool result. It is
not passed off as a live run — the whole build's argument is that a recorded
run honestly labelled beats a live run that might die on conference wifi.
"""
import time

import events

# Demo pace. Fast enough to hold attention, slow enough that a room can
# actually watch three lanes light up in sequence.
STEP_SECONDS = 0.22
PHASE_SECONDS = 0.5

# Recorded event_logs from before this build stored actor names carry event
# TYPES only. Those replay as coordinator-level activity rather than being
# attributed to a lane we did not record — inventing plausible actor names to
# make the sidebar look busier would be fabricating provenance in the one
# panel whose whole job is showing what really happened.
_TYPE_TO_LABEL = {
    "session.thread_created": "spawned",
    "session.thread_status_running": "running",
    "agent.thread_message_received": "reply",
    "agent.thread_message_sent": "delegate",
    "agent.tool_use": "tool",
}


def _emit(label, actor, pace=STEP_SECONDS):
    events.publish("swarm", {"label": label, "actor": actor})
    if pace:
        time.sleep(pace)


def replay_report(report, speed=1.0):
    """Re-emit `report`'s recorded activity into the sidebar at demo pace.

    Returns a dict describing what was replayed, including whether the
    recording carried per-event actor names (older artifacts do not).
    """
    scale = 1.0 / max(speed, 0.05)
    emitted = 0
    faithful = True

    # A persistent mode badge, not just a line that scrolls away. Without it
    # the sidebar header still reads "Live event stream" and the Elapsed tile
    # counts the REPLAY's wall-clock - a measured number a viewer reads as the
    # diagnosis duration, in the one panel whose job is showing what happened.
    events.publish("swarm", {"label": "mode", "actor": "replay"})
    _emit("tool", "REPLAY of a recorded run — no API calls", PHASE_SECONDS * scale)

    baseline = report.get("baseline", {}).get("eval", {})
    _emit("tool", f"baseline: resolved {baseline.get('resolved_trials')}"
                  f"/{baseline.get('total_trials')}", PHASE_SECONDS * scale)

    briefs = report.get("briefs") or []
    if briefs:
        b = briefs[0]
        _emit("tool", f"clerk compaction: ~{b.get('raw_approx_tokens')} tok -> "
                      f"~{b.get('brief_approx_tokens')} tok", PHASE_SECONDS * scale)

    for it in report.get("iterations", []):
        _emit("tool", f"iteration {it.get('iteration')}: attending dispatching panel",
              PHASE_SECONDS * scale)
        for rnd in it.get("rounds", []):
            for entry in rnd.get("event_log", []):
                if isinstance(entry, dict):
                    label, actor = entry.get("label"), entry.get("actor")
                elif isinstance(entry, (list, tuple)) and len(entry) == 2:
                    label, actor = entry
                else:
                    # type-only recording: no actor was captured
                    faithful = False
                    label, actor = _TYPE_TO_LABEL.get(entry), "attending"
                if not label:
                    continue
                _emit(label, actor or "attending", STEP_SECONDS * scale)
                emitted += 1

        verify = it.get("patch_verify") or {}
        _emit("tool", f"patch applied, verified from disk: {bool(verify.get('verified'))}",
              PHASE_SECONDS * scale)
        after = it.get("eval_after") or {}
        if after:
            _emit("tool", f"re-eval: resolved {after.get('resolved_trials')}"
                          f"/{after.get('total_trials')}", PHASE_SECONDS * scale)

    h = report.get("holdout") or {}
    if "resolved_trials" in h:
        _emit("tool", f"holdout (gate open): {h['resolved_trials']}/{h['total_trials']}",
              PHASE_SECONDS * scale)
    elif h.get("error"):
        _emit("tool", "holdout: did not complete (see artifact)", PHASE_SECONDS * scale)
    elif h:
        _emit("tool", f"holdout: gate {h.get('gate', '?')}", PHASE_SECONDS * scale)

    _emit("idle", "session", 0)
    return {
        "mode": "replay",
        "swarm_events_replayed": emitted,
        "actors_faithful": faithful,
        "note": ("replayed from artifacts/full_run.json with zero API calls"
                 if faithful else
                 "replayed from artifacts/full_run.json with zero API calls; this "
                 "recording stored event types without actor names, so lane "
                 "attribution in the sidebar is shown as coordinator-level "
                 "activity rather than guessed"),
    }
