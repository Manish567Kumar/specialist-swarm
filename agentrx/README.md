# AgentRx — a specialist swarm that diagnoses broken agents

**A working swarm diagnosing a broken swarm.**

AgentRx points a coordinator + three blind specialists at **Meridian**, the Day-1 Exercise-04
support-triage coordinator. It reads Meridian's failing transcripts, discovers defects nobody
named for it, patches them, and verifies on a code-gated holdout.

The defects were planted by Anthropic, in the workshop's own exercise — **not by us.** That
removes the usual critique of a demo like this ("you hid the egg, then found it").

The joke that makes it stick: Meridian is *itself* a broken coordinator-plus-specialists system,
and the headline defect is that its coordinator was **forbidden from spawning more than one
specialist**. The cure and the disease are the same architecture.

---

## What it demonstrates

| Concept | Where it's load-bearing |
|---|---|
| **Managed Agents** | Attending coordinator + 3 specialist agents, real server-side fan-out, streamed events |
| **Skills** | Each specialist carries its own uploaded diagnostic playbook (`skills/*/SKILL.md`) |
| **Sub-agents** | `multiagent: {type: coordinator}` — the Attending delegates, it does not read raw transcripts |
| **Context engineering** | Haiku clerk compacts *transcripts* to Defect Briefs (**95% — 5,812 → 286 approx tokens**, the one train transcript); each specialist sees only its own lane |
| **Evals** | Meridian's own graders. Train → patch → **code-gated** holdout that only opens if train improved |
| **Inference optimization** | Haiku clerk · Sonnet specialists · Opus only for synthesis |

---

## The roster

| Agent | Model | Lane | Skill |
|---|---|---|---|
| **Attending** (coordinator) | Opus | Dispatch, synthesize, rank, patch, gate the holdout | — |
| **Prompt-Logic Specialist** | Sonnet | Rule conflicts, unreachable instructions, hardcoded outcomes | `prompt-logic-playbook` |
| **Tool-Spec Specialist** | Sonnet | Bloat, near-duplicate tools, vague descriptions | `tool-spec-playbook` |
| **Context Specialist** | Sonnet | Stuffing, turn waste, token budget | `context-playbook` |

**What the blindness claim actually is** — stated narrowly, because the generous version of it
was false and a review caught that:

- **Each specialist is genuinely blind.** No specialist prompt names another lane, says how many
  specialists exist, or implies a defect taxonomy. Each is handed material and asked what it sees.
- **The Attending does know its roster's three lanes.** It has to — routing lane-scoped material is
  the whole coordinator pattern, and you cannot delegate to an agent you can't describe. What it is
  *not* told is what defects exist, how many there are, or which lane the real bug lives in.

So: **the lanes are scaffolded, the defects are discovered.** That is weaker than "nobody was told
anything", and it is the claim the code supports.

An earlier version of these prompts ended every specialist with *"someone else is reviewing the
tools, someone else the context budget"* — which hands over the entire roster — while the test that
was supposed to catch it only grepped for the literal phrase `"three families"`, a string nobody
would ever write. Both are fixed, and
`test_no_specialist_is_told_the_taxonomy_size` now tests the leaks that would really occur.

---

## Setup

Follow the fork's root `README.md` first — it covers the Managed Agents beta and the API key.
AgentRx adds nothing to that setup beyond `ANTHROPIC_API_KEY` being present in the environment.

```powershell
# from the repo root, after the root README's setup
cd hackathon\specialist-swarm\agentrx
python specialists.py       # creates the 3 specialist agents, uploads + attaches their playbooks
```

Run `specialists.py`, not `upload_skills.py`: the uploader needs `.specialist_ids.json`, which is
gitignored and is created by `specialists.py`. On a fresh clone the uploader alone just exits.

`upload_skills.py` writes local id caches (`.skill_ids.json`, `.specialist_ids.json`,
`.environment_id`, `.coordinator_id`). All of these are gitignored — they are per-account and
regenerate on demand. **No key or id is committed.**

---

## Running it

```powershell
python agentrx.py reset       # rebuild the sandbox from the PRISTINE broken Meridian
python agentrx.py baseline    # bank the bad number + capture failing transcripts
python agentrx.py briefs      # show the compaction: raw transcripts -> Defect Briefs
python agentrx.py diagnose    # the swarm: fan out, synthesize, patch, verify
python agentrx.py record      # all of the above end-to-end -> artifacts/ + AUTOPSY.md
python agentrx.py demo        # replays artifacts/. ZERO network calls  <- the stage command
python agentrx.py selfcheck   # AgentRx audited against its own taxonomy
```

