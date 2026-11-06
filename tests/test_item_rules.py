"""Contract v1 (design v3 §6.2): a product corresponds to its fixed passages field by field, each field at
its own span and tied to the same quantity, or it does not; what the rules cannot prove stays unchecked.
Texts are short real-shaped passages: an NMPA sampling table (header + row), trial result sentences."""
import copy

import pytest
from conftest import FakeLLM
from fastapi.testclient import TestClient

from app import item_rules
from app.service import create_app

TOKEN = "test-token"
SUBJECT = {"source_id": "nmpa", "record_id": "R1", "version_id": "v1", "raw_sha256": "a" * 64, "is_demo": True,
           "artifact": {"processor_version": "datafabric.clean_text.v11", "text_sha256": "b" * 64}}
HEADER = ("| 序号 | 药品品名 | 标示生产企业名称 | 药品规格 | 生产批号 | 检品来源 | 检验依据 | 检验结果 | 不合格项目 |\n"
          "| ---- | ---- | ---- | ---- | ---- | ---- | ---- | ---- | ---- |")
ROW = ("| 954 | 利巴韦林片 | 广东华南药业集团有限公司 | 100mg | 191003 | 广宁县医药有限公司 | "
       "《中国药典》2015年版二部 | 合格 | |")
CN = "在入组的120例患者中，客观缓解32例（26.7%），中位随访12个月。"
RATE = "成人患者客观缓解率为26.7%（32/120），中位随访12个月。"
AGES = "成人10人中2人有效；儿童100人中80人有效。"


class Doc:
    """Passages of one artifact at fixed offsets, and spans found by text (n-th occurrence)."""

    def __init__(self, **texts):
        self.passages, at = [], 1000
        for i, (pid, text) in enumerate(texts.items()):
            self.passages.append({"id": pid, "role": "primary" if i == len(texts) - 1 else "context",
                                  "start": at, "end": at + len(text), "text": text})
            at += len(text) + 500

    def span(self, pid, piece, n=0, inside=None):
        p = next(p for p in self.passages if p["id"] == pid)
        at, lo = -1, 0
        if inside:
            lo = p["text"].index(inside)
        for _ in range(n + 1):
            at = p["text"].index(piece, max(lo, at + 1))
        return {"passage": pid, "start": p["start"] + at, "end": p["start"] + at + len(piece)}

    def field(self, pid, piece, n=0, value=None, inside=None):
        return {"state": "stated", "value": value or piece, "span": self.span(pid, piece, n, inside)}


UNKNOWN = {"state": "unknown"}


def measurement(doc, pid, quote, kind="rate", **fields):
    return {"id": "m1", "kind": "measurement", "producer": {"name": "test", "version": "1"},
            "locator": doc.span(pid, quote), "quote": quote,
            "fields": {"object": UNKNOWN, "metric": UNKNOWN, "unit": UNKNOWN, "period": UNKNOWN,
                       "scope": UNKNOWN, "denominator": UNKNOWN, **fields, "measure_kind": kind}}


def run(doc, *items):
    out = item_rules.check({"subject": SUBJECT, "passages": doc.passages, "items": list(items)})
    assert out["llm_calls"] == [] and out["checker_version"] == item_rules.CHECKER_VERSION
    return out["items"]


def results(item):
    return {k: v["result"] for k, v in item["fields"].items()}


def test_a_percentage_tied_to_its_stated_ratio_is_consistent():
    d = Doc(p=RATE)
    quote = "成人患者客观缓解率为26.7%（32/120）"
    m = measurement(d, "p", quote, object=d.field("p", "客观缓解率"), metric=d.field("p", "客观缓解率"),
                    value=d.field("p", "26.7"), unit=d.field("p", "%"), scope=d.field("p", "成人患者"),
                    denominator=d.field("p", "120"))
    (got,) = run(d, m)
    assert got["status"] == "consistent", got
    assert got["fields"]["denominator"]["reason"] == "percentage_of_stated_ratio"
    # the same percentage with its population in another group of the clause: not provable, so unchecked
    d = Doc(p=CN)
    m = measurement(d, "p", "在入组的120例患者中，客观缓解32例（26.7%）", object=d.field("p", "客观缓解"),
                    metric=d.field("p", "客观缓解"), value=d.field("p", "26.7"), unit=d.field("p", "%"),
                    scope=d.field("p", "患者"), denominator=d.field("p", "120"))
    (got,) = run(d, m)
    assert got["fields"]["scope"]["reason"] == "scope_not_in_value_group" and got["status"] == "unchecked"


