"""Checker v9 (v8 + extraction_normalize.v2, Codex r161): a period span is read only as the whole written period;
a unit in the bound metric phrase must be its head counting noun. Generic templates, not the words of a sample."""

import pytest
from test_measurement_rules import answer, client, fields_for, synthetic  # noqa: F401

from app import measurement_rules

YEAR = {"type": "interval", "precision": "year", "value": "2018"}


def en(client, prefix, period_text, unit="contacts", metric="close contacts", period=YEAR):  # noqa: F811
    text = prefix + ", Hospital B in total reported 23 " + metric + "."
    f = fields_for(text, "23", unit, period_text, period, obj="Hospital B", metric=metric,
                   mode="object_metric_total")
    return answer(client, synthetic(text, text, f))


def test_the_checker_reads_with_normalize_v2_or_later():
    # v9 on its own commit (58f7b08) read with extraction_normalize.v2; from v10 on, v3 (test_measurement_unit_subject)
    assert measurement_rules.CHECKER_VERSION == "biocheck.extraction_rules.v10"
    assert measurement_rules.NORMALIZE_VERSION == "extraction_normalize.v3"


@pytest.mark.parametrize("prefix,period_text", [("In 2018", "In 2018"), ("In 2018", "2018"),
                                                ("During 2018", "During 2018")])
def test_a_whole_single_year_stays_consistent(client, prefix, period_text):  # noqa: F811
    out = en(client, prefix, period_text)
    assert out["status"] == "consistent", out["fields"]


@pytest.mark.parametrize("prefix", ["In Feb. 2018", "On 3 March 2018", "In 2018-19", "In 2018/19", "In 2017–2018",
                                    "From 2017 to 2018", "From 2018", "By the end of 2018", "As of 2018",
                                    "In mid-2018", "In fiscal 2018", "In 2018 and 2019", "From 2018 onwards"])
def test_a_year_that_is_part_of_another_period_is_not_the_period(client, prefix):  # noqa: F811
    out = en(client, prefix, "2018")
    assert out["fields"]["period"]["result"] != "match", out["fields"]["period"]
    assert out["status"] != "consistent"


def test_a_range_written_out_in_the_span_keeps_its_opener_outside(client):  # noqa: F811
    period = {"type": "interval", "precision": "month", "start": "2022-09", "end": "2023-01"}
    out = en(client, "From September 2022 to January 2023", "September 2022 to January 2023", period=period)
    assert out["fields"]["period"]["result"] == "match", out["fields"]["period"]


def cn(client, text, period_text, period):  # noqa: F811
    f = fields_for(text, "23", "例", period_text, period, obj="医院B", metric="新增病例", mode="object_metric_total")
    return answer(client, synthetic(text, text, f))


@pytest.mark.parametrize("text", ["2018年1月，医院B共报告新增病例23例。", "2018年第一季度，医院B共报告新增病例23例。",
                                  "2018年上半年，医院B共报告新增病例23例。", "2017年至2018年，医院B共报告新增病例23例。",
                                  "2018年以来，医院B共报告新增病例23例。"])
def test_a_chinese_year_that_is_part_of_another_period_is_not_the_period(client, text):  # noqa: F811
    i = text.index("2018年")
    out = cn(client, text, text[i:i + 5], YEAR)
    assert out["fields"]["period"]["result"] != "match", out["fields"]["period"]


def test_a_whole_chinese_month_is_read(client):  # noqa: F811
    out = cn(client, "2018年1月，医院B共报告新增病例23例。", "2018年1月",
             {"type": "interval", "precision": "month", "value": "2018-01"})
    assert out["fields"]["period"]["result"] == "match", out["fields"]["period"]


@pytest.mark.parametrize("unit,metric", [("close", "close contacts"), ("confirmed", "confirmed cases"),
                                         ("cholera", "cases of cholera"), ("new", "new cases")])
def test_a_word_of_the_metric_that_is_not_its_head_counting_noun_is_not_the_unit(client, unit, metric):  # noqa: F811
    out = en(client, "In 2018", "In 2018", unit=unit, metric=metric)
    assert out["fields"]["unit"]["result"] != "match", out["fields"]["unit"]
    assert out["status"] != "consistent"


@pytest.mark.parametrize("unit,metric", [("cases", "confirmed cases"), ("cases", "cases of cholera"),
                                         ("children", "children")])
def test_the_head_counting_noun_is_the_unit(client, unit, metric):  # noqa: F811
    out = en(client, "In 2018", "In 2018", unit=unit, metric=metric)
    assert out["fields"]["unit"]["result"] == "match", out["fields"]["unit"]
