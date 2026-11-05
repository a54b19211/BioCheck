"""Extraction consistency (v3 6.2): the product corresponds to its passage or it does not; what the rules cannot
decide stays unchecked. Texts are short real-shaped passages: an NMPA sampling-table row, a trial result
sentence with a stated denominator, a registry identifier line."""
import pytest
from conftest import FakeLLM
from fastapi.testclient import TestClient

from app import extraction
from app.service import create_app

TOKEN = "test-token"
ROW = ("| 954 | 利巴韦林片 | 广东华南药业集团有限公司 | 100mg | 191003 | 广宁县医药有限公司 | "
       "《中国药典》2015年版二部 | 合格 | |")
TRIAL = ("在入组的120例患者中，客观缓解32例（26.7%），中位随访12个月。"
         "Of the adults enrolled, 32 of 120 responded by 2021-06-30.")
REGISTRY = "Trial registration: ClinicalTrials.gov NCT 04165083; first posted 2019-11-18."
SUBJECT = {"source_id": "nmpa", "record_id": "R1", "version_id": "v1", "raw_sha256": "a" * 64,
           "artifact": {"processor_version": "datafabric.clean_text.v11", "text_sha256": "b" * 64}}


def request(text, *items, start=1000):
    return {"subject": SUBJECT, "passage": {"start": start, "end": start + len(text), "text": text},
            "items": list(items)}


def item(text, quote, kind="measurement", start=1000, occurrence=0, **fields):
    at = -1
    for _ in range(occurrence + 1):
        at = text.index(quote, at + 1)
    return {"id": f"i{at}", "kind": kind, "producer": {"name": "test", "version": "1"},
            "locator": {"start": start + at, "end": start + at + len(quote)}, "quote": quote,
            "fields": fields}


def one(text, it):
    out = extraction.check(request(text, it))
    assert out["llm_calls"] == [] and out["checker_version"] == extraction.CHECKER_VERSION
    return out["items"][0]


def test_a_correct_measurement_is_consistent_field_by_field():
    got = one(ROW, item(ROW, "100mg", value="100", unit="mg"))
    assert got["status"] == "consistent"
    assert {k: v["result"] for k, v in got["fields"].items()} == {"locator": "match", "quote": "match",
                                                                  "value": "match", "unit": "match"}
    got = one(TRIAL, item(TRIAL, "客观缓解32例（26.7%）", value="26.7", unit="%", numerator="32", denominator="120"))
    assert got["status"] == "consistent", got


def test_wrong_number_unit_denominator_numerator_are_each_caught():
    cases = [
        (dict(value="10", unit="mg"), "value", "not_found"),
        (dict(value="100", unit="g"), "unit", "mismatch"),             # 100 mg reported as 100 g
        (dict(value="100", unit="mg/kg"), "unit", "mismatch"),
    ]
    for fields, name, expected in cases:
        got = one(ROW, item(ROW, "100mg", **fields))
        assert got["status"] == "inconsistent" and got["fields"][name]["result"] == expected, (fields, got)
    got = one(TRIAL, item(TRIAL, "客观缓解32例（26.7%）", value="26.7", unit="%", denominator="100"))
    assert got["fields"]["denominator"]["result"] == "mismatch" and got["status"] == "inconsistent"
    got = one(TRIAL, item(TRIAL, "客观缓解32例（26.7%）", value="26.7", numerator="30"))
    assert got["fields"]["numerator"]["result"] == "mismatch"
    # "unknown" is only right when the source states no denominator
    got = one(TRIAL, item(TRIAL, "32 of 120 responded", value="32", denominator="unknown"))
    assert got["fields"]["denominator"] == {"result": "mismatch", "reason": "denominator_stated_in_source"}
    got = one(ROW, item(ROW, "100mg", value="100", unit="mg", denominator="unknown"))
    assert got["fields"]["denominator"]["result"] == "match" and got["status"] == "consistent"


