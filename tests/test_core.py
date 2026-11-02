import pytest

from app import core
from conftest import FakeLLM, request, supported


def verify(draft, llm, **extra):
    return core.verify(dict(request(**extra), draft=draft), llm)


def test_supported_sentence_passes():
    result = verify("2.5 mg/kg组ORR为32%[E1]。", FakeLLM(verify_claims=supported()))
    assert result["status"] == "PASS"
    claim = result["claims"][0]
    assert claim["verdict"] == "supported" and claim["quotes"] == [{"ref": "E1", "quote": "ORR was 32%", "found": True}]
    assert [c["stage"] for c in result["llm_calls"]] == ["verify_claims", "verify_coverage"]


@pytest.mark.parametrize("quote,code", [("ORR was 30%", "quote_not_found"), ("ORR was", "quote_ambiguous")])
def test_bad_quote_makes_only_that_claim_insufficient_and_asks_to_read(quote, code):
    result = verify("2.5 mg/kg组ORR为32%[E1]。", FakeLLM(verify_claims=supported(quote)))
    claim = result["claims"][0]
    assert claim["verdict"] == "insufficient" and claim["reason"].startswith(code)
    assert result["status"] == "REVIEW"
    assert [r["ref"] for r in result["read_requests"]] == ["E1"]


def test_uncited_sentence_is_insufficient_without_a_model_call():
    llm = FakeLLM(verify_claims=supported())
    result = verify("ORR为32%[E1]。PFS为2.8个月。", llm)
    assert result["claims"][1]["verdict"] == "insufficient"
    assert result["claims"][1]["reason"].startswith("no_citation")
    assert [c["index"] for c in llm.payloads[0][1]["claims"]] == [1]
    assert result["status"] == "REVIEW"


def test_unknown_ref_fails_the_report_even_when_everything_else_is_supported():
    llm = FakeLLM(verify_claims=supported())
    result = verify("ORR为32%[E1]。PFS为2.8个月[S3]。", llm)
    assert result["claims"][1]["reason"].startswith("unknown_ref")
    assert result["status"] == "FAIL"


def test_quote_from_a_source_the_sentence_does_not_cite_is_not_support():
    result = verify("ORR为32%[E1]。", FakeLLM(verify_claims=supported(quote="网帖称", ref="E2")))
    assert result["claims"][0]["verdict"] == "insufficient"
    assert result["claims"][0]["reason"].startswith("quote_outside_citation")


def test_supported_verdict_with_an_unexplained_extra_number_is_not_accepted():
    result = verify("最终分析纳入999人，ORR为32%[E1]。", FakeLLM(verify_claims=supported()))
    assert result["claims"][0]["verdict"] == "insufficient"
    assert "NUM" in result["claims"][0]["labels"]
    explained = verify("ORR约为三分之一（32%，约0.32）[E1]。",
                       FakeLLM(verify_claims=supported(reason="32%换算为0.32")))
    assert explained["claims"][0]["verdict"] == "supported"


def test_attributed_social_claim_needs_attribution_and_uncertainty_wording():
    llm = FakeLLM(verify_claims=supported(quote="网帖称某市医院出现多例不明肺炎", ref="E2"))
    bare = verify("某市医院出现多例不明肺炎[E2]。", llm)
    assert bare["claims"][0]["verdict"] == "insufficient"
    assert "unconfirmed_source" in bare["claims"][0]["reason"]
    hedged = verify("网帖称某市医院出现多例不明肺炎，尚未证实[E2]。", llm)
    assert hedged["claims"][0]["verdict"] == "supported"


def correction_request(with_notice):
    notice = {"id": "E3", "title": "Correction", "source": "pubmed", "published_at": "2020-03-01",
              "source_kind": "research", "verified": True, "text": "Correction: ORR in the 2.5 mg/kg arm was 28%."}
    base = request(source_updates=[{"target": "E1", "replacement": "E3", "kind": "correction"}])
    if with_notice:
        base["evidence"].append(notice)
    return base


def test_corrected_number_used_as_current_is_not_supported_and_notice_is_requested():
    req = dict(correction_request(with_notice=False), draft="2.5 mg/kg组ORR为32%[E1]。")
    result = core.verify(req, FakeLLM(verify_claims=supported()))
    assert result["claims"][0]["verdict"] == "insufficient"
    assert result["claims"][0]["reason"].startswith("source_updated")
    assert result["read_requests"][0]["ref"] == "E3"


def test_correction_notice_contradiction_fails_and_history_wording_passes():
    contradicted = lambda p: {"claims": [{"index": 1, "verdict": "contradicted", "labels": ["NUM"],
                                          "quotes": [{"ref": "E3", "quote": "was 28%"}], "reason": "已更正为28%"}]}
    req = correction_request(with_notice=True)
    old = core.verify(dict(req, draft="2.5 mg/kg组ORR为32%[E1][E3]。"), FakeLLM(verify_claims=contradicted))
    assert old["claims"][0]["verdict"] == "contradicted" and old["status"] == "FAIL"

    both = lambda p: {"claims": [{"index": 1, "verdict": "supported", "labels": [],
                                  "quotes": [{"ref": "E1", "quote": "ORR was 32%"}, {"ref": "E3", "quote": "was 28%"}],
                                  "reason": "32%与28%均有原文"}]}
    history = core.verify(dict(req, draft="当时披露2.5 mg/kg组ORR为32%，后更正为28%[E1][E3]。"),
                          FakeLLM(verify_claims=both))
    assert history["claims"][0]["verdict"] == "supported"


