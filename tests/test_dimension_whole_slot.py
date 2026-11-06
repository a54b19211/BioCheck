"""A dimension fills a whole slot of a recognised structure - a positive label's
value, a phrase of its own (or "在X中"), a heading line, or a slot of the metric phrase - never a part of a
longer qualified phrase. Generic templates; the value itself stays consistent."""

import pytest
from test_measurement_rules import client  # noqa: F401
from test_dimension_rules import answer, body

SENT = "2026年1月，{lead}医院A在湖北省共报告炭疽确诊病例10例。"


def ask(client, lead, kind, value, heading="## 按报告日统计"):  # noqa: F811
    text = heading + "\n\n" + SENT.format(lead=lead)
    return answer(client, body(text, text.split("\n\n")[-1][:-1], [(kind, value)], scope="在湖北省"))


@pytest.mark.parametrize("lead,value", [("人群：女性成人，", "女性成人"), ("人群：成人，", "成人"),
                                        ("人群为成人，", "成人"), ("在成人中，", "成人"), ("纳入人群：儿童，", "儿童")])
def test_a_value_filling_a_whole_slot_matches(client, lead, value):  # noqa: F811
    got = ask(client, lead, "population", value)
    assert got["status"] == "consistent" and got["dims"]["population"]["result"] == "match", got["dims"]


@pytest.mark.parametrize("lead,value", [
    ("在女性成人中，", "成人"),  # a part of a longer qualified population
    ("人群：女性成人，", "成人"),  # a part of a labelled value
    ("不纳入人群：成人，", "成人"),  # a label that is not a positive label (its own negation before the colon)
    ("排除人群：成人，", "成人"),
    ("在住院成人中，", "成人"),
    ("人群为女性成人，", "成人"),
])
def test_a_part_of_a_longer_qualified_phrase_is_not_the_value(client, lead, value):  # noqa: F811
    got = ask(client, lead, "population", value)
    assert got["status"] == "consistent"
    assert got["dims"]["population"]["result"] == "unsupported", got["dims"]
    assert got["dims"]["population"]["reason"] in ("dim_phrase_not_whole", "dim_qualifier_dropped")


def test_a_heading_is_the_value_only_as_its_whole_line(client):  # noqa: F811
    assert ask(client, "在成人中，", "date_basis", "按报告日统计")["dims"]["date_basis"]["result"] == "match"
    got = ask(client, "在成人中，", "date_basis", "按报告日统计", heading="## 按报告日统计（初步）")
    assert got["dims"]["date_basis"]["result"] == "unsupported"
