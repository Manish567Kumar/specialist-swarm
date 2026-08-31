---
name: tool-spec-playbook
description: Checklist for finding defects in an AI agent's tool manifest — near-duplicate tools, vague names, missing constraints, unused bloat. Use whenever reviewing a tool list against which tools were actually called in a failing transcript.
---

# Tool-Spec Diagnostic Playbook

## What counts as a defect here (and what doesn't)

A tool-spec defect is a problem in the *tool manifest* — names, descriptions,
count, constraints. Not what the prompt tells the agent to do, not how much
surrounding context there is.

## Checklist

1. **Near-duplicates.** Do two or more tools return functionally the same
   data under different names? Read the descriptions side by side and ask
   whether a caller could tell them apart. Every duplicate is a live choice
   the model has to make wrong before it can even start the real task — and
   it re-burns tokens each call.
2. **Vague names.** Does a tool's name carry any information about what it
   actually does? Generic verbs and unnumbered variants tell the caller
   nothing. An agent under time or turn pressure will call the wrong one, or
   call several to work out which one it wanted.
3. **Missing constraints.** Could a malformed call succeed when it shouldn't
   (no enum on a status field, no required customer-id parameter)?
4. **Unused tools.** Cross-reference against the Defect Briefs' tool-call
   lists. A tool that's never invoked across every failing transcript you
   were shown is a bloat candidate — say so, but flag it as weaker evidence
   than a tool you saw actively misused.
5. **Count vs. task.** Is the total tool count proportionate to the actual
   task? A coordinator juggling a 16-turn budget with 15+ tools is spending
   turns just narrowing down which tool to use.

## How to report

Name the exact tools you'd remove or merge, and what the manifest count
becomes if your recommendation lands. If you'd rewrite a description instead
of removing the tool, give the exact replacement text.

## What is NOT a tool-spec defect

Stay inside your lane. If the fix you would write does not change the manifest
— a name, a description, a parameter, an entry added or removed — it is not
yours to report. Say the manifest looks sound and describe what you saw
instead. Do not speculate about who else should look at it; you have not been
told what else is being examined.
