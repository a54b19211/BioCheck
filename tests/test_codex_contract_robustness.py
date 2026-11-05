import pytest
from fastapi.testclient import TestClient
from app.service import create_app
from conftest import FakeLLM
from test_extraction import Doc,SUBJECT,measurement

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("SERVICE_TOKEN", "test-token")
    return TestClient(create_app(FakeLLM()), raise_server_exceptions=False)

def post(client,d,item):
    return client.post("/v1/extraction-consistency",json={"subject":SUBJECT,"passages":d.passages,"items":[item]},headers={"Authorization":"Bearer test-token"})

@pytest.mark.parametrize("kind", ["identifier","claim"])
@pytest.mark.parametrize("state", ["unknown","not_applicable"])
def test_explicit_unknown_required_field_is_handled_without_server_error(client,kind,state):
    d=Doc(p="药物A")
    fields={"scheme":"doi","identifier":{"state":state}} if kind=="identifier" else {"statement":{"state":state},"polarity":"uncertain"}
    item={"id":"missing","kind":kind,"producer":{"name":"test","version":"1"},"locator":d.span("p","药物A"),"quote":"药物A","fields":fields}
    r=post(client,d,item)
    assert r.status_code in (200,400,422),r.text
    if r.status_code==200:
        assert r.json()["items"][0]["status"]!="consistent",r.text

def test_schema_valid_long_decimal_is_bounded_or_explicitly_unsupported(client):
    value="100."+"0"*30
    d=Doc(p="成人药物A响应率"+value+"%（1/1）。")
    item=measurement(d,"p",value+"%",object=d.field("p","药物A"),metric=d.field("p","响应率"),value=d.field("p",value),unit=d.field("p","%"),scope=d.field("p","成人"),denominator=d.field("p","1",n=1,inside="1/1"))
    r=post(client,d,item)
    assert r.status_code in (200,400,422),r.text
