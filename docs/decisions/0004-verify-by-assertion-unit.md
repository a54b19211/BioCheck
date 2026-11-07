# 0004 Verify by assertion unit: batches, and who decides what

Status: accepted (recorded 2026-11-08; design task book 06, section 2.2 and 3)

## Context

ADR 0003 put the form of a sentence's units under rules and left the semantic part for `POST /v2/verify`: the
prompt, the model's output and the batch boundary. The model gives the units and its own opinion of the split; the
program does everything it can check.

## Decision

- One model call per batch, stage `verify_claims`, through the same `llm.chat_json` as `/v1`. The answer is two flat
  lists, `units` and `splits`, each entry naming its sentence by the program's index. The model reports the segment
  numbers the program gave and verbatim fragments; it never invents a unit number or an offset. Numbering, location,
  the form check and the sentence verdict are the program's (`unit_rules.py`).
- Batch boundary: at most 4 sentences and 24 candidate segments of one paragraph. A sentence over 8 segments or 300
  characters goes alone; one over 24 segments is not sent and stays pending (`batch_limit`). An output that was cut
  off (`finish_reason` length) splits its batch in two, down to one sentence, and a single sentence cut off stays
  pending (`output_truncated`). So a batch of n sentences takes at most 2n-1 calls, and `max_llm_calls` caps the
  request as before (a batch it has no call for is `unchecked`). There is no other retry here besides the
  transport's single validation retry.
- `form_check` and `semantic_split` are recorded apart. `semantic_split` is the model's own report that its units
  cover the sentence (`complete`, `incomplete`, `uncertain`) and says so (`source: model_self_assessment`). The
  program takes a sentence as supported only when that report is `complete`; a missing or other report holds the
  support back (`semantic_split_*`). A contradiction or a non-claim is not held back. No field says the split is
  guaranteed complete.
- Kept from `/v1`: the numbers of a supported fragment (in the cited text, or named in the reason), uncited facts,
  the source-use rules, read requests and the coverage check. The coverage check is also told, per sentence, what
  it states and what is only a condition.
- The time facts of each evidence (published, first seen, proof available, replay mode) travel with the references;
  a later date than the one the sentence names its document by goes along as a clue (`time_clues`), never a verdict.

## Not in this step

- The section a sentence belongs to (the "inference only in judgment sections" rule): `unit_rules` has the switch
  (`fact_section`); the request field comes with the report sections (task book 08).
- `scope_span` and revision on units: `/v1/revise` still revises `/v1/verify` reviews.
- A second pass that shows the model the context of every place a repeated quote occurs: the places are recorded,
  an unresolved repeat stays pending.

## Consequences

- `/v1/verify`, `/v1/revise` and their prompts are unchanged (the shared paragraphs of the verify prompt were moved
  into constants; the sentence-level prompt is byte-identical).
- A model that omits a sentence, a segment or its split report costs a pending sentence, never a supported one.
