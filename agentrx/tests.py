"""
tests.py — AgentRx's test suite. stdlib unittest only (no pytest to install),
so it runs identically in a terminal and inside a Jupyter cell:

    python tests.py                      # terminal
    import tests; tests.run()            # notebook

Everything here is OFFLINE — no API calls, no Managed Agents, no billing.
The parsing/patching/verification logic is exactly where a demo breaks, so
that's what gets covered. Network paths are exercised by `agentrx.py record`
instead, which is a real run and can't be unit-tested cheaply.
"""
import ast
import io
import json
import os
import inspect
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# Tests get their OWN sandbox. Without this, running the suite while a real
# `record` run is in flight deletes the directory that run is working in.
# Must be set before `import sandbox` reads it.
# Unconditional, NOT setdefault. sandbox.py's own error message tells a
# blocked user to run `AGENTRX_SANDBOX=sandbox_b python agentrx.py ...`; if
# they export that for the session, setdefault would honour it and the suite
# would rmtree the very sandbox they were told to move to.
os.environ["AGENTRX_SANDBOX"] = "sandbox_test"

import agentrx
import attending
import chat_backend
import bridge
import clerk
import events
import patcher
import sandbox
import chat


class TestSandbox(unittest.TestCase):
    """The sandbox must hand the swarm the PRISTINE BROKEN agent, and must
    never touch the user's own already-fixed exercise files."""

    @classmethod
    def setUpClass(cls):
        sandbox.reset(verbose=False)

    def test_sandbox_exists_with_harness(self):
        self.assertTrue(os.path.isdir(sandbox.SANDBOX_DIR))
        self.assertTrue(os.path.exists(sandbox.harness_path()))

    def test_restores_planted_defect_from_git_head(self):
        prompt = Path(sandbox.SANDBOX_DIR, "system-prompt-coordinator.txt").read_text(encoding="utf-8")
        self.assertIn("exactly ONE specialist", prompt,
                      "sandbox must contain the planted one-specialist defect")

    def test_utf8_not_mangled(self):
        # git show through the wrong codec turns em-dashes into mojibake;
        # that corrupts the exact-substring patch matching later.
        prompt = Path(sandbox.SANDBOX_DIR, "system-prompt-coordinator.txt").read_text(encoding="utf-8")
        self.assertNotIn("â€”", prompt, "em-dash was mangled — decoding regression")

    def test_does_not_mutate_users_real_exercise_files(self):
        """Assert the sandbox is a SEPARATE copy, not that the user's file has
        any particular content.

        This used to assert the working-tree file did not contain "exactly ONE
        specialist" - which passes on this machine only because that checkout
        is locally modified. On a fresh public clone the working tree equals
        HEAD, the string is present, and the suite goes red for a reason that
        has nothing to do with AgentRx."""
        real = Path(sandbox.MERIDIAN_SRC, "system-prompt-coordinator.txt")
        copy = Path(sandbox.SANDBOX_DIR, "system-prompt-coordinator.txt")
        self.assertNotEqual(real.resolve(), copy.resolve(),
                            "the sandbox must not be the user's own file")
        self.assertTrue(real.exists(), "the user's original must still be there")

    def test_runs_log_starts_clean(self):
        # AgentRx's cost accounting must not inherit the user's workshop history.
        self.assertFalse(os.path.exists(os.path.join(sandbox.SANDBOX_DIR, "runs.jsonl")))


class TestBridge(unittest.TestCase):
    def test_tool_manifest_stats_counts_the_bloat(self):
        stats = bridge.tool_manifest_stats()
        self.assertEqual(stats["tool_count"], 17,
                         "the broken target agent ships 17 tools — that's the context-defect baseline")
        self.assertIn("get_customer", stats["tool_names"])
        self.assertIn("helper", stats["tool_names"])
        self.assertGreater(stats["approx_tokens"], 0)

    def test_scoreboard_parser_reads_resolved_and_cost(self):
        sample = (
            "  MERIDIAN SCOREBOARD   ·   claude-sonnet-5   ·   3 trials/ticket\n"
            "  RESOLVED  2/3 trials   <-- your headline number\n"
            "  COST      $0.4137 total   ·   $0.1379/trial\n"
        )
        parsed = bridge._parse_scoreboard(sample)
        self.assertEqual(parsed["resolved_trials"], 2)
        self.assertEqual(parsed["total_trials"], 3)
        self.assertAlmostEqual(parsed["cost_total"], 0.4137)
        self.assertAlmostEqual(parsed["cost_per_trial"], 0.1379)

    def test_scoreboard_parser_fails_loudly_on_garbage(self):
        # It used to return None for every field, which surfaced several frames
        # later as a TypeError inside a print statement - after the eval had
        # already been run and paid for. Fail at the parse, with the evidence.
        with self.assertRaises(RuntimeError) as ctx:
            bridge._parse_scoreboard("no scoreboard here")
        self.assertIn("no scoreboard here", str(ctx.exception),
                      "the error must carry the harness output that confused it")

    def test_holdout_ticket_split_is_defined(self):
        # The holdout number is only honest if it excludes the train ticket.
        self.assertNotIn("T-4471", bridge.HOLDOUT_TICKETS)
        self.assertIn("T-4471", bridge.TRAIN_TICKETS)
        self.assertEqual(sorted(bridge.HOLDOUT_TICKETS), ["T-4490", "T-4503"])