def test_wrong_locator_or_quote_is_caught_and_stops_the_field_checks():
    good = item(ROW, "100mg", value="100", unit="mg")
    shifted = dict(good, locator={"start": good["locator"]["start"] + 1, "end": good["locator"]["end"] + 1})
    got = one(ROW, shifted)
    assert got["status"] == "inconsistent" and got["fields"]["locator"]["result"] == "mismatch"
    assert got["fields"]["quote"]["reason"] == "quote_elsewhere_in_passage"
    assert got["fields"]["value"]["result"] == "unsupported"  # never read from a wrong place
    edited = dict(good, quote="200mg")
    got = one(ROW, edited)
    assert got["fields"]["quote"] == {"result": "not_found", "reason": "quote_not_in_passage"}
    outside = dict(good, locator={"start": 10, "end": 15})
    got = one(ROW, outside)
    assert got["fields"]["locator"]["reason"] == "locator_outside_passage" and got["status"] == "inconsistent"


def test_wrong_period_and_population_are_caught_literal_only():
    base = item(TRIAL, "32 of 120 responded", value="32")
    assert one(TRIAL, dict(base, fields=dict(value="32", period="2021-06-30")))["status"] == "consistent"
    got = one(TRIAL, dict(base, fields=dict(value="32", period="2020-06-30")))
    assert got["fields"]["period"]["result"] == "mismatch" and got["status"] == "inconsistent"
    assert one(TRIAL, dict(base, fields=dict(value="32", population="adults")))["status"] == "consistent"
    got = one(TRIAL, dict(base, fields=dict(value="32", population="children")))
    assert got["fields"]["population"]["result"] == "not_found" and got["status"] == "inconsistent"


def test_what_rules_cannot_decide_is_unchecked_never_consistent():
    got = one(ROW, item(ROW, "100mg", value="100", unit="片剂规格单位"))
    assert got["fields"]["unit"]["reason"] == "unit_not_in_table" and got["status"] == "unchecked"
    got = one(TRIAL, item(TRIAL, "在入组的120例患者中", kind="claim", label="efficacy"))
    assert got["fields"]["label"]["result"] == "unsupported" and got["status"] == "unchecked"
    got = one(ROW, item(ROW, "100mg", value="约一百"))
    assert got["status"] == "unchecked"


def test_identifier_raw_and_normalized_value_are_compared_not_reparsed():
    good = item(REGISTRY, "NCT 04165083", kind="identifier",
                identifier={"scheme": "nct", "raw": "NCT 04165083", "value": "NCT04165083"})
    assert one(REGISTRY, good)["status"] == "consistent"
    wrong = dict(good, fields={"identifier": {"scheme": "nct", "raw": "NCT 04165083", "value": "NCT04165038"}})
    got = one(REGISTRY, wrong)
    assert got["fields"]["identifier"]["reason"] == "identifier_value_differs_from_source"


def test_a_slash_date_is_not_a_denominator():
    text = "截至2021/6/30，共报告病例32例。"
    got = one(text, item(text, "共报告病例32例", value="32", denominator="unknown"))
    # "共" states a total only with a number right after it; the date must not count as n/N
    assert got["fields"]["denominator"]["result"] == "match", got


@pytest.fixture
def http(monkeypatch):
    monkeypatch.setenv("SERVICE_TOKEN", TOKEN)
    llm = FakeLLM()
    return TestClient(create_app(llm)), llm


def auth():
    return {"Authorization": "Bearer " + TOKEN}


def test_http_contract_is_strict_and_calls_no_model(http):
    client, llm = http
    body = request(ROW, item(ROW, "100mg", value="100", unit="mg"))
    response = client.post("/v1/extraction-consistency", json=body, headers=auth())
    assert response.status_code == 200 and response.json()["items"][0]["status"] == "consistent"
    assert response.json()["contract_version"] == "extraction_consistency.v1" and llm.payloads == []
    for broken in (
        dict(body, extra_field=1),                                              # unknown top-level field
        dict(body, items=[dict(body["items"][0], fields={"value": "100", "dose": "x"})]),  # unknown field
        dict(body, items=[dict(body["items"][0], kind="identifier")]),          # identifier without it
        dict(body, items=[dict(body["items"][0], kind="annotation")]),          # fields not allowed for kind
        dict(body, passage=dict(body["passage"], end=body["passage"]["end"] + 1)),  # range != text
        dict(body, items=[]),
    ):
        response = client.post("/v1/extraction-consistency", json=broken, headers=auth())
        assert response.status_code == 400 and response.json()["error_code"] == "invalid_request", broken
    assert client.post("/v1/extraction-consistency", json=body).status_code == 401
