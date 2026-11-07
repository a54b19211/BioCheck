import json

import httpx
import pytest
from conftest import FakeLLM, request, supported
from fastapi.testclient import TestClient

from app.core import CheckBatch, CoverageOut
from app.llm import chat_json
from app.service import create_app

TOKEN = "test-token"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("SERVICE_TOKEN", TOKEN)
    def make(llm=None):
        return TestClient(create_app(FakeLLM() if llm is None else llm))

    return make


def auth(token=TOKEN):
    return {"Authorization": "Bearer " + token}


def test_health_is_open_and_operations_need_the_bearer_token(client):
    http = client()
    assert http.get("/health").json() == {"status": "ok"}
    for headers in ({}, auth("wrong")):
        response = http.post("/v1/verify", json={}, headers=headers)
        assert response.status_code == 401
        assert response.json() == {"error_code": "unauthorized", "message": response.json()["message"]}


def test_missing_service_token_refuses_instead_of_running_open(monkeypatch):
    monkeypatch.delenv("SERVICE_TOKEN", raising=False)
    response = TestClient(create_app(FakeLLM())).post("/v1/verify", json={}, headers=auth())
    assert response.status_code == 503 and response.json()["error_code"] == "service_token_missing"


def test_invalid_body_uses_the_uniform_error_shape(client):
    response = client().post("/v1/verify", json={"question": "q"}, headers=auth())
    assert response.status_code == 400
    assert set(response.json()) == {"error_code", "message"} and response.json()["error_code"] == "invalid_request"


def test_verify_over_http_returns_claims_and_llm_calls(client):
    body = dict(request(), draft="2.5 mg/kg组ORR为32%[E1]。", labels=["evaluation label"])
    llm = FakeLLM(verify_claims=supported())
    response = client(llm).post("/v1/verify", json=body, headers=auth())
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "PASS" and len(data["llm_calls"]) == 2
    assert "evaluation label" not in json.dumps([p for _, p, _ in llm.payloads], ensure_ascii=False)


def test_v2_verify_refuses_unknown_fields_and_a_rules_version_it_does_not_have(client):
    body = dict(request(), draft="2.5 mg/kg组ORR为32%[E1]。")
    http = client(FakeLLM(verify_claims=lambda p: {"units": [
        {"sentence": 1, "segment": "S1", "fragment": "2.5 mg/kg组ORR为32%", "kind": "fact", "verdict": "supported",
         "quotes": [{"ref": "E1", "quote": "ORR was 32%"}]}], "splits": [{"sentence": 1, "assessment": "complete"}]}))
    assert http.post("/v2/verify", json=body).status_code == 401
    answer = http.post("/v2/verify", json=dict(body, labels=["evaluation label"]), headers=auth())
    assert (answer.status_code, answer.json()["error_code"]) == (400, "invalid_request")
    nested = dict(body, evidence=[dict(body["evidence"][0], first_seen_at="2026-11-06T00:00:00Z", extra=1)])
    assert http.post("/v2/verify", json=nested, headers=auth()).status_code == 400
    stale = http.post("/v2/verify", json=dict(body, rules_version="biocheck.segment_rules.v0"), headers=auth())
    assert (stale.status_code, stale.json()["error_code"]) == (409, "rules_version_mismatch")
    ok = http.post("/v2/verify", json=body, headers=auth())
    assert ok.status_code == 200, ok.text
    claim = ok.json()["claims"][0]
    assert ok.json()["status"] == "PASS" and {"segments", "units", "form_check", "semantic_split"} <= set(claim)
    assert ok.json()["rules_version"] == "biocheck.segment_rules.v1" and ok.json()["checker_version"]


def test_generate_model_failure_is_a_502_with_llm_calls(client):
    response = client(FakeLLM()).post("/v1/generate", json=request(), headers=auth())
    assert response.status_code == 502
    assert response.json()["error_code"] == "invalid_output" and len(response.json()["llm_calls"]) == 1


def fake_api(responses):
    sent = []

    def handler(req):
        sent.append(json.loads(req.content))
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        content, finish = item
        return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": finish}],
                                         "usage": {"total_tokens": 10}})
    return httpx.Client(transport=httpx.MockTransport(handler)), sent


def test_local_vllm_gets_the_per_call_thinking_switch_its_template_reads(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "vllm")
    client, sent = fake_api([('{"verdict": "complete", "reason": "ok"}', "stop")] * 2)
    chat_json("verify_coverage", "sys", {"q": 1}, CoverageOut, client=client)
    chat_json("verify_coverage", "sys", {"q": 1}, CoverageOut, client=client, thinking=True)
    assert sent[0]["chat_template_kwargs"] == {"enable_thinking": False} and "thinking" not in sent[0]
    assert sent[1]["chat_template_kwargs"] == {"enable_thinking": True} and "temperature" not in sent[1]


