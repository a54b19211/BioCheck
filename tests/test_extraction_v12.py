"""Checker v12 (Codex r178): a dimension keeps its qualifying context. Its phrase - bounded by clause
punctuation - may not negate or exclude it nor join it with another member; else unsupported
dim_qualifier_dropped, the value itself unchanged. Generic templates, not the words of one sample."""

import pytest
from test_extraction_v4 import client  # noqa: F401
from test_extraction_v11 import answer, body

SENT = "2026年1月，{lead}医院A在湖北省共报告炭疽确诊病例10例。"


def case(lead, heading="## 按报告日统计"):
    return heading + "\n\n" + SENT.format(lead=lead)


def ask(client, text, kind, value):  # noqa: F811
    return answer(client, body(text, text.split("\n\n")[-1][:-1], [(kind, value)], scope="在湖北省"))


@pytest.mark.parametrize("lead", ["在成人中，", "成人中，", "人群为成人，"])
def test_a_whole_member_in_its_own_phrase_matches(client, lead):  # noqa: F811
    got = ask(client, case(lead), "population", "成人")
    assert got["status"] == "consistent" and got["dims"]["population"]["result"] == "match", got["dims"]


@pytest.mark.parametrize("lead", ["在非成人中，", "排除成人后，", "除成人外，", "在成人和儿童中，", "在儿童和成人中，",
                                  "在儿童、成人中，", "在成人或儿童中，", "成人以外，", "不含成人，"])
def test_a_negated_excluded_or_joined_member_is_not_the_value(client, lead):  # noqa: F811
    got = ask(client, case(lead), "population", "成人")
    assert got["status"] == "consistent"
    assert (got["dims"]["population"]["result"], got["dims"]["population"]["reason"]) == (
        "unsupported", "dim_qualifier_dropped"), got["dims"]


def test_a_negated_date_basis_heading_is_not_the_basis(client):  # noqa: F811
    got = ask(client, case("在成人中，", heading="## 不按报告日统计"), "date_basis", "按报告日统计")
    assert got["dims"]["date_basis"]["reason"] == "dim_qualifier_dropped"
    got = ask(client, case("在成人中，"), "date_basis", "按报告日统计")
    assert got["dims"]["date_basis"]["result"] == "match"
