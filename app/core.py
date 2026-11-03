"""generate / verify / revise. Requests are plain dicts (see service.py); the model caller is injected."""
import json
import re
import os
from typing import Literal

from pydantic import BaseModel

from . import claims as C
from .llm import chat_json
from .prompts import CLAIM_REVISE, CLAIM_VERIFY, VERIFY_COVERAGE, WRITE


# Small model output schemas. The model only answers by the program's sentence index.
class DraftOut(BaseModel):
    draft: str


class QuoteOut(BaseModel):
    ref: str
    quote: str


class ClaimCheck(BaseModel):
    index: int
    verdict: Literal["supported", "contradicted", "insufficient", "no_claim"]
    labels: list[str] = []
    quotes: list[QuoteOut] = []
    reason: str = ""
    scope_span: str | None = None


class CheckBatch(BaseModel):
    claims: list[ClaimCheck]


class CoverageOut(BaseModel):
    verdict: Literal["complete", "incomplete", "uncertain"]
    reason: str


class Replacement(BaseModel):
    index: int
    text: str


class RevisionOut(BaseModel):
    sentences: list[Replacement]


class InputError(ValueError):
    def __init__(self, code, message, status=400, llm_calls=None):
        super().__init__(message)
        self.code, self.message, self.status, self.llm_calls = code, message, status, llm_calls


class Budget:
    """Counts chat_json calls against max_llm_calls and keeps the call records."""

    def __init__(self, llm, limit):
        self.llm, self.left, self.calls = llm, limit, []

    def ask(self, stage, system, payload, model_cls, **options):
        if self.left <= 0:
            return None, "llm_call_limit"
        self.left -= 1
        if not self.left:
            options = dict(options, retry=False)  # no room for a format retry
        parsed, call = self.llm(stage, system, payload, model_cls, **options)
        self.left -= max(0, call.get("requests", 1) - 1)
        self.calls.append(call)
        return parsed, call.get("error") or ("empty_output" if parsed is None else None)


def materials(req):
    ids = [e["id"] for e in req.get("evidence", [])] + [a["id"] for a in req.get("analysis_results", [])]
    if not ids:
        raise InputError("no_materials", "需要至少一份evidence或analysis_results")
    if len(set(ids)) != len(ids):
        raise InputError("duplicate_id", "evidence与analysis_results的id必须唯一")
    # Only these fields reach the model; evaluation labels and other extras never do.
    return {k: req[k] for k in ("question", "evidence", "analysis_results", "source_updates", "source_relations",
                                "research_scope", "previous_report", "hypotheses") if req.get(k) is not None}


def references(req):
    """id -> the text a citation may be checked against (analysis results as canonical JSON)."""
    refs = {e["id"]: {"ref": e["id"], "title": e.get("title", ""), "source_kind": e["source_kind"],
                      "verified": e["verified"], "published_at": e.get("published_at"),
                      "origin_group": e.get("origin_group"), "text": e["text"]}
            for e in req.get("evidence", [])}
    for a in req.get("analysis_results", []):
        body = {k: a.get(k) for k in ("method", "output", "calculation_basis", "limitations")}
        refs[a["id"]] = {"ref": a["id"], "kind": "analysis_result",
                         "text": json.dumps(body, ensure_ascii=False, sort_keys=True)}
    return refs


def context(sentences, claim):
    i = claim["index"] - 1
    return {"previous": sentences[i - 1]["text"] if i else None,
            "next": sentences[i + 1]["text"] if i + 1 < len(sentences) else None}


def hints(claim, refs):
    cited = [" ".join(str(refs[r].get(k) or "") for k in ("title", "published_at", "text")) for r in claim["citations"]]
    return C.numeric_hints(claim["text"], cited)


def mark_superseded(evidence, updates):
    """The writer sees on each old source which newer one corrects or withdraws it."""
    later = {u["target"]: u for u in updates}
    return [dict(e, superseded_by=later[e["id"]]["replacement"]) if e["id"] in later else e for e in evidence]


def generate(req, llm=chat_json):
    payload = materials(req)
    payload["evidence"] = mark_superseded(payload.get("evidence", []), payload.get("source_updates", []))
    budget = Budget(llm, req.get("max_llm_calls", 20))
    thinking = os.environ.get("LLM_WRITE_THINKING", "false").lower() in ("1", "true", "yes")
    out, error = budget.ask("generate", WRITE, payload, DraftOut, max_tokens=8192, thinking=thinking)
    if out is None or not out.draft.strip():
        raise InputError(error or "empty_draft", "生成失败：" + (error or "draft为空"),
                         400 if error == "llm_call_limit" else 502, budget.calls)
    return {"draft": out.draft.strip(), "llm_calls": budget.calls}


