"""Checker v15 (Codex 783, T-24 industrial): a reporting-period date basis written as such, a magnitude-and-
classifier unit kept whole, output a flow only as actual output; contract v2 (checker v10) unchanged, and an
entity written fused with its site still unproven."""

import pytest
from test_measurement_rules import at, client, fields_for, synthetic  # noqa: F401
from test_dimension_rules import answer

Q1 = {"type": "interval", "precision": "quarter", "value": "2026Q1"}
HEAD = "## 甲公司\n\n### 按报告期统计\n\n"  # the entity a section of its own, the basis a heading


def ask(client, sentence, value="120", unit="万支", metric="实际产出的注射剂产量", obj="苏州工厂", scope="苏州工厂",  # noqa: F811
        dims=(("entity", "甲公司"), ("date_basis", "按报告期统计")), head=HEAD, kind="flow", contract=3):
    text = head + sentence
    quote = sentence[:-1]
    f = fields_for(text, value, unit, "2026年第一季度", Q1, kind=kind, obj=obj, metric=metric, scope=scope,
                   mode="stated" if scope else "unknown")
    b = synthetic(text, quote, f)
    if contract == 3:
        b["contract_version"] = "extraction_consistency.v3"
        b["items"][0]["dims"] = [{"kind": k, "state": "stated", "value": v, "span": at(text, v)} for k, v in dims]
        b["items"][0]["family"] = "capacity"
        return answer(client, b)
    from test_measurement_rules import answer as answer_v2

    return answer_v2(client, b)


ACTUAL = "2026年第一季度，苏州工厂实际产出的注射剂产量为120万支。"


def test_actual_quarterly_output_with_a_reporting_period_basis_and_a_whole_unit_is_checked(client):  # noqa: F811
    got = ask(client, ACTUAL)
    assert got["status"] == "consistent", {k: v for k, v in got["fields"].items() if v["result"] != "match"}
    assert (got["fields"]["unit"]["result"], got["fields"]["unit"]["reason"]) == ("match", "unit_at_value")
    assert got["fields"]["measure_kind"]["reason"] == "actual_output_in_interval"
    assert got["dims"]["date_basis"]["result"] == "match"
    assert got["dims"]["entity"]["result"] == "match", got["dims"]["entity"]


def test_the_magnitude_is_never_dropped_from_the_unit(client):  # noqa: F811
    got = ask(client, ACTUAL, unit="支")
    assert got["fields"]["unit"]["result"] == "mismatch"
    assert got["fields"]["unit"]["reason"] == "unit_span_is_not_the_unit_at_value:万支"
    assert got["status"] != "consistent"


@pytest.mark.parametrize("basis", ["按生产日期统计", "按发货日期统计", "报告期内"])
def test_a_production_or_shipping_date_or_a_bare_reporting_period_is_no_reporting_period_basis(client, basis):  # noqa: F811
    got = ask(client, ACTUAL, head=f"## 甲公司\n\n### {basis}\n\n", dims=(("entity", "甲公司"), ("date_basis", basis)))
    assert got["dims"]["date_basis"]["result"] == "unsupported"


@pytest.mark.parametrize("sentence,metric,result,reason", [
    ("2026年第一季度，苏州工厂计划产出的注射剂产量为120万支。", "计划产出的注射剂产量", "unsupported",
     "output_planned_not_actual"),
    ("2026年第一季度，苏州工厂的注射剂产能为120万支。", "注射剂产能", "unsupported",
     "output_capacity_not_actual"),
    ("2026年第一季度，苏州工厂的注射剂产成品库存为120万支。", "注射剂产成品库存", "mismatch",
     "inventory_is_not_a_flow"),
])
def test_planned_output_capacity_and_inventory_are_not_actual_output(client, sentence, metric, result, reason):  # noqa: F811
    got = ask(client, sentence, metric=metric)
    assert (got["fields"]["measure_kind"]["result"], got["fields"]["measure_kind"]["reason"]) == (result, reason)
    assert got["status"] != "consistent"


def test_an_entity_written_fused_with_its_site_is_still_not_proven(client):  # noqa: F811
    # the original business sentence: the company fused with its site - kept unproven (Codex 783)
    got = ask(client, "2026年第一季度，甲公司苏州工厂实际产出的注射剂产量为120万支。", obj="甲公司苏州工厂", scope=None,
              head="## 按报告期统计\n\n")
    assert got["dims"]["entity"]["result"] == "unsupported"


def test_contract_v2_stays_checker_v10(client):  # noqa: F811
    got = ask(client, ACTUAL, contract=2)
    assert got["fields"]["unit"]["result"] != "match"
    assert got["fields"]["measure_kind"]["reason"] == "no_event_wording"