def test_fields_of_another_group_are_rejected_not_matched_by_the_sentence():
    d = Doc(p=AGES)
    wrong = measurement(d, "p", "成人10人中2人有效", kind="stock", value=d.field("p", "2"),
                        unit=d.field("p", "人", n=2), denominator=d.field("p", "100"), scope=d.field("p", "儿童"))
    wrong["locator"], wrong["quote"] = d.span("p", AGES.rstrip("。")), AGES.rstrip("。")
    (got,) = run(d, wrong)
    assert got["status"] == "inconsistent"
    assert got["fields"]["denominator"]["reason"] == "the_value_has_another_denominator"
    assert got["fields"]["scope"]["result"] == "unsupported"  # 儿童 is not in the value's clause
    right = dict(wrong, fields=dict(wrong["fields"], denominator=d.field("p", "10"), scope=d.field("p", "成人")))
    (got,) = run(d, right)
    assert results(got)["denominator"] == results(got)["scope"] == results(got)["unit"] == "match"
    assert got["status"] == "unchecked"  # measure_kind "stock" is beyond the rules: never "consistent"


def test_a_table_value_is_tied_to_its_own_column_header():
    d = Doc(r=HEADER + "\n" + ROW)  # header and row in one passage: one table
    apart = Doc(h=HEADER, r=ROW)  # two slices cannot prove they are one table
    m = measurement(apart, "r", "100mg", kind="stock", metric=apart.field("h", "药品规格"),
                    value=apart.field("r", "100"), unit=apart.field("r", "mg"))
    (got,) = run(apart, m)
    assert got["fields"]["metric"]["reason"] == "header_table_not_provable"
    base = {"object": d.field("r", "利巴韦林片"), "value": d.field("r", "100"), "unit": d.field("r", "mg")}
    good = measurement(d, "r", "100mg", kind="stock", metric=d.field("r", "药品规格"), **base)
    (got,) = run(d, good)
    assert got["fields"]["metric"] == {"result": "match", "reason": "header_of_value_column",
                                       "span": d.span("r", "药品规格")}
    assert got["fields"]["object"]["result"] == "match" and got["fields"]["unit"]["result"] == "match"
    # "2015年版" in the row is a pharmacopoeia edition, not proof against an unknown period
    assert got["fields"]["period"]["result"] == "unsupported"
    wrong = measurement(d, "r", "100mg", kind="stock", metric=d.field("r", "生产批号"), **base)
    (got,) = run(d, wrong)
    assert got["fields"]["metric"]["reason"] == "header_of_another_column" and got["status"] == "inconsistent"


def test_wrong_value_unit_and_unstated_unit_are_caught_exactly():
    d = Doc(r=ROW)
    (got,) = run(d, measurement(d, "r", "100mg", value=d.field("r", "100", value="10")))
    assert got["fields"]["value"]["reason"] == "value_differs_from_span"
    (got,) = run(d, measurement(d, "r", "100mg", value=d.field("r", "100", value="100.0")))
    assert got["fields"]["value"]["result"] == "match"  # the same number, written exactly
    (got,) = run(d, measurement(d, "r", "100mg", value=d.field("r", "100"), unit=d.field("r", "mg", value="g")))
    assert got["fields"]["unit"]["result"] == "mismatch" and got["status"] == "inconsistent"
    (got,) = run(d, measurement(d, "r", "100mg", value=d.field("r", "100")))  # unit said unknown
    assert got["fields"]["unit"]["reason"] == "unit_written_at_value:mg"
    d2 = Doc(p="首剂2 mg，之后2 mg/kg维持。")
    (got,) = run(d2, measurement(d2, "p", "2 mg/kg", value=d2.field("p", "2", n=1), unit=d2.field("p", "mg")))
    assert got["fields"]["unit"]["result"] == "mismatch"  # the first "mg", not the unit at this number


def test_denominators_only_from_this_numbers_ratio():
    d = Doc(p="Of the adults enrolled, 32 of 120 responded by 2021-06-30.")
    (got,) = run(d, measurement(d, "p", "32 of 120 responded", value=d.field("p", "32")))
    assert got["fields"]["denominator"]["reason"] == "the_value_has_a_stated_denominator"
    (got,) = run(d, measurement(d, "p", "32 of 120 responded", value=d.field("p", "32"),
                                denominator=d.field("p", "120"), period=d.field("p", "2021-06-30"),
                                scope=d.field("p", "adults")))
    assert results(got)["denominator"] == results(got)["period"] == "match"
    # "adults" is in the group before the comma: the rules cannot prove it is this quantity's population
    assert got["fields"]["scope"]["reason"] == "scope_not_in_value_group" and got["status"] == "unchecked"
    d2 = Doc(p="截至2021/6/30，报告32例。")
    (got,) = run(d2, measurement(d2, "p", "报告32例", value=d2.field("p", "32")))
    assert got["fields"]["denominator"]["result"] == "match"  # a slash date is no ratio
    d3 = Doc(p="每日剂量20 mg/day，连续给药。")
    (got,) = run(d3, measurement(d3, "p", "20 mg/day", value=d3.field("p", "20"), unit=d3.field("p", "mg/day")))
    assert results(got)["unit"] == results(got)["denominator"] == "match"  # mg/day is a unit, not a ratio