def judge(claim, answer, refs, evidence, known=frozenset()):
    """Apply one model answer to one sentence; bad quotes downgrade only this sentence.

    known: numbers written in cited sentences elsewhere in the draft; repeating them in a
    recommendation or summary is not a new claim (the cited sentence is checked on its own)."""
    labels = [x for x in dict.fromkeys(answer.labels) if x in C.LABELS]
    problems, good = [], set()
    for q in answer.quotes:
        where = C.locate_quote(refs[q.ref]["text"], q.quote) if q.ref in claim["citations"] else "outside"
        claim["quotes"].append({"ref": q.ref, "quote": q.quote, "found": where in ("found", "ambiguous")})
        if where in ("found", "ambiguous"):  # the text is really there; a repeated quote is still support
            good.add(q.ref)
        else:
            problems.append({"outside": f"quote_outside_citation: {q.ref}不是本句的引用",
                             "not_found": f"quote_not_found: 短引不在{q.ref}原文中",
                             }[where])
    if answer.verdict == "supported":
        missing = [r for r in claim["citations"] if r not in good]
        if missing:
            problems.append("missing_quote: 缺少" + "、".join(missing) + "的有效短引")
        # The reason must name the number itself, not merely contain its digits ("E3", "2023").
        unexplained = [t for t in hints(claim, refs)
                       if not re.search(r"(?<![A-Za-z0-9_.])" + re.escape(t.rstrip("%")) + r"(?![0-9])",
                                        answer.reason)]
        if unexplained:
            problems.append("numeric_unexplained: 数字" + "、".join(unexplained) + "不在所引原文中，也未说明换算")
            labels = list(dict.fromkeys(labels + ["NUM"]))
    if answer.verdict == "no_claim" and set(C.numeric_tokens(claim["text"])) - known:
        problems.append("no_claim_with_numbers: 含具体数字的句子是可核查陈述")
    if not claim["citations"] and answer.verdict != "no_claim":
        problems.insert(0, "no_citation: 本句陈述可核查事实却没有引用，不能借用相邻句的引用")
        labels = list(dict.fromkeys(labels + ["REF"]))
    if answer.verdict == "contradicted" and not good:
        problems.append("missing_quote: 矛盾判定缺少有效短引")
    claim["verdict"] = "insufficient" if problems else answer.verdict
    claim["labels"] = labels
    claim["reason"] = "；".join(problems) + "｜模型理由：" + answer.reason if problems else answer.reason
    if (answer.verdict == "insufficient" and not problems and answer.scope_span
            and all(r in evidence and r in good for r in claim["citations"])
            and C.scope_span_ok(claim["text"], answer.scope_span)):
        claim["scope_span"] = answer.scope_span


def verify(req, llm=chat_json):
    materials(req)
    refs, evidence = references(req), {e["id"]: e for e in req.get("evidence", [])}
    updates = req.get("source_updates", [])
    budget = Budget(llm, req.get("max_llm_calls", 20))
    sentences = C.sentences(req["draft"])
    if not sentences:
        raise InputError("empty_draft", "draft中没有可核验的句子")
    pending, unknown_ref, reads, source_problems = [], False, [], {}
    for claim in sentences:
        claim.update(verdict="insufficient", labels=[], quotes=[], reason="")
        unknown = [r for r in claim["citations"] if r not in refs]
        if not claim["citations"]:
            # The model decides whether an uncited sentence states a checkable fact (see judge).
            source_problems[claim["index"]] = []
            pending.append(claim)
        elif unknown:
            unknown_ref = True
            claim.update(labels=["REF"], reason="unknown_ref: 引用了不存在的编号" + "、".join(unknown))
        else:
            source_problems[claim["index"]], found = C.source_use_problems(
                claim["text"], claim["citations"], evidence, updates)
            reads += found
            pending.append(claim)

    asked = set(C.numeric_tokens(req["question"]))  # e.g. a replay cutoff restated in a scope note
    for batch in C.batches(pending):
        cited = list(dict.fromkeys(r for c in batch for r in c["citations"]))
        payload = {"question": req["question"],
                   "claims": [{"index": c["index"], "text": c["text"], "citations": c["citations"],
                               "numeric_hints": hints(c, refs),
                               "context": context(sentences, c)} for c in batch],
                   "references": [refs[r] for r in cited],
                   "source_updates": [u for u in updates if u["target"] in cited or u["replacement"] in cited],
                   "source_relations": [r for r in req.get("source_relations", [])
                                        if r["source"] in cited or r.get("target") in cited]}
        out, error = budget.ask("verify_claims", CLAIM_VERIFY, payload, CheckBatch)
        if out is None:
            reason = "unchecked: 已达max_llm_calls上限" if error == "llm_call_limit" else f"batch_failed: {error}"
            for c in batch:
                c["reason"] = reason
            continue
        answers = {}
        for answer in out.claims:
            answers.setdefault(answer.index, answer)  # indexes outside this batch are ignored
        for c in batch:
            if c["index"] in answers:
                known = asked | {t for o in sentences if o["citations"] and o is not c for t in C.numeric_tokens(o["text"])}
                judge(c, answers[c["index"]], refs, evidence, known)
                if c["verdict"] == "insufficient" and c.get("scope_span") is None:
                    reads += [{"ref": r, "reason": f"第{c['index']}句缺少支持，补读{r}全文或相关段落后重核"}
                              for r in c["citations"] if r in evidence]
            else:
                c["reason"] = "no_answer: 模型未返回本句结论"

    for claim in pending:
        problems = source_problems[claim["index"]]
        if problems:
            claim.pop("scope_span", None)
            claim["labels"] = list(dict.fromkeys(claim["labels"] + ["REF"]))
            claim["reason"] = "；".join(problems + ([claim["reason"]] if claim["reason"] else []))
            if claim["verdict"] != "contradicted":
                claim["verdict"] = "insufficient"

    # Only the text: given the sentence verdicts, the model just repeats them as coverage gaps.
    payload = {"question": req["question"], "sentences": [{"index": c["index"], "text": c["text"]} for c in sentences],
               "materials": [{"id": e["id"], "title": e.get("title", ""), "source_kind": e["source_kind"]}
                             for e in req.get("evidence", [])]
                            + [{"id": a["id"], "method": a.get("method", "")} for a in req.get("analysis_results", [])],
               "research_scope": req.get("research_scope", {}),
               "previous_report": req.get("previous_report")}
    out, error = budget.ask("verify_coverage", VERIFY_COVERAGE, payload, CoverageOut)
    coverage = out.model_dump() if out else {
        "verdict": "uncertain",
        "reason": "unchecked: 已达max_llm_calls上限" if error == "llm_call_limit" else f"coverage_failed: {error}"}

    if unknown_ref or any(c["verdict"] == "contradicted" for c in sentences):
        status = "FAIL"
    elif any(c["verdict"] not in ("supported", "no_claim") for c in sentences) or coverage["verdict"] != "complete":
        status = "REVIEW"
    else:
        status = "PASS"
    read_requests = {}
    for request in reads:
        read_requests.setdefault(request["ref"], request)  # first reason per ref wins
    for claim in sentences:
        claim.pop("paragraph")
    return {"status": status, "claims": sentences, "coverage": coverage,
            "read_requests": list(read_requests.values()), "llm_calls": budget.calls}


