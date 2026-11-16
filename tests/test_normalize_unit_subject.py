"""extraction_normalize.v4: the unit is the last word of the metric phrase's own subject - a subset
named after an inclusion / exclusion / example / apposition boundary is never the unit. Generic cases."""

import pytest

from app import normalize as n


def bound(text, value, unit, metric):
    v = text.index(value) + len(value)
    m0 = text.index(metric, v)
    u = text.index(unit, m0)
    return n.unit_subject_head_v4(text, v, u, u + len(unit), m0, m0 + len(metric))


@pytest.mark.parametrize("text,value,unit,metric", [
    ("X reported 14 close contacts.", "14", "contacts", "close contacts"),
    ("X reported 14 close contacts excluding children.", "14", "contacts", "close contacts excluding children"),
    ("X reported 14 close contacts including children.", "14", "contacts", "close contacts including children"),
    ("X reported 14 new confirmed cases of cholera.", "14", "cases", "new confirmed cases of cholera"),
    ("X reported 14 newly confirmed cases.", "14", "cases", "newly confirmed cases"),
    ("X counted 14 hospital beds.", "14", "beds", "hospital beds"),
    ("X reported 14 health workers infected in May.", "14", "workers", "health workers infected in May"),
    ("X reported 1 confirmed case.", "1", "case", "confirmed case"),
])
def test_the_subject_head_is_the_unit(text, value, unit, metric):
    assert bound(text, value, unit, metric)


@pytest.mark.parametrize("text,value,unit,metric", [
    ("X reported 14 close contacts excluding children.", "14", "children", "close contacts excluding children"),
    ("X reported 14 close contacts including children.", "14", "children", "close contacts including children"),
    ("X reported 14 close contacts except children.", "14", "children", "close contacts except children"),
    ("X reported 14 close contacts such as nurses.", "14", "nurses", "close contacts such as nurses"),
    ("X reported 14 close contacts, mostly nurses.", "14", "nurses", "close contacts, mostly nurses"),
    ("X reported 14 close contacts (children).", "14", "children", "close contacts (children)"),
    ("X reported 14 close contacts - children.", "14", "children", "close contacts - children"),
    ("X reported 14 contacts among children.", "14", "children", "contacts among children"),
    ("X reported 14 cases and deaths.", "14", "deaths", "cases and deaths"),
    ("X reported 14 cases/deaths.", "14", "deaths", "cases/deaths"),
    ("X reported 14 contacts involving children.", "14", "children", "contacts involving children"),
    ("X reported 14 close contacts.", "14", "close", "close contacts"),
    ("X reported 14 confirmed.", "14", "confirmed", "confirmed"),
])
def test_a_subset_or_a_non_head_word_is_not_the_unit(text, value, unit, metric):
    assert not bound(text, value, unit, metric)


def test_the_version_is_named():
    assert n.NORMALIZE_V4 == "extraction_normalize.v4"