class TestClerkParsing(unittest.TestCase):
    """The clerk's output is model-generated JSON — the single most likely
    thing to arrive malformed mid-demo."""

    def test_plain_json(self):
        self.assertEqual(clerk._parse_brief_json('{"a": 1}'), {"a": 1})

    def test_fenced_json(self):
        self.assertEqual(clerk._parse_brief_json('```json\n{"a": 1}\n```'), {"a": 1})

    def test_fenced_without_language(self):
        self.assertEqual(clerk._parse_brief_json('```\n{"a": 1}\n```'), {"a": 1})

    def test_truncated_json_raises(self):
        with self.assertRaises(json.JSONDecodeError):
            clerk._parse_brief_json('{"a": "unterminated')


class TestPatchBlockExtraction(unittest.TestCase):
    """attending._extract_patch pulls the machine-readable patch out of the
    Attending's prose. Greedy regex bugs here silently break every run."""

    def test_extracts_well_formed_block(self):
        text = (
            "Here is my synthesis.\n\n"
            "```agentrx-patch\n"
            '{"no_defect_found": false, "system_prompt_find": "x", '
            '"system_prompt_replace": "y", "remove_tools": ["helper"], "rationale": "r"}\n'
            "```\n"
        )
        patch = attending._extract_patch(text)
        self.assertFalse(patch["no_defect_found"])
        self.assertEqual(patch["remove_tools"], ["helper"])

    def test_returns_none_when_absent(self):
        self.assertIsNone(attending._extract_patch("I could not find the format."))

    def test_handles_honest_exit_block(self):
        text = '```agentrx-patch\n{"no_defect_found": true, "rationale": "all lanes clean"}\n```'
        patch = attending._extract_patch(text)
        self.assertTrue(patch["no_defect_found"])

    def test_nested_braces_in_strings(self):
        text = ('```agentrx-patch\n'
                '{"no_defect_found": false, "rationale": "the rule said {x} and {y}", '
                '"remove_tools": []}\n```')
        patch = attending._extract_patch(text)
        self.assertIn("{x}", patch["rationale"])


class TestPatcher(unittest.TestCase):
    """A patch that reports success without landing on disk is exactly the
    defect AgentRx exists to find. These tests are the anti-hypocrisy check."""

    def setUp(self):
        sandbox.reset(verbose=False)

    def test_removes_tools_and_verifies(self):
        before = bridge.tool_manifest_stats()["tool_count"]
        report = patcher.apply_patch({
            "system_prompt_find": None, "system_prompt_replace": None,
            "remove_tools": ["helper", "process", "get_data"],
            "rationale": "vague duplicates",
        })
        after = bridge.tool_manifest_stats()["tool_count"]
        self.assertEqual(after, before - 3)
        self.assertTrue(report["verified"])
        self.assertCountEqual(report["tools_removed"], ["helper", "process", "get_data"])

    def test_prompt_replacement_lands_on_disk(self):
        target = "Each ticket is owned by exactly ONE specialist."
        report = patcher.apply_patch({
            "system_prompt_find": target,
            "system_prompt_replace": "Spawn every specialist the ticket needs.",
            "remove_tools": [], "rationale": "routing defect",
        })
        self.assertTrue(report["prompt_changed"])
        self.assertTrue(report["verified"])
        prompt = Path(sandbox.SANDBOX_DIR, "system-prompt-coordinator.txt").read_text(encoding="utf-8")
        self.assertNotIn(target, prompt)
        self.assertIn("Spawn every specialist the ticket needs.", prompt)

    def test_non_verbatim_find_is_reported_not_silently_ignored(self):
        report = patcher.apply_patch({
            "system_prompt_find": "this text does not appear anywhere in the prompt",
            "system_prompt_replace": "whatever",
            "remove_tools": [], "rationale": "paraphrased instead of quoted",
        })
        self.assertFalse(report["verified"])
        self.assertTrue(report["errors"], "a non-matching find must surface an error")
        self.assertFalse(report["prompt_changed"])

    def test_removing_unknown_tool_reports_nothing_removed(self):
        report = patcher.apply_patch({
            "system_prompt_find": None, "system_prompt_replace": None,
            "remove_tools": ["no_such_tool"], "rationale": "x",
        })
        self.assertEqual(report["tools_removed"], [])


