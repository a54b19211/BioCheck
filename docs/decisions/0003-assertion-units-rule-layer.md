# 0003 Assertion units: the rule layer settles the form, the model the meaning

Status: accepted (recorded 2026-11-08; design task book 06, section 2.2-2.4)

## Context

A compound assertion ("…, although not declared a PHEIC, …") was judged supported as one sentence: the program only
asked for one short quote per citation, and nothing cut a sentence at its commas. The opposite error also happened: a
conditional sentence ("if the notice is later corrected, …") was treated as a stated fact. A program cannot tell what
the assertions of a sentence are. The scope of a negation or a condition reaches across commas, and a compound with
no connector word (获批进入医保) has no mark at all.

## Decision

- `segment_rules.py` cuts a sentence into candidate segments (commas, semicolons, enumeration commas, connector
  words) and numbers them S1... A segment is bookkeeping, never a unit of judgement: the model reads the whole
  sentence and its neighbours. A one-character connector (并 且 而 但) cuts only where it is a word of its own,
  English connectors only as whole words.
- The model reports units, each at a segment with a verbatim fragment. `unit_rules.py` numbers them U1..., locates
  each fragment inside its segment (not found or not unique: the unit is void, with the reason), and sums the
  sentence verdict itself.
- The receipt keeps two things apart. `form_check` is what the program can decide: every segment has a unit, no
  empty result, no undeclared overlap, every `governed_by` maps, no negation word of a segment is dropped between
  segment and unit. It never says the units are complete; whether they are all the assertions is the model's own
  report and is recorded elsewhere. A failed form check keeps the sentence pending, except that a contradiction
  stands.
- A negated fact is supported only by a located quote that holds a negation or by a structured field whose path and
  value check out; a condition, and what it governs, is not a stated fact.
- The negation, condition and connector word lists, and their pseudo-triggers, are cut points and hints, never proof
  of meaning (the ConText idea). They are versioned by `RULES_VERSION`. A later date in a cited text is a clue for
  the semantic check, not a verdict.

## Consequences

- The semantic part (prompt, output schema, batch limits) comes with `/v2/verify`; `/v1/verify` is unchanged.
- A word list that is wrong costs a pending sentence or a missed cut, never a wrong "supported" on its own.