def test_chat_json_retries_once_with_the_validation_error_and_disables_thinking(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    client, sent = fake_api([('{"verdict": "maybe"}', "stop"), ('{"verdict": "complete", "reason": "ok"}', "stop")])
    parsed, call = chat_json("verify_coverage", "sys", {"q": 1}, CoverageOut, client=client)
    assert parsed.verdict == "complete" and call["error"] is None and call["usage"]["total_tokens"] == 20
    assert sent[0]["thinking"] == {"type": "disabled"} and sent[0]["temperature"] == 0
    assert sent[0]["max_tokens"] == 4096
    assert "verdict" in sent[1]["messages"][1]["content"].split("上次输出未通过校验")[1]


def test_chat_json_gives_up_after_the_second_invalid_output(monkeypatch):
    client, sent = fake_api([("not json", "stop"), ("still not json", "stop")])
    parsed, call = chat_json("verify_claims", "sys", {}, CheckBatch, client=client)
    assert parsed is None and call["error"] == "invalid_output" and len(sent) == 2


def test_empty_output_at_length_limit_and_timeout_are_not_resent():
    client, sent = fake_api([("", "length")])
    parsed, call = chat_json("verify_claims", "sys", {}, CheckBatch, client=client)
    assert parsed is None and call["error"] == "output_empty_after_limit" and len(sent) == 1
    client, sent = fake_api([httpx.ReadTimeout("slow")])
    parsed, call = chat_json("verify_claims", "sys", {}, CheckBatch, client=client)
    assert parsed is None and call["error"] == "timeout" and len(sent) == 1


def test_hypotheses_reach_the_writer_over_http(client):
    seen = []

    def generate(payload):
        seen.append(payload)
        return {"draft": "待验证的假设：XQ-17 准备申报[E1]。"}

    hypothesis = {"id": "H1", "statement": "XQ-17 准备申报", "alternatives": ["只是更正"],
                  "confirm": ["受理公告"], "refute": ["试验终止"],
                  "facts": [{"ref": "E1", "text": "ORR 32%", "stance": "supports"}]}
    body = dict(request(), hypotheses=[hypothesis])
    response = client(FakeLLM(generate=generate)).post("/v1/generate", json=body, headers=auth())
    assert response.status_code == 200
    assert seen[0]["hypotheses"][0]["id"] == "H1" and seen[0]["hypotheses"][0]["facts"][0]["ref"] == "E1"


def test_reposts_and_origin_groups_reach_the_writer_and_the_checker_over_http(client):
    """The request models drop an undeclared field silently: relations and origin groups must be declared to reach
    the model payloads."""
    seen = {}

    def keep(stage, answer):
        def run(payload):
            seen[stage] = payload
            return answer(payload)
        return run

    llm = FakeLLM(generate=keep("generate", lambda p: {"draft": "转载内容与原文一致[E2]。"}),
                  verify_claims=keep("verify_claims", supported(quote="ORR was 32%", ref="E1")))
    body = request(source_relations=[{"kind": "repost", "source": "E2", "target": "E1", "target_record": "R1"}])
    body["evidence"][1]["origin_group"] = "og-1"
    http = client(llm)
    assert http.post("/v1/generate", json=body, headers=auth()).status_code == 200
    assert seen["generate"]["source_relations"][0] == {
        "kind": "repost", "source": "E2", "target": "E1", "target_record": "R1", "read": True,
    }
    assert seen["generate"]["evidence"][1]["origin_group"] == "og-1"
    assert http.post("/v1/verify", json=dict(body, draft="转载内容与原文一致[E2]。"), headers=auth()).status_code == 200
    assert seen["verify_claims"]["source_relations"][0]["target"] == "E1"
    assert seen["verify_claims"]["references"][0]["origin_group"] == "og-1"


def test_the_reviser_also_sees_which_materials_are_one_source(client):
    seen = []

    def revise(payload):
        seen.append(payload)
        return {"sentences": []}

    body = request(source_relations=[{"kind": "repost", "source": "E2", "target": "E1", "target_record": "R1"}])
    draft = "两个独立来源都报告了ORR 32%[E1][E2]。"
    review = {"status": "REVIEW", "claims": [{"index": 1, "text": draft, "start": 0, "end": len(draft),
              "citations": ["E1", "E2"], "verdict": "contradicted", "labels": [],
              "quotes": [{"ref": "E1", "quote": "ORR was 32%", "found": True}],
              "reason": "E2 转载自 E1"}]}
    response = client(FakeLLM(revise=revise)).post(
        "/v1/revise", json=dict(body, draft=draft, review=review), headers=auth()
    )
    assert response.status_code == 200
    assert seen and seen[0]["source_relations"][0]["target"] == "E1"
