# 0007 The model answers /v2/verify by sentence group

Status: accepted (recorded 2026-11-08; amends the answer shape of ADR 0004). The unit prompt and the checker are now
`biocheck.unit_verify.v3`; the segment rules stay `biocheck.segment_rules.v1`, and the receipt does not change.

## Context

In a rerun on the r198 draft one batch (four sentences, fifteen units) failed twice with `units.N.sentence Field
required`: the model left the sentence number out of every unit, and the retry, which carries the validation error,
repeated the answer. In the flat shape the number was the last field of each unit and was written once per unit.

## Decision

- The model answers `{"claims":[{"sentence", "units":[...], "split":{...}|null}]}`: one group per sentence, the
  program's index written once and first, the units without it, the model's own split report (`complete`,
  `incomplete`, `uncertain`) inside the group. It replaces the two flat lists `units` and `splits`.
- The program turns the groups into what it used before (the units of a sentence, its split report) and goes on as
  before. The number is never taken from the position: a group without `sentence` is not a valid answer (the single
  validation retry, then the batch is pending, as for any invalid output); a group whose number is not in the batch is
  not read; a sentence that no group names has no units, so the form check (`empty_result`) keeps it pending and its
  split is `not_reported`. Two groups for one sentence have their units joined and the first report kept, as the flat
  lists did. There is no repair and no fallback.
- `split` may be left out, as the flat `splits` entry could be: the sentence is then held back, never passed.
- Version: `biocheck.unit_verify.v3`. The rules and the receipt are the same as in v2, but what the model is asked and
  how it answers is not, receipts of v2 exist from the flat form, and the two may differ for the same input.
  `rules_version` is unchanged.

## Consequences

- The schema is now three levels deep (claims, units, quotes) where AGENTS.md asks for two; one number per group is
  what removes the failure above.
- The offline stand-in model of devtools tells the unit check from the claim check by how the format line opens
  (`{"claims":[{"sentence":int,"units":[`), and answers in this shape; a test here pins the opening.