class TestProtectedTools(unittest.TestCase):
    """Removing escalate_to_human would make holdout T-4503's
    pentest_escalated grader unpassable forever - and the broken agent never
    escalates, so a specialist hunting never-called tools is actively steered
    toward proposing exactly that."""

    def setUp(self):
        sandbox.reset(verbose=False)

    def test_load_bearing_tools_cannot_be_removed(self):
        before = bridge.tool_manifest_stats()["tool_count"]
        report = patcher.apply_patch({"remove_tools": ["escalate_to_human", "helper"]})
        self.assertIn("escalate_to_human", report["tools_refused"])
        self.assertIn("helper", report["tools_removed"])
        stats = bridge.tool_manifest_stats()
        self.assertIn("escalate_to_human", stats["tool_names"])
        self.assertEqual(stats["tool_count"], before - 1)

    def test_half_filled_patch_does_not_delete_the_rule(self):
        # find set + replace null used to silently delete the matched text.
        prompt_path = os.path.join(sandbox.SANDBOX_DIR, "system-prompt-coordinator.txt")
        before = io.open(prompt_path, encoding="utf-8").read()
        report = patcher.apply_patch(
            {"system_prompt_find": "do not spawn more than one specialist",
             "system_prompt_replace": None})
        after = io.open(prompt_path, encoding="utf-8").read()
        self.assertEqual(before, after, "a null replacement must not delete the rule")
        self.assertFalse(report["verified"])
        self.assertTrue(report["errors"])

    def test_patch_that_extends_a_rule_still_verifies(self):
        # The old check was `find not in reread`, which reported FAILED
        # whenever the replacement CONTAINED the original - the single most
        # likely rewrite shape (appending a qualifier to a rule).
        find = "do not spawn more than one specialist"
        report = patcher.apply_patch({
            "system_prompt_find": find,
            "system_prompt_replace": find + " unless the ticket raises two categories",
        })
        self.assertTrue(report["verified"], report["errors"])
        self.assertTrue(report["applied_anything"])


class TestEventBus(unittest.TestCase):
    """The sidebar's credibility rests on it showing real events."""

    def test_subscriber_receives_published_event(self):
        q = events.subscribe()
        try:
            events.publish("swarm", {"label": "spawned", "actor": "Prompt-Logic Specialist"})
            item = q.get(timeout=2)
            self.assertEqual(item["channel"], "swarm")
            self.assertEqual(item["data"]["actor"], "Prompt-Logic Specialist")
            self.assertIn("ts", item)
        finally:
            events.unsubscribe(q)

    def test_unsubscribed_queue_stops_receiving(self):
        q = events.subscribe()
        events.unsubscribe(q)
        events.publish("swarm", {"label": "reply", "actor": "Context Specialist"})
        self.assertTrue(q.empty())

    def test_multiple_subscribers_all_get_it(self):
        q1, q2 = events.subscribe(), events.subscribe()
        try:
            events.publish("chat", {"text": "hello"})
            self.assertEqual(q1.get(timeout=2)["data"]["text"], "hello")
            self.assertEqual(q2.get(timeout=2)["data"]["text"], "hello")
        finally:
            events.unsubscribe(q1)
            events.unsubscribe(q2)


class TestChatFrontDesk(unittest.TestCase):
    def test_every_declared_tool_has_an_implementation(self):
        declared = {t["name"] for t in chat.TOOLS}
        implemented = set(chat.DISPATCH)
        self.assertEqual(declared, implemented,
                         "a declared tool with no dispatch entry fails at runtime, on stage")

    def test_tool_enums_match_the_registry(self):
        for tool in chat.TOOLS:
            props = tool["input_schema"].get("properties", {})
            if "agent_id" in props:
                self.assertEqual(set(props["agent_id"]["enum"]), set(chat.AGENTS))

    def test_list_agents_returns_registry(self):
        result = chat.DISPATCH["list_agents"]({})
        self.assertIn("meridian", result)

    def test_unknown_agent_is_refused_not_faked(self):
        result = chat.DISPATCH["run_diagnosis"]({"agent_id": "nonexistent"})
        self.assertIn("error", result)

    def test_no_patient_wording_leaks_to_users(self):
        # The word survives only in the instruction telling the model NOT to
        # use it; it must not appear in any user-visible description.
        for aid, meta in chat.AGENTS.items():
            self.assertNotIn("patient", meta["description"].lower())
        for tool in chat.TOOLS:
            self.assertNotIn("patient", tool["description"].lower())