Flags: `--trials N` · `--iterations N` · `--holdout` · `--model ID` · `--live` (demo only).

A full recorded run:

```powershell
python agentrx.py --trials 3 --iterations 2 --holdout record
```

This makes real API calls and takes several minutes. It **checkpoints every stage** to
`artifacts/`, so a failure late in the run never discards the baseline or the briefs.

`artifacts/` is gitignored — it is one account's billed run, not source. So on a fresh clone
`demo` has nothing to replay and says so; **run `record` once first.** That is the honest ordering
anyway: the replay is only worth watching because the run behind it was real.

### The chatbot + live swarm sidebar

```powershell
python server.py            # http://localhost:8765
```

A front-desk agent you talk to in plain English — *"what agents can you diagnose?"*,
*"diagnose meridian"*. The right-hand sidebar streams the swarm over SSE: spawns, delegations and
replies land as they happen. Pure stdlib `http.server` — no Flask, no FastAPI, no install.

Two honest notes about the sidebar. A **live** run attributes each event to the specialist that
raised it, so the three lanes light independently. A **replay** can only show what the recording
stored — and a recording made before per-event actor names were added replays as coordinator-level
activity rather than three lanes. The replay says so, in the sidebar and in its own result, instead
of inventing lane names to look busier. Re-run `record` to get a recording with lanes.

---

## Where the numbers come from

`AUTOPSY.md` is generated by `record` — nothing in it is typed by hand. **[PROVENANCE.md](PROVENANCE.md)** records what a generated report cannot know about itself: how the holdout was split from the harness's grand total, why it had to be re-run separately, and what the recorded run does *not* support.

---

## Tests

```powershell
python tests.py                 # 63 tests, stdlib unittest, no network
```

Or open **`AgentRx_Tests.ipynb`** in Jupyter and run all cells — same suite plus live cells that
show the sandbox, a real patch being applied and re-verified from disk, and the specialist roster.

The tests run against `AGENTRX_SANDBOX=sandbox_test` (the notebook uses `sandbox_notebook`) so a
test run can never yank the sandbox out from under a live `record` run. That isolation exists
because it bit us once.

---

## Design decisions worth defending

**The sandbox never touches your files.** `sandbox.py` copies the exercise, then restores the
*pristine broken* `system-prompt-coordinator.txt` and `coordinator-tools.json` via
`git show HEAD:` — because your working tree has your own fixes in it. Your completed exercise is
read, never written.

**A failed reset does not silently produce an empty sandbox.** `reset()` builds a staging copy,
checks the patient is genuinely broken *there*, and swaps it in only once the copy is complete —
and if the swap cannot be completed it fails loudly with the staged copy left on disk, rather than
half-applying. The obvious order —
`rmtree` the live directory, then copy into it — has a window where any failure leaves the sandbox
permanently empty. A live holdout crashed into exactly that window mid-run, three trials deep and
already paid for. Relatedly, the run lock's liveness probe now fails **closed**: a check that cannot
tell whether a PID is alive treats it as alive, because the alternative is clearing a live run's lock
and deleting its work.

**Patches are verified, not asserted.** After applying a patch, `patcher.py` re-reads the file
from disk and confirms the change landed; a non-verbatim `find` is reported as an error, not
swallowed. Claiming success without checking is precisely Meridian's hardcoded-`"resolved"` bug,
and it would have been very easy to commit it here.

**The holdout is code-gated.** It only runs if the train score actually improved. The gate is in
code, not in a prompt, so the score cannot be talked into opening it.

**Record/replay.** `record` does all the real work once. `demo` replays it with zero network
calls. A live demo should not depend on a research-preview API and conference wifi both behaving.

**Self-diagnosis is split in two.** `selfcheck` separates **MEASURED** (instrumented, reproducible)
from **UNVERIFIED** (labeled opinion). A self-critique section is the single easiest place in a
build like this to commit the exact sin it warns about — confident unverified claims — so the two
never share a bucket. Per-round context growth sits in the **UNVERIFIED** half, not the measured
one: the loop is single-round and each iteration opens a fresh session, so the unbounded-growth
curve this build would most like to show has never actually been instrumented. Saying that plainly
is the point — the alternative is asserting an unmeasured number inside the honesty section.

---

Built with Claude Code at Anthropic Partner Basecamp, Aug 2026.
