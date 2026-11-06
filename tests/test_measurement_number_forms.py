"""Number forms and attribution read with the shared normalisation (extraction_normalize): grouped thousands,
English single years, the unit inside the bound metric phrase - and their negatives. Generic templates."""

import pytest
from test_measurement_rules import answer, at, client, fields_for, synthetic  # noqa: F401

from app import measurement_rules

TEXT = "From September 2022 to January 2023, {v} suspected cases were recorded at the post."


def run(client, text, value, **kw):  # noqa: F811
    unit = kw.pop("unit", "cases")
    f = fields_for(text, value, unit, "From September 2022 to January 2023",
                   {"type": "interval", "precision": "month", "start": "2022-09", "end": "2023-01"},
                   metric=kw.pop("metric", "suspected cases"), **kw)
    f["value"]["value"] = kw.get("canonical") or f["value"]["value"]
    return f, answer(client, synthetic(text, text, f))


def test_the_checker_reads_with_the_shared_normalization():
    assert measurement_rules.CHECKER_VERSION == "biocheck.extraction_rules.v10"
    assert measurement_rules.NORMALIZE_VERSION == "extraction_normalize.v3"


@pytest.mark.parametrize("written,canonical", [("1 444", "1444"), ("1 444", "1444"), ("1 444", "1444"),
                                               ("1,444", "1444"), ("12 345 678", "12345678")])
def test_a_grouped_value_is_one_number(client, written, canonical):  # noqa: F811
    text = TEXT.format(v=written)
    f = fields_for(text, written, "cases", "From September 2022 to January 2023",
                   {"type": "interval", "precision": "month", "start": "2022-09", "end": "2023-01"},
                   metric="suspected cases")
    f["value"]["value"] = canonical
    out = answer(client, synthetic(text, text, f))
    fields = out["fields"]
    assert fields["value"]["result"] == "match"
    assert all("several_quantities" not in (x.get("reason") or "") for x in fields.values())


def test_the_tail_of_a_grouped_number_is_not_a_whole_number(client):  # noqa: F811
    text = TEXT.format(v="7 851")
    f = fields_for(text, "851", "cases", "From September 2022 to January 2023",
                   {"type": "interval", "precision": "month", "start": "2022-09", "end": "2023-01"},
                   metric="suspected cases")
    out = answer(client, synthetic(text, text, f))
    assert out["fields"]["value"] == {"result": "mismatch", "reason": "value_span_not_a_whole_number",
                                                  **{k: v for k, v in out["fields"]["value"].items()
                                                     if k not in ("result", "reason")}}


@pytest.mark.parametrize("written", ["78 51", "1 44 4", "1 4444"])
def test_a_wrongly_grouped_run_is_not_one_value(client, written):  # noqa: F811
    text = TEXT.format(v=written)
    f = fields_for(text, written, "cases", "From September 2022 to January 2023",
                   {"type": "interval", "precision": "month", "start": "2022-09", "end": "2023-01"},
                   metric="suspected cases")
    f["value"]["value"] = written.replace(" ", "")
    out = answer(client, synthetic(text, text, f))
    assert out["fields"]["value"]["result"] in ("mismatch", "unsupported")
    assert out["status"] != "consistent"


def test_a_space_grouped_ratio_is_read_whole_not_split():
    out = measurement_rules.ratios_in("56% (1 923/3 432) were female", 0)
    assert [(r[2], r[5]) for r in out] == [(1923, 3432)]
    assert measurement_rules.ratios_in("78 51/2", 0)[0][2] == 51  # a wrong group is not one numerator


@pytest.mark.parametrize("text,year", [("2018", "2018"), ("In 2018", "2018"), ("in 2023", "2023")])
def test_an_english_single_year_is_a_year_period(text, year):
    assert measurement_rules.canonical_period(text) == ({"type": "interval", "precision": "year", "value": year}, None)


@pytest.mark.parametrize("text", ["in 2018 and 2019", "since 2012", "2018-2019"])
def test_other_year_forms_stay_unsupported(text):
    period, why = measurement_rules.canonical_period(text)
    assert period is None and why


def unit_case(client, text, value, unit, metric):  # noqa: F811
    f = fields_for(text, value, unit, "From September 2022 to January 2023",
                   {"type": "interval", "precision": "month", "start": "2022-09", "end": "2023-01"}, metric=metric)
    return answer(client, synthetic(text, text, f))["fields"]["unit"]


@pytest.mark.parametrize("phrase,unit,metric", [("14 close contacts", "contacts", "close contacts"),
                                                ("677 stool samples", "samples", "stool samples"),
                                                ("2613 MERS-CoV cases", "cases", "MERS-CoV cases")])
def test_a_unit_inside_the_bound_metric_phrase_matches(client, phrase, unit, metric):  # noqa: F811
    text = "From September 2022 to January 2023, " + phrase + " were recorded."
    got = unit_case(client, text, phrase.split()[0], unit, metric)
    assert (got["result"], got["reason"]) == ("match", "unit_in_bound_metric")


@pytest.mark.parametrize("phrase,unit,metric", [
    ("14 cases and 3 contacts", "contacts", "cases and 3 contacts"),  # another quantity
    ("14 not confirmed contacts", "contacts", "not confirmed contacts"),  # negation
    ("14 or more contacts", "contacts", "or more contacts"),  # comparison
])
def test_a_unit_across_a_quantity_negation_or_comparison_does_not_match(client, phrase, unit, metric):  # noqa: F811
    text = "From September 2022 to January 2023, " + phrase + " were recorded."
    assert unit_case(client, text, "14", unit, metric)["result"] != "match"


def test_a_unit_word_without_a_stated_metric_is_not_bound(client):  # noqa: F811
    text = "From September 2022 to January 2023, 14 close contacts were recorded."
    f = fields_for(text, "14", "contacts", "From September 2022 to January 2023",
                   {"type": "interval", "precision": "month", "start": "2022-09", "end": "2023-01"})
    out = answer(client, synthetic(text, text, f))
    assert out["fields"]["unit"]["result"] != "match"


def test_s19_a_known_positive_stays_consistent(client):  # noqa: F811
    from test_measurement_rules import ROWS, request_of
    out = answer(client, request_of(ROWS["S19"]))
    assert out["status"] == "consistent"  # answer() checks the checker version