class TestSpecialistDesign(unittest.TestCase):
    """The 'blind specialists' claim is architectural — assert it in code so
    a future prompt edit can't quietly break the story."""

    def test_three_specialists_with_distinct_lanes(self):
        import specialists
        keys = [s["key"] for s in specialists.SPECIALISTS]
        self.assertEqual(sorted(keys), ["context", "prompt_logic", "tool_spec"])

    def test_no_specialist_is_told_the_taxonomy_size(self):
        """The 'blind specialists' claim, asserted properly.

        The previous version of this test only looked for the literal string
        "three families" - a phrase nobody would ever actually write - so it
        passed for weeks while every single specialist prompt ended with
        "someone else is reviewing the tools, someone else the context budget",
        which hands over the entire roster. A test that can only fail on a
        phrasing no one uses is not a test. These are the leaks that would
        really occur."""
        import specialists
        leaks = [
            # how many of us there are
            "three specialists", "3 specialists", "several specialists",
            "one of several", "the three", "all three",
            # that anyone else is looking at anything
            # ("each other" is deliberately NOT here: "rules that conflict with
            #  each other" is about the patient's rules, not about the panel.)
            "someone else", "another specialist", "other specialists",
            "the other two", "other lane", "other panel",
            # that a defect taxonomy exists at all
            "taxonomy", "defect families", "three families",
        ]
        for spec in specialists.SPECIALISTS:
            text = spec["system"].lower()
            for leak in leaks:
                self.assertNotIn(leak, text,
                                 f"{spec['key']} leaks {leak!r} - it must stay blind")

        # The SAME list, over the attached playbooks. This test used to read
        # only the system prompts, so the leak simply moved: every SKILL.md
        # ended with a "What is NOT a X defect" section naming the other two
        # lanes outright ("that's Tool-Spec's lane"). README calls the playbook
        # part of what each specialist carries, so it is part of the claim.
        for skill in (HERE / "skills").glob("*/SKILL.md"):
            text = skill.read_text(encoding="utf-8").lower()
            for leak in leaks + ["prompt-logic's lane", "tool-spec's lane",
                                 "context's lane"]:
                self.assertNotIn(leak, text,
                                 f"{skill.parent.name} leaks {leak!r}")

    def test_no_specialist_is_handed_this_patient_s_actual_defects(self):
        """The blindness claim is not only about the roster. The playbooks and
        prompts used to give THIS patient's planted defects as their worked
        examples - `helper, process, get_data, validate` are four real entries
        in its tool manifest, "five separate look up the customer tools" is
        exactly what it has, and "assign exactly one specialist" / "mark the
        ticket resolved" are two of its three planted defects almost verbatim.
        A specialist told the answer is not discovering it, and README stakes
        the project on the "you hid the egg, then found it" rebuttal."""
        import specialists
        planted = [
            "exactly one specialist", "mark the ticket resolved",
            "resolution_status", "sla",
            "get_data", "tool_3_v2", "fetch_customer",
            'five separate "look up the customer"',
        ]
        sources = {s["key"]: s["system"] for s in specialists.SPECIALISTS}
        for skill in (HERE / "skills").glob("*/SKILL.md"):
            sources[skill.parent.name] = skill.read_text(encoding="utf-8")
        for name, text in sources.items():
            low = text.lower()
            for phrase in planted:
                self.assertNotIn(phrase, low,
                                 f"{name} names the patient's own defect: {phrase!r}")

    def test_prompt_push_is_not_gated_on_a_hand_bumped_version(self):
        """Editing a prompt here changes nothing about the agents that actually
        run unless the new text is pushed to the already-created server-side
        agents. That push used to be skipped unless PROMPT_VERSION was bumped
        by hand - which reproduces B1's exact symptom for anyone who edits a
        prompt and forgets. push_prompts() already no-ops per agent when the
        stored text matches, so the gate bought three cheap GETs and cost the
        guarantee."""
        import inspect
        import specialists
        src = inspect.getsource(specialists.ensure_specialists)
        self.assertIn("push_prompts(ids)", src)
        self.assertNotIn("if stamped != PROMPT_VERSION", src)

    def test_selfcheck_states_the_narrow_blindness_claim(self):
        """The Attending DOES know its roster - it must, to route lane-scoped
        material. selfcheck used to claim the opposite as a resolved design
        win. A false statement inside the honesty section is worse than no
        honesty section, so pin the corrected wording down."""
        import inspect
        import agentrx
        src = inspect.getsource(agentrx.cmd_selfcheck)
        self.assertIn("the lanes are scaffolded", src.lower())
        self.assertNotIn("no specialist's\n   prompt mentions the three-family taxonomy", src)

    def test_attending_is_not_given_the_taxonomy(self):
        text = attending.COORDINATOR_SYSTEM.lower()
        self.assertNotIn("three defect families", text)
        self.assertIn("you do not know in advance", text,
                      "the Attending must be told to discover, not to assume")

    def test_attending_stream_timeout_is_generous(self):
        # A default read timeout killed a full run mid-diagnosis once already.
        self.assertGreaterEqual(attending.STREAM_TIMEOUT_SECONDS, 600)


