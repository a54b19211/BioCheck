"""The real NMPA notice shape - a 发布时间 line above the body - does not make the
body's period part of a larger one under contract v3; contract v2 unchanged; other continuations
still refuse the period."""

from test_measurement_rules import answer as answer_v2
from test_measurement_rules import client, fields_for, synthetic  # noqa: F401
from test_dimension_rules import answer

M3 = {"type": "interval", "precision": "month", "value": "2026-03"}
NOTICE = ("关于批准注册医疗器械产品的公告（2026年3月）（2026年第38号）\n发布时间：2026-04-13\n"
          "2026年3月，国家药监局共批准注册医疗器械产品272个。")
QUOTE = "2026年3月，国家药监局共批准注册医疗器械产品272个"


def item(client, text, contract=3):  # noqa: F811
    body_at = text.index(QUOTE)
    f = fields_for(text, "272", "个", "2026年3月", M3, obj="国家药监局", metric="批准注册医疗器械产品",
                   mode="object_metric_total")
    # the body's period and metric, not the title's (the title also says 2026年3月 and 批准注册医疗器械产品)
    f["period"]["span"] = {"passage": "p1", "start": 1000 + body_at, "end": 1000 + body_at + len("2026年3月")}
    m_at = text.index("批准注册医疗器械产品", body_at)
    f["metric"]["span"] = {"passage": "p1", "start": 1000 + m_at, "end": 1000 + m_at + len("批准注册医疗器械产品")}
    b = synthetic(text, QUOTE, f)
    if contract == 3:
        b["contract_version"] = "extraction_consistency.v3"
        b["items"][0]["dims"] = []
        b["items"][0]["family"] = "milestone_count"
        return answer(client, b)
    return answer_v2(client, b)


def test_a_publication_date_line_above_the_body_leaves_its_period_whole_in_v16(client):  # noqa: F811
    got = item(client, NOTICE)
    assert got["fields"]["period"]["result"] == "match", got["fields"]["period"]
    assert got["status"] == "consistent", {k: v for k, v in got["fields"].items() if v["result"] != "match"}


def test_contract_v2_keeps_the_v2_period_rule(client):  # noqa: F811
    got = item(client, NOTICE, contract=2)
    assert (got["fields"]["period"]["result"], got["fields"]["period"]["reason"]) == (
        "unsupported", "period_span_not_the_whole_period")


def test_a_bare_number_on_the_line_above_still_refuses_the_period(client):  # noqa: F811
    got = item(client, NOTICE.replace("发布时间：2026-04-13", "编号：13"))
    assert got["fields"]["period"]["result"] == "unsupported"
