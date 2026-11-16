"""extraction_normalize.v5: a period span is the whole written period. Generic format / attribution cases, not the
words of any sample."""

import pytest

from app import normalize as n


def whole(text, part):
    i = text.index(part)
    return n.period_span_whole_v5(text, i, i + len(part))


@pytest.mark.parametrize("text,part", [
    ("In 2018, X reported 3 cases.", "In 2018"),
    ("In 2018, X reported 3 cases.", "2018"),
    ("During 2018 X reported 3 cases.", "During 2018"),
    ("X reported 3 cases in 2018.", "2018"),
    ("2018: 3 cases", "2018"),
    ("2018年医院A报告3例。", "2018年"),
    ("医院A在2018年报告3例。", "2018年"),
    ("2018年1月医院A报告3例。", "2018年1月"),
    ("From January to March 2018, 3 cases.", "From January to March 2018"),
    ("截至2018年底共3例", "截至2018年底"),
    # the opener of a range written out in full in the span
    ("From September 2022 to January 2023, 42 cases.", "September 2022 to January 2023"),
    ("Between 1 and 9 May 2024, 300 samples.", "1 and 9 May 2024"),
])
def test_a_whole_period_span(text, part):
    assert whole(text, part)


@pytest.mark.parametrize("text,part", [
    ("In January 2018, X reported 3 cases.", "2018"),
    ("In Jan. 2018, X reported 3 cases.", "2018"),
    ("On 5 January 2018 X reported 3 cases.", "2018"),
    ("On January 5, 2018 X reported 3 cases.", "2018"),
    ("In 2018–2019, X reported 3 cases.", "2018"),
    ("In 2018-19, X reported 3 cases.", "2018"),
    ("In 2018/19, X reported 3 cases.", "2018"),
    ("In 2017–2018, X reported 3 cases.", "2018"),
    ("From 2017 to 2018, X reported 3 cases.", "2018"),
    ("From 2017 to 2018, X reported 3 cases.", "2017"),
    ("Since 2018, X reported 3 cases.", "2018"),
    ("From 2018, X reported 3 cases.", "2018"),  # a start, not a range the span writes out
    ("From May to June 2018, X reported 3 cases.", "June 2018"),
    ("By the end of 2018 X reported 3 cases.", "2018"),
    ("As of 2018 X reported 3 cases.", "2018"),
    ("In mid-2018 X reported 3 cases.", "2018"),
    ("In fiscal 2018 X reported 3 cases.", "2018"),
    ("In fiscal year 2018 X reported 3 cases.", "2018"),
    ("In 2018 Q1 X reported 3 cases.", "2018"),
    ("In the 2018 season X reported 3 cases.", "2018"),
    ("In 2018 and 2019 X reported 3 cases.", "2018"),
    ("In 2018, 2019 and 2020 X reported 3 cases.", "2018"),
    ("From 2018 onwards X reported 3 cases.", "2018"),
    ("In the 2010s X reported 3 cases.", "2010"),
    ("2018年1月医院A报告3例。", "2018年"),
    ("2018年第一季度医院A报告3例。", "2018年"),
    ("2018年上半年医院A报告3例。", "2018年"),
    ("2017年至2018年医院A报告3例。", "2018年"),
    ("2017年至2018年医院A报告3例。", "2017年"),
    ("2018年以来医院A报告3例。", "2018年"),
    ("自2018年起医院A报告3例。", "2018年"),
    ("2018年医院A报告3例。", "2018"),
])
def test_a_year_that_is_part_of_another_period_is_not_the_period(text, part):
    assert not whole(text, part)


def test_v1_reading_is_unchanged():
    assert n.NORMALIZE_VERSION == "extraction_normalize.v1"
    assert n.english_year("2018") == {"type": "interval", "precision": "year", "value": "2018"}
    text = "X reported 14 close contacts."
    v = text.index("14") + 2
    assert n.unit_bound(text, v, text.index("close"), text.index("close") + 5, v + 1, v + 15)  # v1 as released
