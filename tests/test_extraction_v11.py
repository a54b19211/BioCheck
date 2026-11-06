"""Checker v11 under contract v3 (Codex 730/740): v10's fields and status unchanged, and each stated dimension
judged on three things from the text - its literal, its relation to the value (same sentence without another
quantity between - nor a subset mark or a later quantity after it - or an effective ancestor heading) and
evidence of the role its kind names. Role mismatches (an institution as a product or disease, a date value as a
date basis, a number as a denominator definition) and dimensions elsewhere are unsupported while the value
itself stays consistent."""

import pytest
from test_extraction_v4 import TOKEN, at, client, fields_for, synthetic  # noqa: F401

from app import extraction_v5 as v11

PERIOD = {"type": "interval", "precision": "month", "value": "2026-01"}


def post(client, body):  # noqa: F811
    return client.post("/v3/extraction-consistency", json=body, headers={"Authorization": f"Bearer {TOKEN}"})


def body(text, quote, dims, metric="报告炭疽确诊病例", scope=None):
    mode = "stated" if scope else "object_metric_total"
    f = fields_for(text, "10", "例", "2026年1月", PERIOD, obj="医院A", metric=metric, scope=scope, mode=mode)
    f["unit"]["span"] = at(text, "例", 1)  # the 例 after the value (the first is in 病例)
    b = synthetic(text, quote, f)
    b["contract_version"] = "extraction_consistency.v3"
    b["items"][0]["dims"] = [{"kind": k, "state": "stated", "value": v, "span": at(text, v)} for k, v in dims]
    b["items"][0]["family"] = "case_count"
    return b


def answer(client, b):  # noqa: F811
    r = post(client, b)
    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["contract_version"], out["checker_version"]) == ("extraction_consistency.v3", v11.CHECKER_VERSION)
    assert v11.CHECKER_VERSION == "biocheck.extraction_rules.v13"  # v11 on e98b392; its tests run unchanged here
    return out["items"][0]


FULL = "## 按报告日统计\n\n2026年1月，医院A在湖北省共报告成人炭疽确诊病例10例。"
FULL_Q = "2026年1月，医院A在湖北省共报告成人炭疽确诊病例10例"
PLAIN = "2026年1月，医院A共报告炭疽确诊病例10例。"


def test_dimensions_written_with_their_role_by_the_value_match_and_the_status_is_v10s(client):  # noqa: F811
    item = answer(client, body(FULL, FULL_Q, [("disease", "炭疽"), ("population", "成人"),
                                              ("date_basis", "按报告日统计")], metric="报告成人炭疽确诊病例",
                               scope="在湖北省"))
    assert item["status"] == "consistent"
    assert {k: (r["result"], r["reason"]) for k, r in item["dims"].items()} == {
        "disease": ("match", "dim_in_value_sentence"),
        "population": ("match", "dim_in_value_sentence"),
        "date_basis": ("match", "dim_in_heading_ancestor"),
    }
    assert item["dims"]["disease"]["span"] == at(FULL, "炭疽")


@pytest.mark.parametrize("kind,value", [
    ("product_or_class", "医院A"),  # an institution is no product
    ("disease", "医院A"),  # nor a disease
    ("date_basis", "2026年1月"),  # a date value is no date basis
    ("denominator_definition", "10"),  # a number is no denominator definition
    ("population", "炭疽"),  # a disease is no population
])
def test_a_role_without_evidence_in_the_text_is_unsupported_and_the_value_stays_consistent(client, kind,
                                                                                           value):  # noqa: F811
    item = answer(client, body(PLAIN, PLAIN[:-1], [(kind, value)]))
    assert item["status"] == "consistent"
    assert (item["dims"][kind]["result"], item["dims"][kind]["reason"]) == ("unsupported", "dim_kind_not_proven")


def test_a_dimension_in_another_sentence_or_of_another_quantity_is_not_related(client):  # noqa: F811
    text = "人群：儿童。" + PLAIN
    item = answer(client, body(text, PLAIN[:-1], [("population", "儿童")]))
    assert item["dims"]["population"]["reason"] == "dim_relation_not_rule_checkable"
    sub = "2026年1月，医院A共报告炭疽确诊病例10例，其中儿童3例。"
    item = answer(client, body(sub, sub[:-1], [("population", "儿童")]))
    assert item["dims"]["population"]["reason"] == "dim_relation_not_rule_checkable"  # 儿童 is the 3's


def test_a_dimension_value_not_written_at_its_span_is_a_mismatch(client):  # noqa: F811
    b = body(PLAIN, PLAIN[:-1], [("disease", "炭疽")])
    b["items"][0]["dims"][0]["value"] = "霍乱"
    item = answer(client, b)
    assert (item["dims"]["disease"]["result"], item["dims"]["disease"]["reason"]) == (
        "mismatch",
        "dim_differs_from_span",
    )


def test_one_dimension_per_kind_and_known_kinds_only(client):  # noqa: F811
    b = body(PLAIN, PLAIN[:-1], [("disease", "炭疽"), ("disease", "炭疽")])
    assert post(client, b).status_code == 400
    b = body(PLAIN, PLAIN[:-1], [("disease", "炭疽")])
    b["items"][0]["dims"][0]["kind"] = "mood"
    assert post(client, b).status_code == 400


def test_v2_stays_strict_and_unchanged(client):  # noqa: F811
    b = body(PLAIN, PLAIN[:-1], [("disease", "炭疽")])
    b["contract_version"] = "extraction_consistency.v2"
    headers = {"Authorization": f"Bearer {TOKEN}"}
    assert client.post("/v2/extraction-consistency", json=b, headers=headers).status_code == 400  # no dims in v2
    b["items"][0].pop("dims")
    b["items"][0].pop("family")
    r = client.post("/v2/extraction-consistency", json=b, headers=headers)
    assert r.status_code == 200 and r.json()["checker_version"] == "biocheck.extraction_rules.v10"
    assert "dims" not in r.json()["items"][0]


def test_v11_reads_a_single_letter_in_the_metric_subject(client):  # noqa: F811
    text = "In 2018, Hospital B in total reported 23 hepatitis E cases."
    f = fields_for(text, "23", "cases", "In 2018", {"type": "interval", "precision": "year", "value": "2018"},
                   obj="Hospital B", metric="hepatitis E cases", mode="object_metric_total")
    b = synthetic(text, text, f)
    b["contract_version"] = "extraction_consistency.v3"
    assert answer(client, b)["fields"]["unit"]["result"] == "match"  # v10 cuts at the letter E
