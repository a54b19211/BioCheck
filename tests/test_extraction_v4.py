"""Checker v4 / contract v2 (T-29 P3). Real samples: tests/fixtures/p3_matrix.json (matrix v2 of extraction-contract:
real clauses of fixed clean_text.v11 artifacts with their real offsets, every field's literal span, the final
values and the expected outcome). Each matrix row becomes a real HTTP request to /v2; negatives of the kind
"wrong final value at a real span" change only the final value. Synthetic texts cover what no real sample has
yet: heading hierarchy, tables, period edge cases, decimal bounds."""

import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest
from conftest import FakeLLM
from fastapi.testclient import TestClient

from app import extraction_v4 as v4
from app.service import create_app

TOKEN = "test-token"
MATRIX = json.loads((Path(__file__).parent / "fixtures" / "p3_matrix.json").read_text(encoding="utf-8"))
ROWS = {r["id"]: r for r in MATRIX["rows"]}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("SERVICE_TOKEN", TOKEN)
    with TestClient(create_app(FakeLLM())) as c:
        yield c


def post(client, body):
    return client.post("/v2/extraction-consistency", json=body, headers={"Authorization": f"Bearer {TOKEN}"})


def dec(x):
    return None if x is None else format(Decimal(str(x)).normalize(), "f")


def request_of(row, final=None):
    """The v2 request a correct client builds from a matrix row (final values at the literals' real spans)."""
    final = final or row["final"]
    f = row["fields"]
    c0, c1 = row["clause_span"]

    def span(name):
        x = f[name]
        return {"passage": "p1", "start": x["start"], "end": x["end"]} if "start" in x else None

    def field(name, value):
        s = span(name)
        return {"state": "stated", "value": value, "span": s} if s and value not in (None, "") else {"state": "unknown"}

    period = {"state": "stated", "value": final["period"], "span": span("period")} \
        if final.get("period") and span("period") else {"state": "unknown"}
    fields = {"object": field("object", final["object"]), "metric": field("metric", final["metric"]),
              "value": field("value", dec(final["value"])), "unit": field("unit", final["unit"]),
              "period": period, "scope": field("scope", final["scope"]),
              "scope_mode": final["scope_mode"] if final["scope_mode"] != "stated" or span("scope") else "unknown",
              "denominator": field("denominator", dec(final["denominator"])), "measure_kind": final["measure_kind"],
              "qualifier": final["qualifier"], "modality": final["modality"]}
    if fields["scope_mode"] != "stated":
        fields["scope"] = {"state": "unknown"}
    a, b = row["sentence_span"]
    return {"contract_version": "extraction_consistency.v2",
            "subject": {"source_id": row["source"], "record_id": row["record"][:2000], "version_id": row["version_id"],
                        "raw_sha256": row["raw_sha256"], "is_demo": row["is_demo"],
                        "artifact": {"processor_version": row["processor"], "text_sha256": row["text_sha256"]}},
            "passages": [{"id": "p1", "role": "primary", "start": c0, "end": c1, "text": row["clause"]}],
            "items": [{"id": row["id"], "kind": "measurement", "producer": {"name": "bioradar.triage", "version": "test"},
                       "locator": {"passage": "p1", "start": a, "end": b}, "quote": row["sentence"], "fields": fields}]}


