"""A neighbour bounds a dimension's metric slot only when it is independently proven
and of a compatible kind; a wrong extra dimension never helps another one pass, and nothing proves itself in a
cycle. Generic templates; the value itself stays consistent."""

from test_measurement_rules import client  # noqa: F401
from test_dimension_rules import answer, body

Q = "2026年1月，{lead}医院A在湖北省共报告{metric}10例。"


def ask(client, lead, metric, dims):  # noqa: F811
    q = Q.format(lead=lead, metric=metric)
    text = "## 按报告日统计\n\n" + q
    return answer(client, body(text, q[:-1], dims, metric="报告" + metric, scope="在湖北省"))


def test_wrong_extra_dimensions_do_not_bound_a_shortened_population(client):  # noqa: F811
    got = ask(client, "在女性成人中，", "炭疽确诊病例", [("disease", "炭疽"), ("population", "成人"),
                                                   ("entity", "女性"), ("stage", "中")])
    assert got["status"] == "consistent"
    assert got["dims"]["entity"]["result"] == got["dims"]["stage"]["result"] == "unsupported"
    assert got["dims"]["population"]["result"] == "unsupported"


def test_a_proven_population_and_disease_bound_each_other_only_as_an_anchored_run(client):  # noqa: F811
    got = ask(client, "", "成人炭疽确诊病例", [("disease", "炭疽"), ("population", "成人")])
    assert {k: r["result"] for k, r in got["dims"].items()} == {"disease": "match", "population": "match"}
    # a population that fails on its own (another qualified phrase) does not bound the disease either
    got = ask(client, "", "女性成人炭疽确诊病例", [("disease", "炭疽"), ("population", "成人")])
    assert got["dims"]["population"]["result"] == "unsupported"


def test_a_run_in_the_wrong_order_or_with_an_incompatible_kind_is_no_slot(client):  # noqa: F811
    got = ask(client, "", "炭疽成人确诊病例", [("disease", "炭疽"), ("population", "成人")])
    assert got["dims"]["disease"]["result"] == "unsupported" or got["dims"]["population"]["result"] == "unsupported"
