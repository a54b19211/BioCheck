# 0006 Verify semantics, contract v2: evidence for the claim, conditions read with their consequences

Status: accepted (recorded 2026-11-08; follows the limited evaluation of the r198 draft against `/v2/verify` (task book
06, step 1a). The unit prompt and the checker are now `biocheck.unit_verify.v2`; the segment rules stay
`biocheck.segment_rules.v1`.)

## Context

The first real-model run left three things the contract had not said clearly enough:

- "未触发高级别核查机制" was typed `fact` and judged supported because the cited text "does not mention a PHEIC
  declaration". Absence of a mention is a statement about the material, not about the world.
- Two conditional sentences of the same kind were handled differently: the consequence of one ("则可能改变…") was judged
  insufficient because the model applied the inference rule (is the premise in the text?) to a premise that is a
  hypothesis; the other was a non-claim.
- An inference that the model had checked and found supported was held back only because its paragraph was a `gaps`
  section.

## Decision

- **Evidence for the claim** (prompt, `CLAIM_VERIFY_UNITS`). A unit judged supported needs evidence for its core
  claim, whatever kind the model gave the unit: a quote that states the claim itself (for an inference, its premise,
  which the conclusion must not exceed). "The text does not mention X" cannot support "X did not happen / was not
  triggered", stated as a fact or as an inference; it supports only a unit that says "the material does not report
  X". There is no
  negation-word veto in the program (a death count of 0 is real evidence for "no deaths", ADR 0003), and a unit typed
  `fact` is not an error by itself.
- **Conditions read with their consequences** (prompt and payload). A conditional sentence has three roles: the
  condition (kind `condition`, no claim), the consequence (`governed_by` names every segment of its condition), and a
  fact asserted alongside (its own unit, no `governed_by`, verified as usual). A consequence is judged with its
  condition: the condition is its premise and needs no support from the text; a mere scenario ("may change the
  judgment") is `no_claim`; a consequence that overstates what the condition gives is `insufficient`. The program
  hints which segments a consequence answers (`consequent_of` on a segment that opens with 则/那么, from the markers of
  `segment_rules.py`; a hint like the markers, not a verdict, so `RULES_VERSION` does not change). It still takes the
  model's verdict as given: on a governed unit supported/no_claim mean "nothing stated" (as before), insufficient and
  contradicted stand.
- **Gaps** (`INFERENCE_SECTIONS`). A `gaps` paragraph may explain what the limits of the evidence imply, so an
  inference the model found supported there is no longer demoted by `inference_in_fact_section`. There is no hedge-word
  list: whether an inference is grounded is the model's check. `evidence` and every other section keep the rule.

## Consequences

- `checker_version` of a `/v2/verify` receipt is `biocheck.unit_verify.v2`; receipts of v1 and v2 are not
  comparable without that in hand.
- Not changed: a later date in the cited text stays a clue for the time check (no rule on it); `question`,
  `uncertainty`, `visibility` and `revision` sections are still fact sections.
- The writer prompt for slots (`WRITE_BY_SLOT`) was aligned with the `gaps` rule right after this change (commit
  "prompts: a gaps paragraph may explain the limits of the evidence and their grounded implications"): `evidence`
  states the facts of the material; `gaps` may explain the limits of the evidence and their grounded implications,
  and still never writes an inference as an established fact.
