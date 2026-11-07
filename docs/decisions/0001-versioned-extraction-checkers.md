# 0001 Versioned extraction checkers

Status: accepted (2026-11-06; records decisions taken 2026-11-02 to 2026-11-06)

## Context

`/v{1,2,3}/extraction-consistency` answer whether a processing product corresponds to its fixed source text.
Fabric and Radar store those answers next to the data they judge, so an answer must stay reproducible: the same
request, under the same checker version, gets the same answer forever.

## Decision

- Every rule, word list, unit table or number conversion change bumps `CHECKER_VERSION` of the module it touches.
  An old version is never edited; it stays reproducible at the commit that released it.
- One module per contract, named by what it checks: `app/item_rules.py` (contract v1), `app/measurement_rules.py`
  (contract v2), `app/dimension_rules.py` (contract v3). A newer contract imports the older module's rules and
  passes its own normalisation functions in (`unit_head`, `period_whole`, `output`) instead of copying them.
- Rules shared with Radar live in the `extraction-contract` wheel (`extraction_contract.normalize`), pinned by hash
  in `requirements-contract.txt`; each normalisation version is a separate function there, never changed in place.
- Release checker versions in batches of rule changes, not one version per boundary case.
- `tests/fixtures/checker_golden.jsonl` freezes the answers of the current versions; it is regenerated only with
  a version bump (`tests/golden.py`).

## Version history

| Contract | Checker | Commit | What changed |
|---|---|---|---|
| v1 | v1 | 92c1e37, 8d4c4be | rule-only check of each field at its own span in bounded passages |
| v1 | v2 | 47b8d67 | whole numbers only, a table header of the same table, one quantity per group, a rate needs positive evidence |
| v1 | v3 | a1c8e29 | numbers bounded to 18 significant digits: no server error on contract-valid edge input |
| v2 | v4 | 17f99d9 | measurements: kind (flow / cumulative / stock / rate), canonical period, scope mode, qualifier, modality |
| v2 | v5 | 2a61aad | exactness needs positive evidence; only the nearest effective heading supplies a field; kind evidence, ratios and units belong to the value's own group; a unit span is the whole recognised unit |
| v2 | v6 | 02e8e4c | the quantity's phrase must be covered by the item's fields; comparators are whole phrases with their negation; a percentage takes only a ratio written next to it |
| v2 | v7 | 45600af | a negated predicate outside the fields proves no event |
| v2 | v8 | 43dba74 | extraction_normalize.v1 (contract wheel 1.5.0): space-grouped numbers, English single years, the unit inside the bound metric phrase |
| v2 | v9 | 58f7b08 | extraction_normalize.v2 (1.6.1): a period span must be the whole written period; a bound unit is the phrase's head counting noun |
| v2 | v10 | 407b45b | extraction_normalize.v3 (1.7.0): the unit is the head of the metric phrase's own subject, never a subset named after "including / excluding / such as". Current for contract v2 |
| v3 | v11 | e98b392 | business dimensions (disease, population, date basis, ...) judged by literal, role evidence and relation to the value; extraction_normalize.v4 (1.8.0) |
| v3 | v12 | 1c08f7c | a dimension keeps its qualifying context: not negated, excluded or joined with another member (1.9.0) |
| v3 | v13 | 978af0a | a dimension fills a whole slot of a recognised structure (1.10.0) |
| v3 | v14 | 2b395ae | only independently proven, kind-compatible neighbours bound a metric slot (1.11.0) |
| v3 | v15 | 73e425f | output measures: a reporting-period date basis written as such, a magnitude kept with its classifier (万支), output a flow only as actual output (1.12.0) |
| v3 | v16 | b67aaef | extraction_normalize.v5 (1.13.0): a complete date ending the line above (a notice's 发布时间) is its own, not part of the body's period |
| v3 | v17 | r3/biocheck-v17 | English wording (docs/decisions/0008): several numbers in a sentence, one period for a list, "a total of", weeks, "since ...", "On ...", ranges of years, glued digits, ends of ranges and old values as errors, spelled counts (extraction_normalize.v6, 1.15.0). Current for contract v3 |

## Consequences

- The code carries only the current rules; why a rule exists is said next to it, the history is here and in the
  commit messages.
- A caller that needs an older checker version runs that commit; the service answers only the current ones.
