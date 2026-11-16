"""extraction_normalize.v1: numbers, English single years, the unit in the bound metric phrase. Cases are generic
format / attribution cases, not the words of any sample."""

import pytest

from app import normalize as n


@pytest.mark.parametrize("written,canonical", [
    ("318", "318"), ("1,444", "1444"), ("7 851", "7851"), ("1 895 122", "1895122"), ("7 851", "7851"),
    ("12 345 678", "12345678"), ("0.6", "0.6"), ("48.20", "48.2"), ("１，２３４", "1234"), ("-2 500", "-2500"),
    ("2 193.5", "2193.5"),
])
def test_grouped_numbers_read_to_one_canonical_decimal(written, canonical):
    assert n.decimal_text(written) == canonical


@pytest.mark.parametrize("written", [
    "78 51", "1 44 4", "1 4444", "12  345", "1,444 555", "1 444,555", "7 851 222".replace(" ", " ", 1),
    "1234567890123456789", "0.1234567", "", "12a", "1.", ".5", "1 000 00",
])
def test_wrongly_grouped_or_out_of_range_is_not_a_number(written):
    assert n.decimal_text(written) is None


def test_a_grouped_number_is_one_token_and_its_parts_are_not_whole_numbers():
    text = "a total of 7 851 cases, 384 deaths and 1,444 tests; codes 78 51 (2026)"
    spans = n.number_tokens(text)
    words = [text[a:b] for a, b in spans]
    assert words == ["7 851", "384", "1,444", "78", "51", "2026"]
    at = text.index("851")
    assert not n.is_whole_number(text, at, at + 3)  # the tail of 7 851 is not a number of its own
    assert n.is_whole_number(text, text.index("7 851"), text.index("7 851") + 5)


def test_a_year_and_a_count_next_to_each_other_are_not_one_number():
    text = "In 2023 1 444 cases"  # four digits then a space: not a valid group, two numbers
    assert [text[a:b] for a, b in n.number_tokens(text)] == ["2023", "1 444"]


@pytest.mark.parametrize("written,year", [("2018", "2018"), ("in 2018", "2018"), ("In 2018", "2018"),
                                          ("During  2024", "2024")])
def test_an_english_single_year_is_a_year_interval(written, year):
    assert n.english_year(written) == {"type": "interval", "precision": "year", "value": year}


@pytest.mark.parametrize("written", ["2018-2019", "in 2018 and 2019", "since 2012", "18", "in 1850", "As of 2018",
                                     "2018年", "in the year 2018"])
def test_anything_else_is_not_read_as_a_single_year(written):
    assert n.english_year(written) is None


def spans(text, value, unit, metric):
    v = text.index(value)
    m = text.index(metric, v)
    u = text.index(unit, m)
    return text, v + len(value), u, u + len(unit), m, m + len(metric)


@pytest.mark.parametrize("text,value,unit,metric", [
    ("14 close contacts of the case were identified", "14", "contacts", "close contacts of the case"),
    ("2613 MERS-CoV cases have been reported", "2613", "cases", "MERS-CoV cases"),
    ("677 stool samples were analysed", "677", "samples", "stool samples"),
    ("2092 suspected hepatitis E cases", "2092", "cases", "suspected hepatitis E cases"),
])
def test_a_unit_inside_the_metric_phrase_right_after_the_value_is_bound(text, value, unit, metric):
    assert n.unit_bound(*spans(text, value, unit, metric))


@pytest.mark.parametrize("text,value,unit,metric", [
    ("14 cases and 3 contacts", "14", "contacts", "cases and 3 contacts"),  # another quantity in between
    ("14 not confirmed cases", "14", "cases", "not confirmed cases"),  # negation in between
    ("14 cases, contacts", "14", "contacts", "cases, contacts"),  # a separator in between
    ("14 or more cases", "14", "cases", "or more cases"),  # comparison words in between
    ("14 (new) cases", "14", "cases", "(new) cases"),
])
def test_a_unit_across_a_quantity_negation_comparison_or_separator_is_not_bound(text, value, unit, metric):
    assert not n.unit_bound(*spans(text, value, unit, metric))


def test_the_metric_phrase_must_start_right_after_the_value():
    text = "14 were seen among close contacts"
    v_end = 2
    m = text.index("close contacts")
    u = text.index("contacts")
    assert not n.unit_bound(text, v_end, u, u + 8, m, m + len("close contacts"))


def test_a_unit_outside_the_metric_phrase_or_a_fragment_is_not_bound():
    text = "14 close contacts of the case"
    m = text.index("close")
    metric_end = m + len("close contacts")
    assert not n.unit_bound(text, 2, text.index("case"), text.index("case") + 4, m, metric_end)  # outside
    assert not n.unit_bound(text, 2, text.index("cont"), text.index("cont") + 4, m, metric_end)  # a fragment
    assert n.unit_bound(text, 2, text.index("contacts"), text.index("contacts") + 8, m, metric_end)


def test_the_version_is_named():
    assert n.NORMALIZE_VERSION == "extraction_normalize.v1"
