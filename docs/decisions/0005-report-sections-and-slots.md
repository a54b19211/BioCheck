# 0005 Report sections: slots for the writer, sections for the checker

Status: accepted (recorded 2026-11-08; design task book 06 sections 2.2 and 3, 08 sections 2.2-2.4; the radar side is
its ADR 0014)

## Context

The radar writes the report by slot: before asking for the draft it lists the questions the report must answer
(J1, J2 ...), and when it asks for verification it says which section each paragraph is. BioCheck had no way to
take either.

## Decision

- **`POST /v1/generate` takes `slots`** (`[{slot, question}]`, numbered by the radar) and then answers with
  `sections`: `[{kind, slot?, status?, text}]`, one paragraph each. `kind` is judgment, evidence, gaps or scenarios;
  only a judgment has `slot` (a slot of the request, or a hypothesis id) and `status` (supported, refuted,
  insufficient, open, conflict; a hypothesis paragraph has none). There is no recommendation kind: a request carries
  no objectives, and without objectives that section stays empty. `draft` is the paragraphs joined by a blank line,
  each with its whitespace squashed so that one section is one paragraph for the verifier too.
- The model's schema is the flat list of sections (`SectionsOut`) and the prompt is `WRITE_BY_SLOT`: `WRITE` plus the
  structure and nothing else. A request without `slots` is written with `WRITE` and `DraftOut` exactly as before (the
  golden runs pin the prompt's hash). BioCheck does not check that a slot is one the request gave: the radar places
  the paragraphs and drops what it cannot place, and it is the only reader that knows what a hypothesis id is.
- **`POST /v2/verify` takes `sections`** (`[{kind, start, end}]`, characters of `draft`, strict like the rest of the
  request): inside the draft, not empty, not overlapping. A sentence that lies wholly in a section other than
  judgment, gaps (since 0006), scenarios or recommendation is a sentence of a fact section, and
  `check_sentence(fact_section=True)` does not let an inference unit stand as supported there. Without `sections`,
  or for a sentence in none of them, nothing changes. The model is not told the section: the rule is the program's.
- **Each claim of `/v2/verify` has `call`**, the position in `llm_calls` of the last `verify_claims` call that
  carried the sentence: after a cut-off split, the call of the smaller batch, and also when that call failed (the
  sentence is then unchecked and its reason says so). `null` when no call carried it.
- **`/v1/revise` reads a `/v2/verify` review**: a claim without `quotes` but with `units` gets the units' quotes as
  its quotes (found once or several times counts as found). `scope_span` has no equivalent in the units, so a v2
  review revises contradicted sentences only.

## Not in this step

- `[R#]` receipts as a kind of citation.
- The deterministic structure rules of 08 section 2.7 (sections present and not empty, a recommendation only with
  objectives ...). Comparing a slot's status with the radar's assessment is done by the radar.
- Telling the verify model which section a sentence is in.