def answer(client, body):
    r = post(client, body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["contract_version"] == "extraction_consistency.v2" and out["checker_version"] == v4.CHECKER_VERSION
    assert out["llm_calls"] == []
    return out["items"][0]


SAMPLES = [r["id"] for r in MATRIX["rows"] if r["group"] != "negative"]


@pytest.mark.parametrize("sid", SAMPLES)
def test_real_samples_get_their_expected_status(client, sid):
    row = ROWS[sid]
    item = answer(client, request_of(row))
    assert set(item["fields"]) == {"locator", *v4.FIELDS}
    assert item["status"] == row["expected"]["status"], (sid, {k: v for k, v in item["fields"].items() if v["result"] != "match"})


WRONG = [r["id"] for r in MATRIX["rows"] if r["group"] == "negative" and r["kind"] == "wrong_final_at_real_span"]


@pytest.mark.parametrize("nid", WRONG)
def test_a_wrong_final_value_at_a_real_span_is_never_consistent(client, nid):
    neg = ROWS[nid]
    base = ROWS[neg["of"]]
    final = copy.deepcopy(base["final"])
    for key, value in neg["change"].items():
        final[key.split(".", 1)[1]] = value
    item = answer(client, request_of(base, final))
    assert item["status"] == neg["expected"]["status"], (nid, {k: v for k, v in item["fields"].items() if v["result"] != "match"})


def test_monthly_flow_series_points_and_their_evidence(client):
    for sid in ("S01", "S02", "S03"):
        item = answer(client, request_of(ROWS[sid]))
        assert item["status"] == "consistent"
        assert item["fields"]["measure_kind"]["reason"] == "event_in_interval"
        assert item["fields"]["scope"]["reason"] == "object_metric_total"
        assert item["fields"]["period"]["reason"] in ("period_group", "period_before")


def test_s19_overlapping_spans_and_the_interval_is_not_cumulation(client):
    row = ROWS["S19"]
    f = row["fields"]
    assert f["metric"]["start"] <= f["object"]["start"] and f["object"]["end"] <= f["metric"]["end"]
    assert answer(client, request_of(row))["status"] == "consistent"
    final = dict(row["final"], measure_kind="cumulative")
    item = answer(client, request_of(row, final))
    assert item["status"] != "consistent" and item["fields"]["measure_kind"]["reason"] == "no_cumulative_wording"


# ---- synthetic structure: headings, tables, periods, decimals ---------------------------------------------------

def synthetic(text, quote, fields, start=1000):
    at = text.index(quote)
    return {"contract_version": "extraction_consistency.v2",
            "subject": {"source_id": "s", "record_id": "r", "version_id": "v", "raw_sha256": "a" * 64, "is_demo": True,
                        "artifact": {"processor_version": "t", "text_sha256": "b" * 64}},
            "passages": [{"id": "p1", "role": "primary", "start": start, "end": start + len(text), "text": text}],
            "items": [{"id": "m1", "kind": "measurement", "producer": {"name": "t", "version": "1"},
                       "locator": {"passage": "p1", "start": start + at, "end": start + at + len(quote)},
                       "quote": quote, "fields": fields}]}


def at(text, piece, n=0, start=1000):
    i = -1
    for _ in range(n + 1):
        i = text.index(piece, i + 1)
    return {"passage": "p1", "start": start + i, "end": start + i + len(piece)}


def fields_for(text, value, unit, period_text, period, kind="flow", obj=None, metric=None, scope=None,
               mode="unknown", qualifier="exact", modality="actual", n=0):
    st = lambda piece, val=None, k=0: {"state": "stated", "value": val or piece, "span": at(text, piece, k)}  # noqa: E731
    return {"object": st(obj) if obj else {"state": "unknown"}, "metric": st(metric) if metric else {"state": "unknown"},
            "value": st(value, None, n), "unit": st(unit) if unit else {"state": "unknown"},
            "period": {"state": "stated", "value": period, "span": at(text, period_text)} if period_text else {"state": "unknown"},
            "scope": st(scope) if scope else {"state": "unknown"}, "scope_mode": "stated" if scope else mode,
            "denominator": {"state": "unknown"}, "measure_kind": kind, "qualifier": qualifier, "modality": modality}


HEADED = ("# Anthrax – Kenya\n\n## Dengeza Health Post\n\n"
          "From September 2022 to January 2023, 42 suspected cases were recorded.\n\n"
          "## Other sites\n\n"
          "From September 2022 to January 2023, 17 suspected cases were recorded.\n")


def test_heading_scope_counts_only_inside_its_own_section(client):
    period = {"type": "interval", "precision": "month", "start": "2022-09", "end": "2023-01"}
    own = fields_for(HEADED, "42", "cases", "September 2022 to January 2023", period, scope="Dengeza Health Post",
                     metric="suspected cases")
    quote = "42 suspected cases were recorded"
    item = answer(client, synthetic(HEADED, quote, own))
    assert item["fields"]["scope"]["reason"] == "scope_heading", item["fields"]["scope"]
    # the sibling section: the same heading is no longer an ancestor once "## Other sites" opens
    sib = fields_for(HEADED, "17", "cases", "September 2022 to January 2023", period, scope="Dengeza Health Post",
                     metric="suspected cases")
    sib["period"]["span"] = at(HEADED, "September 2022 to January 2023", 1)
    sib["metric"]["span"] = at(HEADED, "suspected cases", 1)
    item = answer(client, synthetic(HEADED, "17 suspected cases were recorded", sib))
    assert item["fields"]["scope"]["result"] == "unsupported"


def test_a_heading_listing_several_places_proves_no_scope(client):
    text = "## Kenya, Uganda\n\nIn 2023年 the posts reported 5 cases.\n"
    text = "## Kenya, Uganda\n\n2023年4月，报告病例5例。\n"
    f = fields_for(text, "5", "例", "2023年4月", {"type": "interval", "precision": "month", "value": "2023-04"},
                   scope="Kenya", metric="报告病例")
    item = answer(client, synthetic(text, "报告病例5例", f))
    assert item["fields"]["scope"]["result"] == "unsupported"


def test_a_planned_heading_overrules_an_actual_sentence(client):
    text = "# 2026年建设计划\n\n2026年4月，国家药监局共批准注册医疗器械产品318个。\n"
    base = dict(obj="国家药监局", metric="批准注册医疗器械产品", mode="object_metric_total")
    period = {"type": "interval", "precision": "month", "value": "2026-04"}
    actual = fields_for(text, "318", "个", "2026年4月", period, **base)
    assert answer(client, synthetic(text, "国家药监局共批准注册医疗器械产品318个", actual))["fields"]["modality"]["result"] == "mismatch"
    planned = fields_for(text, "318", "个", "2026年4月", period, modality="planned", **base)
    assert answer(client, synthetic(text, "国家药监局共批准注册医疗器械产品318个", planned))["fields"]["modality"]["result"] == "match"


def test_point_and_interval_of_the_same_month_differ():
    point, _ = v4.canonical_period("截至2023年12月")
    interval, _ = v4.canonical_period("2023年12月")
    assert point["value"] == interval["value"] == "2023-12" and point != interval
    assert v4.canonical_period("截至2020年12月31日")[0] == {"type": "point", "precision": "day", "value": "2020-12-31"}


@pytest.mark.parametrize("text,expected", [
    ("22 December to 11 January 2021", {"type": "interval", "precision": "day", "start": "2020-12-22", "end": "2021-01-11"}),
    ("from September 2022 to January 2023", {"type": "interval", "precision": "month", "start": "2022-09", "end": "2023-01"}),
    ("1 February to 29 February 2024", {"type": "interval", "precision": "day", "start": "2024-02-01", "end": "2024-02-29"}),
    ("2023年前5月", {"type": "interval", "precision": "month", "start": "2023-01", "end": "2023-05"}),
    ("2021年上半年", {"type": "interval", "precision": "half", "value": "2021H1"}),
    ("2025年第三季度", {"type": "interval", "precision": "quarter", "value": "2025Q3"}),
])
def test_canonical_periods(text, expected):
    assert v4.canonical_period(text) == (expected, None)


@pytest.mark.parametrize("text,reason", [
    ("1 February to 29 February 2023", "invalid_date"), ("As of 6 October", "period_year_not_stated"),
    ("近3年", "relative_period"), ("11 February 2020 to 22 January 2020", "range_ends_before_it_starts"),
])
def test_periods_refused(text, reason):
    assert v4.canonical_period(text) == (None, reason)


def test_past_n_days_must_match_the_stated_dates(client):
    text = "In the past 21 days (22 January to 11 February 2020), 12 confirmed cases were reported in Beni."
    period = {"type": "interval", "precision": "day", "start": "2020-01-22", "end": "2020-02-11"}
    good = fields_for(text, "12", "cases", "22 January to 11 February 2020", period, scope="Beni", metric="confirmed cases")
    assert answer(client, synthetic(text, "12 confirmed cases were reported in Beni", good))["fields"]["period"]["result"] == "match"
    wrong = text.replace("past 21 days", "past 20 days")
    bad = fields_for(wrong, "12", "cases", "22 January to 11 February 2020", period, scope="Beni", metric="confirmed cases")
    item = answer(client, synthetic(wrong, "12 confirmed cases were reported in Beni", bad))
    assert item["fields"]["period"]["reason"] == "past_n_days_disagrees_with_dates"


def test_other_numbers_of_an_english_paragraph_do_not_reach_the_value(client):
    text = ("Between 1 and 9 May 2024, 300 samples were tested. From September 2022 to January 2023, "
            "42 suspected cases of anthrax were recorded at the Dengeza Health Post.")
    period = {"type": "interval", "precision": "month", "start": "2022-09", "end": "2023-01"}
    f = fields_for(text, "42", "cases", "September 2022 to January 2023", period, scope="Dengeza Health Post",
                   metric="suspected cases of anthrax", obj="anthrax")
    item = answer(client, synthetic(text, "42 suspected cases of anthrax were recorded at the Dengeza Health Post", f))
    assert item["status"] == "consistent", {k: v for k, v in item["fields"].items() if v["result"] != "match"}


def test_a_monthly_stock_is_not_a_flow(client):
    text = "2026年4月，国家药监局在册医疗器械产品共318个。"
    f = fields_for(text, "318", "个", "2026年4月", {"type": "interval", "precision": "month", "value": "2026-04"},
                   obj="国家药监局", metric="在册医疗器械产品", mode="object_metric_total")
    item = answer(client, synthetic(text, "国家药监局在册医疗器械产品共318个", f))
    assert item["fields"]["measure_kind"]["result"] != "match"


def test_a_total_word_alone_is_not_cumulation(client):
    text = "As of 19 May 2020, ICH reported a total of 3462 cases."
    f = fields_for(text, "3462", "cases", "As of 19 May 2020", {"type": "point", "precision": "day", "value": "2020-05-19"},
                   kind="cumulative", obj="ICH")
    item = answer(client, synthetic(text, "a total of 3462 cases", f))
    assert item["fields"]["measure_kind"]["reason"] == "no_cumulative_wording"


def test_table_header_of_the_value_column(client):
    text = ("2026年4月医疗器械批准情况\n| 类别 | 批准注册产品数 |\n| --- | --- |\n| 境内第三类 | 318 |\n")
    f = fields_for(text, "318", "", "", None, metric="批准注册产品数")
    item = answer(client, synthetic(text, "318", f))
    assert item["fields"]["metric"]["reason"] == "header_of_value_column"
    two = text + "\n| 类别 | 注销产品数 |\n| --- | --- |\n| 境内第三类 | 9 |\n"
    g = fields_for(two, "9", "", "", None, metric="批准注册产品数")
    assert answer(client, synthetic(two, "9 |", g))["fields"]["metric"]["result"] == "mismatch"


@pytest.mark.parametrize("value,ok", [("123456789012345678", True), ("1234567890123456789", False),
                                      ("1.123456", True), ("1.1234567", False), ("1,444", False), ("0318", False),
                                      ("318.0", False), ("-3", True)])
def test_decimal_strings_on_the_wire(client, value, ok):
    text = f"2026年4月，国家药监局共批准注册医疗器械产品{value}个。"
    f = fields_for(text, value, "个", "2026年4月", {"type": "interval", "precision": "month", "value": "2026-04"},
                   obj="国家药监局", metric="批准注册医疗器械产品", mode="object_metric_total")
    r = post(client, synthetic(text, f"国家药监局共批准注册医疗器械产品{value}个", f))
    assert (r.status_code == 200) == ok, r.text
    if ok:
        assert r.json()["items"][0]["fields"]["value"]["result"] == "match"


def test_v1_is_unchanged(client):
    r = client.post("/v1/extraction-consistency", json={"subject": {}}, headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code in (400, 422)