def revise(req, llm=chat_json):
    materials(req)
    refs, draft = references(req), req["draft"]
    current = {c["index"]: c for c in C.sentences(draft)}
    sentences = list(current.values())
    replaced, to_model = {}, []
    for old in req["review"]["claims"]:
        now = current.get(old["index"])
        if now is None or (now["text"], now["start"]) != (old["text"], old["start"]):
            raise InputError("review_mismatch", "review与draft的句子不对应，请先对这份draft重新核验", 409)
        overreach = (old["verdict"] == "insufficient" and now["citations"] and old["labels"]
                     and not old["reason"].startswith(("no_citation", "unknown_ref", "unchecked", "batch_failed")))
        if (old["verdict"] == "contradicted" and any(q["found"] for q in old["quotes"])) or (
                overreach and not old.get("scope_span")):
            to_model.append(dict(now, verdict=old["verdict"], labels=old["labels"],
                                 reason=old["reason"], quotes=old["quotes"]))
        elif old["verdict"] == "insufficient" and C.scope_span_ok(now["text"], old.get("scope_span")):
            replaced[now["index"]] = C.scope_revision(now["text"], old["scope_span"])
        elif old["verdict"] == "insufficient" and not now["citations"] and old["reason"].startswith("no_citation"):
            # An uncited fact may take a citation from its neighbours, and only from them.
            near = [current.get(now["index"] + d) for d in (-1, 1)]
            nearby = list(dict.fromkeys(r for o in near if o for r in o["citations"] if r in refs))
            if nearby:
                to_model.append(dict(now, citations=nearby, verdict=old["verdict"], labels=old["labels"],
                                     reason=old["reason"], quotes=[]))
    budget = Budget(llm, req.get("max_llm_calls", 20))
    for batch in C.batches(sorted(to_model, key=lambda c: c["index"]), size=C.MAX_BATCH):
        cited = list(dict.fromkeys(r for c in batch for r in c["citations"]))
        payload = {"question": req["question"],
                   "claims": [{k: c[k] for k in ("index", "text", "citations", "verdict", "labels", "reason", "quotes")}
                              | {"context": context(sentences, c)} for c in batch],
                   "references": [refs[r] for r in cited if r in refs]}
        out, _ = budget.ask("revise", CLAIM_REVISE, payload, RevisionOut)
        allowed = {c["index"]: c for c in batch}
        for item in (out.sentences if out else []):
            old, text = allowed.get(item.index), item.text.strip()
            if old is None or item.index in replaced or "\n" in text or len(C.sentences(text)) != 1:
                continue
            cites = C.citations(text)
            if cites and set(cites) <= set(old["citations"]):  # only the sentence's own references
                replaced[item.index] = text
    changed, out_draft = [], draft
    for index in sorted(replaced, reverse=True):
        before, after = current[index]["text"], replaced[index]
        if after != before:
            out_draft = out_draft[:current[index]["start"]] + after + out_draft[current[index]["end"]:]
            changed.insert(0, {"index": index, "before": before, "after": after})
    return {"draft": out_draft, "changed": changed, "llm_calls": budget.calls}
