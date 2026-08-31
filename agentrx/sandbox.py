"""
sandbox.py — AgentRx's copy of the agent under diagnosis.

Copies Meridian (Day-1 Ex-04) into a local sandbox and restores the
PRISTINE BROKEN originals for system-prompt-coordinator.txt and
coordinator-tools.json via `git show HEAD:...` from the basecamp-exercises
repo. Never writes back to the user's real, already-fixed exercise files.

`git status` on that repo shows these two files as locally modified
(the user's in-progress fixes) — which means HEAD is still the pristine
broken version Anthropic planted. That's the fact this script leans on,
and `_assert_patient_is_broken` now checks it instead of assuming it.
Without that check, one `git commit -a` in the exercises repo would make
AgentRx quietly diagnose a healthy agent and report a flat score as
though the swarm had failed.
"""
import glob
import os
import shutil
import subprocess
import time

HERE = os.path.dirname(os.path.abspath(__file__))

# Overridable so the test suite (and any parallel experiment) gets its own
# copy instead of yanking the directory out from under a live `record` run.
SANDBOX_DIR = os.path.join(HERE, os.environ.get("AGENTRX_SANDBOX", "sandbox"))

MERIDIAN_SRC = os.path.abspath(os.path.join(
    HERE, "..", "..", "..", "basecamp-exercises", "day1", "04_diagnosing-ai-problems"
))
MERIDIAN_RELPATH = "day1/04_diagnosing-ai-problems"

# Files whose HEAD version is the planted-broken original we want to diagnose,
# not whatever the user has since edited in their working tree.
RESTORE_FROM_HEAD = [
    "system-prompt-coordinator.txt",
    "coordinator-tools.json",
]

# Everything else we just need read-only (harness, subagent prompts/tools,
# tickets/graders baked into the .py, notebook checkpoints excluded).
SKIP = {".ipynb_checkpoints", "Diagnose_Fix_Brief.ipynb"}

# Markers proving the sandbox really does contain the planted-broken agent.
EXPECTED_TOOL_COUNT = 17
BROKEN_PROMPT_MARKERS = [
    "do not spawn more than one specialist",   # the routing defect
    # Must be a phrase the FIX has to remove. This was
    # `resolution_status "resolved"` - which survives the repair verbatim (the
    # fixed prompt reads `resolution_status "resolved" ONLY if ...`), so the
    # guard passed on an already-honest patient while printing a
    # measured-sounding "2/2 planted defects present". The guard exists so that
    # one `git commit -a` in the exercises repo cannot make AgentRx silently
    # diagnose a healthy patient; a marker that outlives the defect cannot do
    # that job.
    "escalations count against the team's resolution SLA",  # the honesty defect
]


# One lock PER SANDBOX, not one globally. A `record` run on `sandbox` must not
# block the test suite, which deliberately works in `sandbox_test` and cannot
# collide with it.
LOCK_PATH = os.path.join(HERE, ".run.%s.lock" % os.path.basename(SANDBOX_DIR))


def _pid_alive(pid):
    try:
        if os.name != "nt":
            # tasklist does not exist off Windows, so this used to raise
            # FileNotFoundError, hit the fail-closed branch below, and report
            # every dead PID as alive - meaning a lock left by a Ctrl+C'd run
            # could NEVER go stale on macOS or Linux, and every later run
            # exited with "another AgentRx run is already using this sandbox".
            try:
                os.kill(pid, 0)
                return True
            except ProcessLookupError:
                return False
            except PermissionError:
                return True          # alive, just not ours to signal
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, check=False,
        )
        return str(pid) in (out.stdout or "")
    except Exception:
        # Fail CLOSED. The old comment here said "can't tell -> don't block
        # the user", which sounds considerate and is the wrong trade: a
        # liveness check that errors would clear a LIVE run's lock, and the
        # next reset() would delete the sandbox out from under it. Blocking a
        # user for a few seconds costs nothing; the alternative cost a paid
        # holdout mid-flight.
        return True


