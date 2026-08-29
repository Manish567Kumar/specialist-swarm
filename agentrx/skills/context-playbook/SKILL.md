---
name: context-playbook
description: Checklist for finding context-engineering defects in an AI agent — stuffing, lost-in-the-middle positioning, turn waste, and budget caps that make correct behavior unreachable. Use whenever reviewing token/size stats and turn accounting against a failing transcript.
---

# Context Diagnostic Playbook

## What counts as a defect here (and what doesn't)

A context defect is about *volume and shape* of what the agent is forced to
read or accumulate — not the rules in the prompt, not the tool names.

## Checklist

1. **Stuffing.** Is information included that the task at hand never needed
   — few-shot examples for scenarios that don't recur, reference material
   nobody queries? Estimate the size cost.
2. **Positioning.** Is a load-bearing constraint buried in the middle of a
   long prompt or a long tool-result, where models are known to under-weight
   it (lost-in-the-middle)? A rule that's *present* but *ignored* because of
   where it sits is still a context defect.
3. **Turn waste.** Did the agent make several tool calls that a single
   well-scoped call could have answered? Count them from the Defect Brief's
   tool-call list.
4. **Budget interaction.** Is the agent close to a stated turn or token cap?
   If so — does that cap, combined with how much it has to read per turn,
   make the correct action structurally unreachable in the turns remaining?
   This is a compounding defect: it isn't just "too much context," it's
   "too much context relative to too little budget."
5. **False positive check.** Sometimes the failure isn't a context problem
   at all. If you don't see stuffing, bad positioning, or turn waste, say so
   plainly — a context finding invented to have something to report wastes
   the Attending's one patch for this round.

## How to report

Give a concrete number: what you'd cut and the resulting size estimate. Name
what you'd explicitly keep and why it's load-bearing — a good context patch
removes noise without removing the rule that actually matters.

## What is NOT a context defect

Stay inside your lane. If the fix you would write does not change how MUCH the
agent reads or WHERE in the input it sits, it is not yours to report — say the
context budget looks sound and describe what you saw instead. Do not speculate
about who else should look at it; you have not been told what else is being
examined.