class TestEventReplay(unittest.TestCase):
    """A tab refresh drops the EventSource for ~3s. Anything published in that
    gap used to be lost forever - including the chat reply, which left the UI
    stuck on typing dots with no way to recover."""

    def test_events_are_replayable_after_a_gap(self):
        events.reset()
        first = events.publish("swarm", {"label": "spawned", "actor": "a"})
        second = events.publish("chat", {"text": "the reply nobody was listening for"})
        missed = events.recent(after_id=first)
        self.assertEqual([m["id"] for m in missed], [second])
        self.assertEqual(missed[0]["data"]["text"], "the reply nobody was listening for")

    def test_recent_from_zero_returns_everything_buffered(self):
        events.reset()
        events.publish("swarm", {"label": "spawned", "actor": "a"})
        events.publish("swarm", {"label": "reply", "actor": "b"})
        self.assertEqual(len(events.recent(0)), 2)


class TestSandboxDurability(unittest.TestCase):
    """A live `record` run had its sandbox emptied mid-holdout: the harness
    loaded the coordinator prompt, spawned two specialists fine, then found
    system-prompt-subagent-billing.txt gone on the third trial. Two separate
    weaknesses made that possible and both are covered here."""

    def test_pid_liveness_check_fails_closed(self):
        """`_pid_alive` used to return False when it could not tell - which
        means an errored liveness probe CLEARS a live run's lock and the next
        reset() deletes its sandbox. Uncertainty must mean 'assume alive'."""
        import subprocess as sp
        real = sp.run

        def boom(*a, **kw):
            raise OSError("tasklist unavailable")

        sp.run = boom
        try:
            self.assertTrue(sandbox._pid_alive(999999),
                            "an unknown-liveness PID must be treated as ALIVE")
        finally:
            sp.run = real

    def test_a_failed_reset_leaves_the_previous_sandbox_intact(self):
        """reset() used to rmtree the live directory and THEN copy into it, so
        any failure in between left it permanently empty. It now builds a
        staging copy and swaps only once that copy is complete."""
        sandbox.reset(verbose=False)
        before = sorted(os.listdir(sandbox.SANDBOX_DIR))
        self.assertIn("system-prompt-subagent-billing.txt", before)

        real = sandbox._git_show_head
        sandbox._git_show_head = lambda *a, **kw: (_ for _ in ()).throw(
            RuntimeError("git exploded mid-reset"))
        try:
            with self.assertRaises(RuntimeError):
                sandbox.reset(verbose=False)
        finally:
            sandbox._git_show_head = real

        after = sorted(os.listdir(sandbox.SANDBOX_DIR))
        self.assertEqual(before, after,
                         "a reset that fails must not destroy the working sandbox")

    def test_the_subagent_prompts_the_harness_needs_are_present(self):
        """The crash was a missing subagent prompt. Nothing asserted these
        were copied, so their absence could only surface as a paid run dying
        several minutes in."""
        sandbox.reset(verbose=False)
        for role in ["account", "billing", "technical"]:
            self.assertTrue(
                os.path.exists(os.path.join(
                    sandbox.SANDBOX_DIR, f"system-prompt-subagent-{role}.txt")),
                f"the harness cannot spawn the {role} specialist without its prompt")
            self.assertTrue(
                os.path.exists(os.path.join(
                    sandbox.SANDBOX_DIR, f"subagent-{role}-tools.json")))