def acquire_run_lock():
    """Claim the sandbox for this process for the duration of a run.

    A diagnosis started from the web console calls sandbox.reset(), which
    rmtree's the sandbox directory. If a `record` run is already using it, that
    deletes the files out from under a live, already-paid-for run. This
    happened once during the build and survived on luck alone."""
    holder = read_run_lock()
    if holder and holder != os.getpid():
        raise SystemExit(
            f"another AgentRx run is already using {SANDBOX_DIR}\n"
            f"  (process {holder} holds {LOCK_PATH})\n"
            "Wait for it to finish, or run against a separate sandbox:\n"
            "  PowerShell:  $env:AGENTRX_SANDBOX='sandbox_b'; python agentrx.py ...\n"
            "  bash/zsh:    AGENTRX_SANDBOX=sandbox_b python agentrx.py ...\n"
            f"If you are certain nothing is running, delete {LOCK_PATH}."
        )
    with open(LOCK_PATH, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    return os.getpid()


def read_run_lock():
    """PID currently holding the lock, or None. A lock left behind by a crashed
    or killed run is stale and gets cleared rather than blocking forever."""
    if not os.path.exists(LOCK_PATH):
        return None
    try:
        with open(LOCK_PATH, "r", encoding="utf-8") as f:
            pid = int((f.read() or "0").strip())
    except (ValueError, OSError):
        pid = 0
    if pid and pid != os.getpid() and not _pid_alive(pid):
        try:
            os.remove(LOCK_PATH)   # stale
        except OSError:
            pass
        return None
    return pid or None


def release_run_lock():
    if read_run_lock() == os.getpid():
        try:
            os.remove(LOCK_PATH)
        except OSError:
            pass


def _git_repo_root(path):
    out = subprocess.run(
        ["git", "-C", path, "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def _git_show_head(repo_root, relpath):
    # Decode explicitly as UTF-8 — on Windows, text=True decodes with the
    # console's locale codec and mangles the em-dashes in these files.
    out = subprocess.run(
        ["git", "-C", repo_root, "show", f"HEAD:{relpath}"],
        capture_output=True, check=True,
    )
    return out.stdout.decode("utf-8")


def _assert_patient_is_broken(verbose=True, root=None):
    """The entire premise is that HEAD still holds the planted-broken agent.
    Check it, loudly, rather than discovering mid-demo that we spent ten
    minutes and real money diagnosing an already-healthy patient."""
    import json

    root = root or SANDBOX_DIR
    prompt_path = os.path.join(root, "system-prompt-coordinator.txt")
    tools_path = os.path.join(root, "coordinator-tools.json")
    with open(prompt_path, "r", encoding="utf-8") as f:
        prompt = f.read()
    with open(tools_path, "r", encoding="utf-8") as f:
        spec = json.load(f)
    tools = spec if isinstance(spec, list) else spec.get("tools", spec)

    missing = [m for m in BROKEN_PROMPT_MARKERS if m not in prompt]
    problems = []
    if missing:
        problems.append(f"planted prompt defect(s) absent from HEAD: {missing}")
    if len(tools) != EXPECTED_TOOL_COUNT:
        problems.append(f"expected {EXPECTED_TOOL_COUNT} tools at HEAD, found {len(tools)}")

    if problems:
        raise SystemExit(
            "The sandbox was NOT built from a broken patient.\n  "
            + "\n  ".join(problems)
            + "\n\nAgentRx restores the patient from `git show HEAD:` in\n  "
            + MERIDIAN_SRC
            + "\nwhich only works while HEAD still holds Anthropic's original\n"
              "broken exercise files. If those fixes have since been committed,\n"
              "point RESTORE_FROM_HEAD at the original commit instead."
        )

    if verbose:
        print(f"  patient verified broken: {len(tools)} tools, "
              f"{len(BROKEN_PROMPT_MARKERS)}/{len(BROKEN_PROMPT_MARKERS)} planted defects present")


def _grader_provenance(repo_root):
    """Show, rather than assert, that the graders weren't touched. The harness
    is copied from the working tree (it carries a local TLS shim), so an
    obvious question is whether its graders were edited too."""
    try:
        out = subprocess.run(
            ["git", "-C", repo_root, "diff", "--stat", "HEAD", "--",
             f"{MERIDIAN_RELPATH}/Diagnose_Fix_Brief.py"],
            capture_output=True, check=False,
        )
        return out.stdout.decode("utf-8", errors="replace").strip() or "(no local changes)"
    except Exception as e:  # provenance is informational, never fatal
        return f"(could not read: {e})"


def reset(verbose=True):
    """(Re)build the sandbox from scratch. Safe to call repeatedly — never
    touches MERIDIAN_SRC, only reads from it and from git HEAD."""
    if not os.path.isdir(MERIDIAN_SRC):
        raise SystemExit(f"Meridian source not found at {MERIDIAN_SRC}")

    holder = read_run_lock()
    if holder and holder != os.getpid():
        raise SystemExit(
            f"refusing to rebuild {SANDBOX_DIR} - process {holder} is running a\n"
            "diagnosis against it right now. Wait for it, or use a separate sandbox:\n"
            "  AGENTRX_SANDBOX=sandbox_b python agentrx.py ..."
        )

    # Build the new sandbox in a staging directory and swap it in at the very
    # end. The old order was rmtree(live) -> copy-into-live, so ANY failure in
    # between - a crash, a PermissionError on a file the harness had open, a
    # killed process - left the live sandbox permanently EMPTY. That is the
    # exact state a running holdout crashed into: it loaded the coordinator
    # prompt fine, then found system-prompt-subagent-billing.txt gone mid-run.
    # Staging cannot fix a concurrent reset (the lock is for that), but it does
    # mean a reset that fails leaves the previous sandbox intact instead of
    # destroying it.
    staging = SANDBOX_DIR + ".staging"
    if os.path.isdir(staging):
        shutil.rmtree(staging)
    os.makedirs(staging)

    for name in os.listdir(MERIDIAN_SRC):
        if name in SKIP:
            continue
        src = os.path.join(MERIDIAN_SRC, name)
        dst = os.path.join(staging, name)
        if os.path.isdir(src):
            continue
        shutil.copy2(src, dst)

    try:
        repo_root = _git_repo_root(MERIDIAN_SRC)
    except subprocess.CalledProcessError as e:
        raise SystemExit(
            f"{MERIDIAN_SRC}\nis not inside a git repository, so the pristine broken\n"
            f"patient cannot be restored from HEAD. git said: {e}"
        )

    restored = []
    for fname in RESTORE_FROM_HEAD:
        relpath = f"{MERIDIAN_RELPATH}/{fname}"
        try:
            pristine = _git_show_head(repo_root, relpath)
        except subprocess.CalledProcessError as e:
            raise SystemExit(
                f"could not read `HEAD:{relpath}` from {repo_root}.\n"
                f"AgentRx needs the original broken exercise file from git history. git said: {e}"
            )
        dst = os.path.join(staging, fname)
        with open(dst, "w", encoding="utf-8", newline="") as f:
            f.write(pristine)
        restored.append(fname)

    # runs.jsonl belongs to the sandbox's own history, not the user's workshop
    # run log — start it clean so AgentRx's cost accounting is self-contained.
    runs_log = os.path.join(staging, "runs.jsonl")
    if os.path.exists(runs_log):
        os.remove(runs_log)

    # Same reasoning for traces. The exercise ships trace-T-4471-*.json from
    # Anthropic's own earlier runs; left in place, the clerk would compact
    # those stale transcripts and the swarm would diagnose evidence from a
    # different run than the one we just measured.
    stale_traces = glob.glob(os.path.join(staging, "trace-*.json"))
    for path in stale_traces:
        os.remove(path)

    # Verify the staged copy BEFORE it becomes the live sandbox, so a patient
    # that isn't actually broken never replaces a good one.
    _assert_patient_is_broken(verbose=verbose, root=staging)

    _swap_into_place(staging, SANDBOX_DIR)

    if verbose:
        print(f"sandbox reset -> {SANDBOX_DIR}")
        print(f"  copied from : {MERIDIAN_SRC}")
        print(f"  restored from HEAD (pristine broken): {', '.join(restored)}")
        print(f"  cleared {len(stale_traces)} stale trace file(s) + runs.jsonl")
        print(f"  grader provenance (Diagnose_Fix_Brief.py vs HEAD): {_grader_provenance(repo_root)}")
    return SANDBOX_DIR


def _swap_into_place(staging, dest, attempts=12, pause=0.25):
    """Replace `dest` with `staging`, tolerating Windows' delayed deletes.

    shutil.rmtree() returns as soon as it has issued the deletes, but NTFS
    keeps a directory in a pending-delete state until every open handle to it
    closes - and on a dev box something is always about to open it: Defender's
    real-time scan, the search indexer, an Explorer window left on the folder.
    The rename onto that name then fails with WinError 5, which is what killed
    a full test run here. Retrying is the documented remedy; the alternative is
    a reset that fails perhaps one time in ten for reasons unrelated to AgentRx.

    Fails loudly rather than half-swapping: if the destination cannot be
    cleared, the previous sandbox stays intact and the staged copy is left on
    disk for inspection."""
    last = None
    for attempt in range(attempts):
        try:
            if os.path.isdir(dest):
                shutil.rmtree(dest)
            os.rename(staging, dest)
            return dest
        except OSError as e:
            last = e
            time.sleep(pause)
    raise SystemExit(
        f"could not replace {dest} after {attempts} attempts ({last}).\n"
        f"Something is holding a handle on it - close any shell or editor sitting\n"
        f"in that directory. The previous sandbox is untouched and the new one is\n"
        f"staged at {staging}.")


def harness_path():
    return os.path.join(SANDBOX_DIR, "Diagnose_Fix_Brief.py")


if __name__ == "__main__":
    reset()
