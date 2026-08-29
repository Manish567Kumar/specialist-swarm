---
name: prompt-logic-playbook
description: Checklist for finding defects in an AI system prompt — rule conflicts, unreachable instructions, hardcoded outcomes, and escalation framed as failure. Use whenever reviewing a coordinator or agent's system prompt against tickets it handled incorrectly.
---

# Prompt-Logic Diagnostic Playbook

## What counts as a defect here (and what doesn't)

A prompt-logic defect is a problem in the *instructions themselves* — not the
tools available, not how much context is stuffed in. If removing a rule (or
rewriting one sentence) would fix the observed failure, it belongs here.

## Checklist

1. **Rule conflicts.** Does one instruction say to always do X while another
   says to do Y in the same situation? Quote both.
2. **Unreachable instructions.** Is there a rule that *sounds* reasonable in
   isolation but can never actually fire given another constraint elsewhere
   in the same prompt? Read every instruction against every other one and ask
   what is still possible; a goal and a cap on the means to reach it are the
   usual pairing.
3. **Hardcoded outcomes.** Does the prompt fix any part of the agent's
   *output* in advance, rather than letting it follow from what actually
   happened? Look for status fields, verdicts, or summary phrasing the prompt
   supplies rather than derives. This is the single most damaging defect class
   — it turns an honest failure into a false success.
4. **Asking for help penalized.** Does the prompt attach a cost — reputational,
   procedural, numeric — to the agent admitting it cannot finish? An agent
   that is charged for saying "I need a human" will learn to declare victory
   instead, even when handing off is the correct action.
5. **Silent scope narrowing.** Does a rule quietly exclude a class of ticket
   ("routing only handles X") without giving the agent any path to still get
   Y handled?

## How to report

Quote the exact offending sentence(s) verbatim — a paraphrase is not
evidence. State the minimal rewrite: change only what's necessary to remove
the conflict/hardcode/penalty, don't rewrite the whole prompt.

## What is NOT a prompt-logic defect

Stay inside your lane. If the fix you would write does not change the wording
of an instruction, it is not yours to report — say the prompt looks sound and
describe what you saw instead. Do not speculate about who else should look at
it; you have not been told what else is being examined, and a guess dressed as
a referral is noise the synthesis has to unpick.