class TestAutopsyReporting(unittest.TestCase):

    def _report(self, holdout):
        return {
            "baseline": {"eval": {"resolved_trials": 0, "total_trials": 3, "cost_total": 0.379}},
            "final_train": {"resolved_trials": 3, "total_trials": 3},
            "holdout": holdout,
            "tool_stats_before": {"tool_count": 17},
            "tool_stats_after": {"tool_count": 17},
            "iterations": [{"iteration": 1, "outcome": "patched"}],
        }

    def test_a_failed_holdout_reports_no_score_and_no_stdout_dump(self):
        """AUTOPSY.md used to interpolate the whole holdout dict, pasting
        thousands of characters of raw harness stdout into a single bullet -
        on precisely the run you most need to read. It must summarise, and it
        must not invent a score it never obtained."""
        noise = "\n".join(f"  [T-4490 trial 1] coordinator turn {i}/16..." for i in range(200))
        report = self._report({
            "gate": "open",
            "error": f"RuntimeError: harness failed (rc=1):\n{noise}\nFileNotFoundError: prompt missing",
        })
        text = "".join(agentrx._autopsy_lines(report))
        self.assertNotIn("coordinator turn 150/16", text, "raw stdout must not be dumped")
        self.assertIn("DID NOT COMPLETE", text)
        self.assertIn("FileNotFoundError: prompt missing", text)
        self.assertIn("NO holdout score", text)
        self.assertLess(len(text), 1500, "the autopsy must stay readable")

    def test_the_demo_scoreboard_uses_the_same_summary(self):
        """The dump existed in TWO places and the second one is the command
        that runs on a projector. One helper, so a fix to one is a fix to
        both."""
        h = {"gate": "open", "error": "RuntimeError: boom\n" + "noise\n" * 500}
        lines = agentrx._holdout_summary(h)
        self.assertTrue(all(len(ln) < 260 for ln in lines), lines)
        self.assertIn("DID NOT COMPLETE", lines[0])
        import inspect
        self.assertIn("_holdout_summary", inspect.getsource(agentrx.cmd_demo))

    def test_a_real_holdout_score_is_still_reported(self):
        report = self._report({"gate": "open", "resolved_trials": 6, "total_trials": 6})
        text = "".join(agentrx._autopsy_lines(report))
        self.assertIn("resolved 6/6", text)


class TestConsoleReplay(unittest.TestCase):
    """`diagnose meridian` in the console used to call cmd_record: ten to
    twelve minutes, most of it inside blocking harness subprocesses that
    publish nothing, so the sidebar showed dead air on a projector. The rest
    of the build already solved this - record once, replay for the demo - and
    the console was the one component ignoring its own principle."""

    def _report(self):
        return {
            "baseline": {"eval": {"resolved_trials": 0, "total_trials": 3}},
            "final_train": {"resolved_trials": 3, "total_trials": 3},
            "holdout": {"gate": "open", "resolved_trials": 6, "total_trials": 6},
            "tool_stats_before": {"tool_count": 17},
            "tool_stats_after": {"tool_count": 17},
            "briefs": [{"raw_approx_tokens": 5812, "brief_approx_tokens": 286}],
            "iterations": [{
                "iteration": 1,
                "patch": {"rationale": "r"},
                "patch_verify": {"verified": True},
                "eval_after": {"resolved_trials": 3, "total_trials": 3},
                "rounds": [{"event_log": [
                    {"type": "session.thread_created", "label": "spawned",
                     "actor": "Prompt-Logic Specialist"},
                    {"type": "agent.thread_message_received", "label": "reply",
                     "actor": "Prompt-Logic Specialist"},
                    {"type": "span.model_request_start"},
                ]}],
            }],
        }

    def setUp(self):
        import replay
        self.replay = replay
        self._pace = (replay.STEP_SECONDS, replay.PHASE_SECONDS)
        replay.STEP_SECONDS = replay.PHASE_SECONDS = 0   # don't sleep in tests
        events.reset()

    def tearDown(self):
        self.replay.STEP_SECONDS, self.replay.PHASE_SECONDS = self._pace

    def test_replay_emits_the_recorded_actors_not_invented_ones(self):
        meta = self.replay.replay_report(self._report())
        actors = [e["data"]["actor"] for e in events.recent(0)]
        self.assertIn("Prompt-Logic Specialist", actors)
        self.assertTrue(meta["actors_faithful"])
        self.assertEqual(meta["swarm_events_replayed"], 2,
                         "events with no actor-bearing label must be skipped")

    def test_a_type_only_recording_is_not_dressed_up_with_fake_lanes(self):
        """Older artifacts stored bare event-type strings. Guessing plausible
        specialist names to make the sidebar look busier would fabricate
        provenance in the one panel whose job is showing what really
        happened."""
        report = self._report()
        report["iterations"][0]["rounds"][0]["event_log"] = [
            "session.thread_created", "agent.thread_message_received"]
        meta = self.replay.replay_report(report)
        actors = {e["data"]["actor"] for e in events.recent(0)}
        self.assertFalse(meta["actors_faithful"])
        self.assertNotIn("Prompt-Logic Specialist", actors)
        self.assertIn("attention", meta["note"].replace("attribution", "attention"))

    def test_replay_announces_itself_as_a_replay(self):
        """The FIRST event must be the persistent mode badge, not the scrolling
        banner. A one-off line scrolls out of the stream within seconds, after
        which the sidebar header still reads "Live event stream" and the
        Elapsed tile keeps counting - a measured number a viewer reads as the
        diagnosis duration."""
        self.replay.replay_report(self._report())
        emitted = events.recent(0)
        self.assertEqual(emitted[0]["data"], {"label": "mode", "actor": "replay"})
        self.assertTrue(any("REPLAY" in (e["data"].get("actor") or "")
                            for e in emitted), "banner line missing")

    def test_replay_ends_idle_so_the_sidebar_clock_stops(self):
        self.replay.replay_report(self._report())
        self.assertEqual(events.recent(0)[-1]["data"]["label"], "idle")

    def test_the_front_desk_prefers_replay_over_a_live_run(self):
        import chat
        self.assertIn("replay_diagnosis", chat.DISPATCH)
        names = [t["name"] for t in chat.TOOLS]
        self.assertIn("replay_diagnosis", names)
        self.assertIn("prefer it unless the user explicitly",
                      next(t for t in chat.TOOLS if t["name"] == "replay_diagnosis")["description"])
        self.assertIn("call replay_diagnosis", chat.SYSTEM_PROMPT)

    def test_attending_records_actor_names_for_future_replays(self):
        import inspect
        import attending
        src = inspect.getsource(attending)
        self.assertIn('event_log.append({"type": t})', src)
        self.assertIn('event_log[-1].update({"label": label, "actor": actor})', src)


