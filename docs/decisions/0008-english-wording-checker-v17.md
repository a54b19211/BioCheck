# 0008 English wording of contract v3 (checker v17)

Status: accepted (2026-11-08)

## Context

The first measurement batch of the D1 chain (24 sealed windows of WHO Disease Outbreak News; the annotation is
AI-assisted and was not reviewed by a person) built 40 candidates, and checker v16 judged none of them consistent -
not even two correct readings written by hand. The rules were written for sentences that carry one quantity, a
period in the sentence and wording like 累计; English notices write lists ("159 suspected cases of Lassa fever,
including 82 deaths"), "a total of", weeks, "since September 2012" and counts as words.

## Decision

Checker v17 (`dimension_rules`, contract v3) adds only what the correct sentences of that batch need. Everything
below is the `english` switch of `measurement_rules.check`; it is on for `/v3`, off for `/v2` (checker v10 answers as
before), and it reads a value only when its local sentence has no CJK character (Chinese answers are unchanged).

- **Several numbers in a sentence.** A quantity's phrase runs from its number to the next quantity, so a field after
  the value is the value's unless a number stands between them, and one before it unless a number precedes it in the
  group. A percentage in a parenthesis ("(CFR of 14%)", "4717 (56.5%) cases") is no quantity. Commas inside a
  parenthesis do not cut the value's group. The words of the next quantity's phrase, "including", and a rate in a
  parenthesis are not words of this quantity's phrase.
- **One period for a list.** The period is every list member's when it is the only date of the sentence, stands
  before every quantity or after every one, and each quantity from it to the value, the first of the sentence aside,
  is introduced as a member (including, and, of which, ...). "Bringing the total to 40" is not a member.
- **Totals.** "a total of" is a total word (scope `object_metric_total`); "As of <date>, a total of N" is a
  cumulative wording; an interval open at its end ("since September 2012") is cumulative and never a flow. A heading
  that names the object ("Dengue - Nepal") does not restrict a total: that comparison ignores case in English.
- **Period forms.** A first month without its day before an end with one is a range of days from the 1st; an ISO 8601
  week (Monday to Sunday) and a week ending on a date are seven days; "On 12 November 2015" is an interval of one
  day; "Between 2017 and 2021" is a range of months (2017-01 to 2021-12); "since September 2012" is an interval with
  only `start`. A period without a year, "to date" and "So far" stay refused.
- **Errors that are inconsistent.** A digit glued to a word (`case1`), a single digit right behind the metric's last
  word (`linked 2`), one end of a written range (`between 38 to 74`), the old values of "corrected from 1,595 and
  571", and N of "N of the M confirmed cases died" read as a count of the M. Each is a mismatch of the value, so the
  item is inconsistent. A reading that is only ambiguous stays unchecked; no rule guesses.
- **Spelled counts** one to twelve are numbers (`extraction_normalize.v6`, contract 1.15.0).

Not decided here: the event word of a quantity in a later comma group ("..., including 82 deaths, were reported"),
a place that trails the verb ("were reported across 19 states"), a cumulative claim over an interval, a period the
sentence does not carry, and an object that is only in the title. They stay unchecked.

## Consequences

- `CHECKER_VERSION` of `dimension_rules` is `biocheck.extraction_rules.v17`. A client names it explicitly
  (`extraction_contract.CHECKER_V17`, contract 1.15.0), and its producer must write the new canonical periods
  (a day range for a week and for a mixed-precision range, a month range for years, `start` only when open).
- `PeriodValue` of the request accepts an interval with `start` alone (`/v2` never produces one).
- The frozen golden answers of contract v1, v2 and v3 (v16) are unchanged apart from the checker version and two
  v3 answers that the rules above change on purpose.