def test_a_wrong_locator_or_edited_quote_reads_no_field():
    d = Doc(r=ROW)
    good = measurement(d, "r", "100mg", value=d.field("r", "100"), unit=d.field("r", "mg"))
    shifted = copy.deepcopy(good)
    shifted["locator"]["start"] += 1
    shifted["locator"]["end"] += 1
    (got,) = run(d, shifted)
    assert got["fields"]["locator"]["reason"] == "quote_elsewhere_in_passages" and got["status"] == "inconsistent"
    assert got["fields"]["value"]["result"] == "unsupported"
    (got,) = run(d, dict(good, quote="200mg"))
    assert got["fields"]["locator"]["reason"] == "quote_not_in_passages"


def test_identifiers_only_as_written_claims_and_annotations_stay_unchecked():
    d = Doc(p="Trial registration: ClinicalTrials.gov NCT 04165083; first posted 2019-11-18.")
    ident = {"id": "i1", "kind": "identifier", "producer": {"name": "identifiers", "version": "v2"},
             "locator": d.span("p", "NCT 04165083"), "quote": "NCT 04165083",
             "fields": {"scheme": "nct", "identifier": d.field("p", "NCT 04165083")}}
    normalized = copy.deepcopy(ident)
    normalized["fields"]["identifier"]["value"] = "NCT04165083"
    wrong = copy.deepcopy(ident)
    wrong["fields"]["identifier"]["value"] = "NCT 04165038"
    claim = {"id": "c1", "kind": "claim", "producer": {"name": "x", "version": "1"},
             "locator": d.span("p", "first posted 2019-11-18"), "quote": "first posted 2019-11-18",
             "fields": {"statement": d.field("p", "first posted 2019-11-18"), "polarity": "affirmed"}}
    note = {"id": "a1", "kind": "annotation", "producer": {"name": "profile", "version": "v5"},
            "locator": d.span("p", "Trial registration"), "quote": "Trial registration",
            "fields": {"label": "human"}}
    got = {i["id"]: i for i in run(d, ident, dict(normalized, id="i2"), dict(wrong, id="i3"), claim, note)}
    assert got["i1"]["status"] == "consistent"
    assert got["i2"]["fields"]["identifier"]["reason"] == "normalized_form_not_verifiable_in_v1"
    assert got["i2"]["status"] == "unchecked" and got["i3"]["status"] == "inconsistent"
    assert got["c1"]["status"] == "unchecked" and got["c1"]["fields"]["polarity"]["result"] == "unsupported"
    assert got["a1"]["status"] == "unchecked"


@pytest.fixture
def http(monkeypatch):
    monkeypatch.setenv("SERVICE_TOKEN", TOKEN)
    llm = FakeLLM()
    return TestClient(create_app(llm)), llm


def test_http_contract_is_strict_and_calls_no_model(http):
    client, llm = http
    auth = {"Authorization": "Bearer " + TOKEN}
    d = Doc(h=HEADER, r=ROW)
    item = measurement(d, "r", "100mg", kind="stock", object=d.field("r", "利巴韦林片"),
                       metric=d.field("h", "药品规格"), value=d.field("r", "100"), unit=d.field("r", "mg"))
    body = {"subject": SUBJECT, "passages": d.passages, "items": [item]}
    response = client.post("/v1/extraction-consistency", json=body, headers=auth)
    assert response.status_code == 200 and llm.payloads == []
    assert response.json()["contract_version"] == "extraction_consistency.v1"

    def broken(change):
        b = copy.deepcopy(body)
        change(b)
        return b

    for b in (
        broken(lambda b: b.update(extra=1)),
        broken(lambda b: b["subject"].pop("is_demo")),
        broken(lambda b: b["items"][0]["fields"].pop("scope")),                          # a dimension left out
        broken(lambda b: b["items"][0]["fields"].update(dose={"state": "unknown"})),      # unknown field
        broken(lambda b: b["items"][0]["fields"]["unit"].pop("span")),                    # stated without span
        broken(lambda b: b["items"][0]["fields"].update(scope={"state": "unknown", "value": "x"})),
        broken(lambda b: b["items"][0].update(kind="identifier")),                        # fields of another kind
        broken(lambda b: b["passages"][0].update(end=b["passages"][0]["end"] + 1)),       # range != text
        broken(lambda b: b["passages"][1].update(start=b["passages"][0]["start"] + 5,
                                                 end=b["passages"][0]["start"] + 5 + len(ROW))),  # overlap
        broken(lambda b: b["items"][0]["fields"]["value"]["span"].update(passage="zz")),  # no such passage
        broken(lambda b: b.update(items=[])),
    ):
        response = client.post("/v1/extraction-consistency", json=b, headers=auth)
        assert response.status_code == 400 and response.json()["error_code"] == "invalid_request", b
    assert client.post("/v1/extraction-consistency", json=body).status_code == 401
