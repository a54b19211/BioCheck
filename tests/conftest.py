import copy

import pytest

E1 = ("DREAMM-2 was a phase II, two-arm, open-label study. In the 2.5 mg/kg arm ORR was 32% "
      "and median PFS was 2.8 months. In the 3.4 mg/kg arm ORR was 35%.")
E2 = "网帖称某市医院出现多例不明肺炎，帖子随后被删除。"


def request(**extra):
    base = {"question": "DREAMM-2两个剂量组的结果如何？",
            "evidence": [{"id": "E1", "title": "DREAMM-2", "source": "pubmed", "published_at": "2020-01-01",
                          "source_kind": "research", "verified": True, "text": E1},
                         {"id": "E2", "title": "网帖", "source": "weibo", "published_at": None,
                          "source_kind": "social", "verified": False, "text": E2}],
            "analysis_results": [], "source_updates": [],
            "research_scope": {"checked": ["pubmed"], "not_checked": [], "failed": []},
            "previous_report": None, "max_llm_calls": 20}
    base.update(extra)
    return base


class FakeLLM:
    """Stands in for llm.chat_json. `answers[stage]` is a function payload -> dict, or None to fail."""

    def __init__(self, **answers):
        self.answers, self.payloads = answers, []

    def __call__(self, stage, system, payload, model_cls, **options):
        self.payloads.append((stage, copy.deepcopy(payload), options))
        call = {"stage": stage, "model": "fake", "finish_reason": "stop", "usage": {}, "seconds": 0.0, "error": None}
        default = (lambda p: {"verdict": "complete", "reason": "ok"}) if stage == "verify_coverage" else None
        answer = self.answers.get(stage, default)
        data = answer(payload) if answer else None
        if data is None:
            return None, dict(call, error="invalid_output")
        return model_cls.model_validate(data), call

    def stages(self):
        return [s for s, _, _ in self.payloads]


def supported(quote="ORR was 32%", ref="E1", reason="原文一致"):
    """Every claim in the batch judged supported with the same quote."""
    return lambda p: {"claims": [{"index": c["index"], "verdict": "supported", "labels": [],
                                  "quotes": [{"ref": ref, "quote": quote}], "reason": reason} for c in p["claims"]]}


@pytest.fixture
def fake():
    return FakeLLM
