import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core import CheckBatch, CoverageOut
from app.llm import chat_json
from app.service import create_app
from conftest import FakeLLM, request, supported

TOKEN = "test-token"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("SERVICE_TOKEN", TOKEN)
    return lambda llm=FakeLLM(): TestClient(create_app(llm))


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