class TestReviewRegressions(unittest.TestCase):
    """Each test here pins a defect the Opus review round actually found."""

    def test_prompt_push_is_not_skipped_once_the_coordinator_is_cached(self):
        """B1. push_prompts() runs only from ensure_specialists(), and that call
        used to sit BELOW ensure_coordinator's cached-id early return - so on
        every run after the first it never executed. Editing a specialist prompt
        changed the local file and nothing else while the server-side agents
        kept running their original text. That silently invalidated the central
        claim of this build: the recorded specialists were still carrying the
        roster leak the prompts had supposedly been fixed to remove."""
        src = inspect.getsource(attending.ensure_coordinator)
        push = src.index("ensure_specialists()")
        early_return = src.index("COORDINATOR_ID_PATH.exists()")
        self.assertLess(push, early_return,
                        "ensure_specialists() must run BEFORE the cached-id early return")

    def test_a_failed_holdout_split_never_reports_a_contaminated_score(self):
        """B3. On a split failure the grand total used to be returned under
        `resolved_trials` untouched. Every consumer keys on that name, so
        AUTOPSY.md, the demo scoreboard and the front-desk model's context all
        read "Holdout (T-4490, T-4503 ONLY): 9/9" - an impossible denominator
        for two tickets at three trials, and exactly what _split_holdout's own
        docstring says must never be printed."""
        real = agentrx.bridge.last_run_tickets
        agentrx.bridge.last_run_tickets = lambda: ["T-4471"]  # holdout rows absent
        try:
            out = agentrx._split_holdout({"resolved_trials": 9, "total_trials": 9})
        finally:
            agentrx.bridge.last_run_tickets = real
        self.assertNotIn("resolved_trials", out)
        self.assertNotIn("total_trials", out)
        self.assertEqual(out["mixed_resolved_trials"], 9)
        self.assertIn("NOT the holdout number", out["holdout_split_error"])

    def test_the_holdout_summary_of_a_failed_split_shows_no_score(self):
        out = agentrx._holdout_summary(
            {"gate": "open", "mixed_resolved_trials": 9, "mixed_total_trials": 9,
             "holdout_split_error": "the harness grand total across train+holdout "
                                    "was 9/9, which is NOT the holdout number"})
        self.assertIn("NO SCORE", out[0])
        # The digits may legitimately appear inside the reason string - what
        # must never happen is them appearing WITHOUT the disclaimer that makes
        # them safe to read.
        for line in out:
            if "9/9" in line:
                self.assertIn("NOT the holdout number", line)

    def test_systemexit_from_a_tool_is_reported_not_swallowed(self):
        """B2. The tools raise SystemExit constantly - every sandbox guard, the
        run lock, the harness runner - and SystemExit is a BaseException, so
        `except Exception` missed it. It killed the worker thread silently:
        no traceback, no chat event, the browser left on typing dots with a
        dead send button. The likeliest trigger was also the most public one:
        clicking Diagnose while a terminal run holds the lock."""
        for mod, fn in [("chat_backend.py", "the tool dispatch"),
                        ("server.py", "the request handler")]:
            src = io.open(HERE / mod, encoding="utf-8").read()
            self.assertIn("(Exception, SystemExit)", src,
                          f"{fn} in {mod} must catch SystemExit too")

    def test_a_failed_turn_does_not_poison_every_later_turn(self):
        """B2 (cont). _messages is module-global and survives the exception, so
        an assistant turn left carrying a tool_use with no matching tool_result
        made EVERY later request 400 - the console stayed broken for the life of
        the process, even after a page reload."""
        src = inspect.getsource(chat_backend.ask)
        self.assertIn("_rollback_to = len(_messages)", src)
        self.assertIn("del _messages[_rollback_to:]", src)

    def test_the_front_desk_is_not_handed_the_raw_harness_stdout(self):
        """The comment claimed the payload was trimmed; it was not. Each of the
        three eval blocks carried the harness's full captured stdout - 14.9KB of
        a 22.3KB tool result on the recorded run - pasted into the context
        window of a build whose entire thesis is context engineering."""
        block = {"resolved_trials": 3, "total_trials": 3, "cost_total": 0.1,
                 "raw_stdout": "x" * 15000}
        out = chat._scoreboard(block)
        self.assertNotIn("raw_stdout", out)
        self.assertEqual(out["resolved_trials"], 3)

    def test_a_truncated_artifact_fails_with_a_readable_message(self):
        """A Ctrl+C during the final write left a half-written full_run.json,
        and `demo` then died on a raw JSONDecodeError two lines below the
        friendly message that handles the file simply being absent."""
        agentrx.ARTIFACTS_DIR.mkdir(exist_ok=True)
        path = agentrx.ARTIFACTS_DIR / "truncated_test.json"
        path.write_text('{"baseline": {"eval"', encoding="utf-8")
        try:
            with self.assertRaises(SystemExit) as ctx:
                agentrx._read_artifact("truncated_test.json")
            self.assertIn("unreadable", str(ctx.exception))
        finally:
            path.unlink()

    def test_artifact_writes_are_atomic(self):
        """Writing in place gave away the guarantee the checkpoints exist for:
        a late failure could not cost the earlier stages, until the last write
        landed on top of the good full_run.json."""
        src = inspect.getsource(agentrx._write_artifact)
        self.assertIn("os.replace", src)
        self.assertIn(".tmp", src)

    def test_the_suite_cannot_be_pointed_at_a_real_sandbox(self):
        """sandbox.py's own error message tells a blocked user to run
        `AGENTRX_SANDBOX=sandbox_b ...`. With setdefault, exporting that for the
        session made this suite rmtree the very sandbox they moved to."""
        # Walk the AST rather than grepping: this test's own assertion text
        # would otherwise match itself and pass no matter what the code does.
        tree = ast.parse(io.open(HERE / "tests.py", encoding="utf-8").read())
        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "setdefault"]
        self.assertEqual(calls, [], "AGENTRX_SANDBOX must be set unconditionally")
        assigns = [n for n in ast.walk(tree)
                   if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Subscript)
                   and getattr(n.value, "value", None) == "sandbox_test"]
        self.assertTrue(assigns, "the suite must pin itself to sandbox_test")

    def test_the_sandbox_swap_survives_a_delayed_windows_delete(self):
        """rmtree returns once the deletes are issued, but NTFS holds a
        directory in pending-delete until every handle closes - and on a dev box
        something always has one open. The rename then fails with WinError 5,
        which killed a full test run here."""
        src = inspect.getsource(sandbox._swap_into_place)
        self.assertIn("for attempt in range(attempts)", src)
        self.assertIn("except OSError", src)


def suite():
    loader = unittest.TestLoader()
    s = unittest.TestSuite()
    for cls in [TestSandbox, TestSandboxDurability, TestBridge, TestClerkParsing,
                TestPatchBlockExtraction, TestPatcher, TestProtectedTools,
                TestAutopsyReporting, TestEventBus, TestEventReplay,
                TestConsoleReplay, TestReviewRegressions, TestChatFrontDesk,
                TestSpecialistDesign]:
        s.addTests(loader.loadTestsFromTestCase(cls))
    return s


def run(verbosity=2):
    """Notebook entry point: `import tests; tests.run()`."""
    runner = unittest.TextTestRunner(verbosity=verbosity, stream=sys.stdout)
    return runner.run(suite())


if __name__ == "__main__":
    result = run()
    sys.exit(0 if result.wasSuccessful() else 1)
