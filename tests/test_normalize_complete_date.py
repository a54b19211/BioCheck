"""extraction_normalize.v5: a complete written date ending the line before the period (a notice's 发布时间) is
its own - the real NMPA case; every other continuation before or after the span still makes it part
of a larger period, across a line break too."""

import pytest

from app import normalize as n

NMPA = "关于批准注册医疗器械产品的公告（2026年3月）（2026年第38号）\n发布时间：2026-04-13\n2026年3月，国家药监局共批准注册医疗器械产品272个。"  # noqa: E501


def whole(f, text, part, k=0):
    i = -1
    for _ in range(k + 1):
        i = text.index(part, i + 1)
    return f(text, i, i + len(part))


def test_the_real_nmpa_period_after_a_publication_date_is_whole():
    assert whole(n.period_span_whole_v5, NMPA, "2026年3月", 1) is True
    assert n.NORMALIZE_V5 == "extraction_normalize.v5"


@pytest.mark.parametrize("before", ["发布时间：2026/04/13\n", "Published 2026-04-13\n", "2026.04.13 "])
def test_any_complete_written_date_before_the_period_is_its_own(before):
    text = before + "2026年3月，共批准注册产品272个。"
    assert whole(n.period_span_whole_v5, text, "2026年3月") is True


@pytest.mark.parametrize("text,part", [
    ("January\n2018 saw 42 cases.", "2018"),  # a month word on the line before: part of January 2018
    ("2017 -\n2018 saw 42 cases.", "2018"),  # a range mark across the line break
    ("15\n2018 saw 42 cases.", "2018"),  # a bare number: a day, not a complete date
    ("15 March 2018 saw 42 cases.", "March 2018"),  # a day before the month on the same line
    ("From May\nto June 2018 there were 42 cases.", "June 2018"),  # a range word before
    ("自2026年1月至2026年3月，共报告病例42例。", "2026年3月"),  # a CJK range mark attached
    ("2026-04-13 to 2026年3月", "2026年3月"),  # a range word after the date: still part of a range
])
def test_other_continuations_still_make_the_span_part_of_a_larger_period(text, part):
    assert whole(n.period_span_whole_v5, text, part) is False


def test_a_partial_date_is_not_a_complete_one():
    for before in ("04-13\n", "2026-04\n", "12026-04-13\n"):
        text = before + "2026年3月，共批准注册产品272个。"
        assert whole(n.period_span_whole_v5, text, "2026年3月") is False, before
