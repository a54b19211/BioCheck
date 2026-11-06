import copy

import pytest
from conftest import FakeLLM
from fastapi.testclient import TestClient
from test_item_rules import SUBJECT, Doc, measurement

from app.service import create_app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("SERVICE_TOKEN", "test-token")
    return TestClient(create_app(FakeLLM()))


def checked(c, d, it):
    response = c.post(
        "/v1/extraction-consistency",
        json={"subject": SUBJECT, "passages": d.passages, "items": [it]},
        headers={"Authorization": "Bearer test-token"},
    )
    assert response.status_code == 200, response.text
    return response.json()["items"][0]


def rate(d, number="100", metric="响应率", object="药物A", scope="成人", unit="%"):
    return measurement(
        d,
        "p",
        number + unit,
        object=d.field("p", object),
        metric=d.field("p", metric),
        value=d.field("p", number),
        unit=d.field("p", unit, inside=number + unit),
        scope=d.field("p", scope),
    )


def test_an_identifier_differing_only_in_punctuation_is_not_consistent(client):
    raw = "10.12345/678"
    d = Doc(p=raw)
    i = {
        "id": "i1",
        "kind": "identifier",
        "producer": {"name": "test", "version": "1"},
        "locator": d.span("p", raw),
        "quote": raw,
        "fields": {"scheme": "doi", "identifier": d.field("p", raw)},
    }
    assert checked(client, d, i)["status"] == "consistent"
    i["fields"]["identifier"]["value"] = "10.1234/5678"
    assert checked(client, d, i)["status"] != "consistent"


def group_item(d, denominator="10", scope="成人"):
    return measurement(
        d,
        "p",
        "2人有效",
        object=d.field("p", "药物A"),
        metric=d.field("p", "有效"),
        value=d.field("p", "2"),
        unit=d.field("p", "人", inside="2人"),
        denominator=d.field("p", denominator),
        scope=d.field("p", scope),
    )


def test_a_denominator_or_scope_of_another_semicolon_group_is_not_consistent(client):
    d = Doc(p="药物A成人10人中2人有效；儿童100人中80人有效。")
    assert checked(client, d, group_item(d))["status"] == "consistent"
    assert checked(client, d, group_item(d, "100", "儿童"))["status"] != "consistent"


@pytest.mark.parametrize("written,claimed,trim", [("100", "0", 2), ("-10", "10", 1)])
def test_value_span_must_cover_a_whole_signed_number(client, written, claimed, trim):
    d = Doc(p="地区甲产业增长率" + written + "%。")
    i = rate(d, written, metric="增长率", object="产业", scope="地区甲")
    assert checked(client, d, i)["status"] == "consistent"
    wrong = copy.deepcopy(i)
    wrong["fields"]["value"]["span"]["start"] += trim
    wrong["fields"]["value"]["value"] = claimed
    got = checked(client, d, wrong)
    assert got["status"] != "consistent", got


def test_header_of_another_table_is_not_the_value_header(client):
    h1 = "| 企业 | 死亡率 |\n| --- | --- |\n"
    h2 = "| 企业 | 增长率 |\n| --- | --- |\n"
    d = Doc(p=h1 + "| 甲 | 10% |\n\n" + h2 + "| 乙 | 20% |")
    i = rate(d, "20", metric="增长率", object="乙", scope="乙")
    assert checked(client, d, i)["status"] == "consistent"
    wrong = copy.deepcopy(i)
    wrong["fields"]["metric"] = d.field("p", "死亡率")
    got = checked(client, d, wrong)
    assert got["status"] != "consistent", got


def test_no_denominator_is_not_positive_evidence_of_rate(client):
    d = Doc(p="成人药物A响应率100%。")
    assert checked(client, d, rate(d))["status"] == "consistent"
    d = Doc(p="成人药物A剂量100mg。")
    wrong = rate(d, metric="剂量", unit="mg")
    got = checked(client, d, wrong)
    assert got["fields"]["measure_kind"]["result"] != "match", got


def test_scope_cannot_be_borrowed_across_comma_separated_groups(client):
    one = Doc(p="药物A成人10人中2人有效。")
    assert checked(client, one, group_item(one))["status"] == "consistent"
    d = Doc(p="药物A成人10人中2人有效，儿童100人中80人有效。")
    got = checked(client, d, group_item(d, "10", "儿童"))
    assert got["status"] != "consistent", got