def test_failed_batch_only_affects_its_own_sentences():
    def answer(payload):
        if payload["claims"][0]["index"] == 1:
            return None
        return supported()(payload)
    result = verify("ORR为32%[E1]。\n\n另一段ORR为32%[E1]。", FakeLLM(verify_claims=answer))
    assert result["claims"][0]["reason"].startswith("batch_failed")
    assert result["claims"][1]["verdict"] == "supported"
    assert result["status"] == "REVIEW"


def test_invented_index_is_ignored_and_missing_answer_stays_insufficient():
    answer = lambda p: {"claims": [{"index": 99, "verdict": "supported", "quotes": [], "reason": "?"}]}
    result = verify("ORR为32%[E1]。", FakeLLM(verify_claims=answer))
    assert result["claims"][0]["reason"].startswith("no_answer")


def test_max_llm_calls_leaves_the_rest_unchecked():
    result = verify("ORR为32%[E1]。\n\nORR为32%[E1]。", FakeLLM(verify_claims=supported()), max_llm_calls=1)
    assert len(result["llm_calls"]) == 1
    assert result["claims"][1]["reason"].startswith("unchecked")
    assert result["coverage"]["verdict"] == "uncertain"
    assert result["status"] == "REVIEW"


def test_status_rule_contradiction_fails_and_incomplete_coverage_reviews():
    contradicted = lambda p: {"claims": [{"index": 1, "verdict": "contradicted", "labels": ["OBJ"],
                                          "quotes": [{"ref": "E1", "quote": "ORR was 35%"}], "reason": "剂量组颠倒"}]}
    assert verify("2.5 mg/kg组ORR为35%[E1]。", FakeLLM(verify_claims=contradicted))["status"] == "FAIL"
    incomplete = FakeLLM(verify_claims=supported(),
                         verify_coverage=lambda p: {"verdict": "incomplete", "reason": "缺OS"})
    assert verify("ORR为32%[E1]。", incomplete)["status"] == "REVIEW"


def scope_answer(span, verdict="insufficient"):
    return lambda p: {"claims": [{"index": p["claims"][0]["index"], "verdict": verdict, "labels": ["COND"],
                                  "quotes": [{"ref": "E1", "quote": "open-label study"}],
                                  "reason": "摘要未报告随机化", "scope_span": span}]}


def test_unreported_written_as_not_done_is_narrowed_without_a_model_call():
    draft = "首段不动[E1]。\n\nDREAMM-2未进行随机化，为开放标签研究[E1]。"
    req = dict(request(), draft=draft)
    llm = FakeLLM(verify_claims=lambda p: scope_answer("未进行随机化")(p) if p["claims"][0]["index"] == 2
                  else supported("phase II")(p))
    review = core.verify(req, llm)
    assert review["claims"][1]["scope_span"] == "未进行随机化"
    result = core.revise(dict(req, review=review), FakeLLM())
    assert result["llm_calls"] == []
    assert result["draft"] == "首段不动[E1]。\n\nDREAMM-2本次引用材料不足以确认“未进行随机化”这一说法，为开放标签研究[E1]。"


def test_scope_span_is_dropped_when_verdict_or_span_does_not_qualify():
    for answer in (scope_answer("未进行随机化", verdict="supported"), scope_answer("开放标签研究")):
        review = verify("DREAMM-2未进行随机化，为开放标签研究[E1]。", FakeLLM(verify_claims=answer))
        assert "scope_span" not in review["claims"][0]


def contradicted_review(draft):
    answer = lambda p: {"claims": [{"index": 1, "verdict": "contradicted", "labels": ["OBJ", "NUM"],
                                    "quotes": [{"ref": "E1", "quote": "In the 3.4 mg/kg arm ORR was 35%"}],
                                    "reason": "35%属于3.4 mg/kg组"}]}
    req = dict(request(), draft=draft)
    return req, core.verify(req, FakeLLM(verify_claims=answer))


@pytest.mark.parametrize("replacement,applied", [
    ("2.5 mg/kg组ORR为32%[E1]。", True),
    ("2.5 mg/kg组ORR为32%[E2]。", False),           # borrows a source the sentence never cited
    ("2.5 mg/kg组ORR为32%。", False),               # drops every citation
    ("2.5 mg/kg组ORR为32%[E1]。PFS为2.8个月[E1]。", False),  # one sentence became two
])
def test_revise_keeps_one_sentence_and_only_its_own_citations(replacement, applied):
    draft = "2.5 mg/kg组ORR为35%[E1]。\n\n其余不变[E1]。"
    req, review = contradicted_review(draft)
    llm = FakeLLM(revise=lambda p: {"sentences": [{"index": 1, "text": replacement}]})
    result = core.revise(dict(req, review=review), llm)
    assert result["draft"] == (replacement + "\n\n其余不变[E1]。" if applied else draft)
    assert [c["index"] for c in result["changed"]] == ([1] if applied else [])


def test_revise_refuses_a_review_of_a_different_draft():
    req, review = contradicted_review("2.5 mg/kg组ORR为35%[E1]。")
    with pytest.raises(core.InputError) as raised:
        core.revise(dict(req, draft="改过的稿子[E1]。", review=review), FakeLLM())
    assert raised.value.code == "review_mismatch"


def test_generate_uses_8192_tokens_and_no_thinking_by_default(monkeypatch):
    monkeypatch.delenv("LLM_WRITE_THINKING", raising=False)
    llm = FakeLLM(generate=lambda p: {"draft": "ORR为32%[E1]。"})
    assert core.generate(request(), llm)["draft"] == "ORR为32%[E1]。"
    assert llm.payloads[0][2] == {"max_tokens": 8192, "thinking": False}
    assert "text" in llm.payloads[0][1]["evidence"][0]
