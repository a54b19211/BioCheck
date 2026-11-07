"""POST /v2/verify: verify a draft by assertion unit (docs/decisions/0004, report sections 0005).

The program cuts each sentence into numbered candidate segments (segment_rules.py). The model reads the whole
sentence, its neighbours and the cited texts, reports the units (a verbatim fragment at a segment each) and says
whether it believes its split is complete. The program numbers and locates the units, checks their form and sums
the sentence verdict (unit_rules.py). That the units are all the assertions of the sentence is only ever the
model's own report (semantic_split); nothing here guarantees it."""
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Literal

from pydantic import BaseModel

from . import claims as C
from . import core
from .llm import chat_json
from .prompts import CLAIM_VERIFY_UNITS, VERIFY_COVERAGE_UNITS
from .segment_rules import RULES_VERSION, VisibleSource, condition_context, split_segments, time_clues
from .unit_rules import ReportedUnit, check_sentence, coverage_view

CHECKER_VERSION = "biocheck.unit_verify.v2"
BATCH_SEGMENTS = 24  # candidate segments in one model call
LONG_SENTENCE_SEGMENTS, LONG_SENTENCE_CHARS = 8, 300  # a sentence over either goes alone
NO_CITATION = "no_citation: 本句陈述可核查事实却没有引用，不能借用相邻句的引用"
# Where an inference may stand: gaps explains what the limits of the evidence imply, and whether that is grounded is
# the semantic check's to say. evidence (and any section not named) states facts only.
INFERENCE_SECTIONS = ("judgment", "gaps", "scenarios", "recommendation")


# ---- what the model answers: flat lists, the sentence named by the program's index

class UnitOut(ReportedUnit):
    sentence: int  # the program's index of the sentence the unit belongs to


class SplitOut(BaseModel):
    """The model's own report on whether the units it gave are all the assertions of the sentence."""

    sentence: int
    assessment: Literal["complete", "incomplete", "uncertain"]
    reason: str = ""


class UnitBatch(BaseModel):
    units: list[UnitOut]
    splits: list[SplitOut]


@dataclass
class Inputs:
    """What every batch of one request needs."""

    req: dict
    refs: dict
    evidence: dict
    sentences: list
    sources: list  # VisibleSource of each evidence, for the time clues
    asked: set  # numbers the question itself states
    known_ids: frozenset  # identifiers the request pre-assigned (hypotheses)
    views: dict = field(default_factory=dict)  # sentence index -> what the coverage check is told about it


# ---- batches

def _fits(batch, claim, sizes):
    return (len(batch) < C.MAX_BATCH and batch[0]["paragraph"] == claim["paragraph"]
            and sum(sizes[c["index"]] for c in batch) + sizes[claim["index"]] <= BATCH_SEGMENTS)


def plan_batches(pending, sizes):
    """(batches, too_big). A batch holds at most C.MAX_BATCH sentences and BATCH_SEGMENTS segments of one paragraph;
    a long sentence goes alone; one with more segments than a batch holds cannot be sent and stays pending."""
    batches, too_big, open_batch = [], [], None
    for claim in pending:
        size = sizes[claim["index"]]
        if size > BATCH_SEGMENTS:
            too_big.append(claim)
            open_batch = None
        elif size > LONG_SENTENCE_SEGMENTS or len(claim["text"]) > LONG_SENTENCE_CHARS:
            batches.append([claim])
            open_batch = None
        elif open_batch and _fits(open_batch, claim, sizes):
            open_batch.append(claim)
        else:
            open_batch = [claim]
            batches.append(open_batch)
    return batches, too_big


def _offered(segments):
    """The segments as the model reads them; one that answers a condition says which segments hold it."""
    answers = condition_context(segments)
    return [dict(id=s.id, text=s.text, **({"consequent_of": answers[s.id]} if s.id in answers else {}))
            for s in segments]


def _payload(inputs, batch):
    updates = inputs.req.get("source_updates", [])
    payload = core.claims_payload(inputs.req, batch, inputs.sentences, inputs.refs, updates)
    for entry, claim in zip(payload["claims"], batch):
        entry["segments"] = _offered(split_segments(claim["text"]))
        clues = time_clues(claim["text"], inputs.sources)
        if clues:
            entry["time_clues"] = clues
    if inputs.known_ids:
        payload["pre_assigned_ids"] = sorted(inputs.known_ids)
    return payload


def _ask(budget, payload):
    """(the parsed answer or None, the error, whether the model's output was cut off, the position of the call in
    budget.calls - None when no call was made)."""
    made = len(budget.calls)
    out, error = budget.ask("verify_claims", CLAIM_VERIFY_UNITS, payload, UnitBatch)
    call = budget.calls[-1] if len(budget.calls) > made else {}
    cut = call.get("finish_reason") == "length" or call.get("error") == "output_empty_after_limit"
    return out, error, cut, made if call else None


def _run_batches(batches, inputs, budget):
    """Ask the model for each batch; the read requests of the sentences left without support. A cut-off answer
    splits its batch in two, down to one sentence, so the calls for one batch of n sentences are at most 2n-1 and
    the loop ends; max_llm_calls bounds it further (a batch it has no call left for stays unchecked)."""
    reads, queue = [], deque(batches)
    while queue:
        batch = queue.popleft()
        out, error, cut, call = _ask(budget, _payload(inputs, batch))
        if cut and len(batch) > 1:
            half = len(batch) // 2
            queue.extendleft([batch[half:], batch[:half]])
        else:
            reads += _record(batch, out, error, cut, call, inputs)
    return reads


# ---- the record of a sentence

def _segment_rows(segments, handled):
    """The program's segments with their state: handled (a located unit is at it), unhandled, or unchecked (there
    is no answer to check against)."""
    def state(segment):
        if handled is None:
            return "unchecked"
        return "handled" if handled.get(segment.id) else "unhandled"
    return [dict(asdict(s), status=state(s), units=(handled or {}).get(s.id, [])) for s in segments]


def _unchecked(claim, reason=None):
    """A sentence the model did not judge: the program's own cut is all there is to show."""
    if reason:
        claim["reason"] = reason
    claim.setdefault("call", None)
    claim.update(segments=_segment_rows(split_segments(claim["text"]), None), units=[], form_check=None,
                 semantic_split=None)


def _split_record(split):
    return {"source": "model_self_assessment", "assessment": split.assessment if split else "not_reported",
            "reason": split.reason if split else ""}


def _with_split(result, split):
    """The program's sentence verdict, except that a support or a non-claim the model's own split report does not
    stand behind (anything but complete, or no report) is held back. A contradiction is not."""
    if result.verdict not in ("supported", "no_claim") or (split and split.assessment == "complete"):
        return result.verdict, result.reason
    said = split.assessment if split else "not_reported"
    return "insufficient", f"semantic_split_{said}: 模型自评没有说单元已涵盖本句的全部断言"


def _apply(claim, result, split):
    verdict, reason = _with_split(result, split)
    labels = list(dict.fromkeys(label for unit in result.units for label in unit.labels))
    if verdict == "insufficient" and not claim["citations"]:
        reason, labels = NO_CITATION + ("；" + reason if reason else ""), list(dict.fromkeys([*labels, "REF"]))
    claim.update(verdict=verdict, labels=labels, reason=reason,
                 segments=_segment_rows(result.segments, result.form_check.handled),
                 units=[asdict(unit) for unit in result.units], form_check=asdict(result.form_check),
                 semantic_split=_split_record(split))


def _known_numbers(inputs, claim):
    """Numbers the question or another cited sentence of the draft states: repeating them is no new claim."""
    return inputs.asked | {t for other in inputs.sentences if other["citations"] and other is not claim
                          for t in C.numeric_tokens(other["text"])}


def _fact_section(claim, sections):
    """Whether the sentence lies in a section where only facts belong (any but INFERENCE_SECTIONS). Without sections,
    or outside every one of them, it is not: nothing is known about where it stands."""
    for section in sections:
        if section["start"] <= claim["start"] and claim["end"] <= section["end"]:
            return section["kind"] not in INFERENCE_SECTIONS
    return False


def _record(batch, out, error, cut, call, inputs):
    """Apply one batch's answer to its sentences; the read requests of those left without support. call: the
    position in llm_calls of the call that carried the batch, kept on each sentence."""
    for claim in batch:
        claim["call"] = call
    if out is None or cut:
        reason = ("output_truncated: 模型输出被截断，本句待核" if cut else
                  "unchecked: 已达max_llm_calls上限" if error == "llm_call_limit" else f"batch_failed: {error}")
        for claim in batch:
            _unchecked(claim, reason)
        return []
    splits = {}
    for split in out.splits:
        splits.setdefault(split.sentence, split)
    reads = []
    for claim in batch:
        mine = [unit for unit in out.units if unit.sentence == claim["index"]]  # other indexes are ignored
        result = check_sentence(claim, mine, inputs.refs, known_ids=inputs.known_ids,
                                fact_section=_fact_section(claim, inputs.req.get("sections", [])),
                                known_numbers=_known_numbers(inputs, claim))
        _apply(claim, result, splits.get(claim["index"]))
        inputs.views[claim["index"]] = {k: v for k, v in coverage_view(result).items() if k != "index"}
        if claim["verdict"] == "insufficient":
            reads += [{"ref": r, "reason": f"第{claim['index']}句缺少支持，补读{r}全文或相关段落后重核"}
                      for r in claim["citations"] if r in inputs.evidence]
    return reads


# ---- the operation

def _visible_sources(req):
    return [VisibleSource(ref=e["id"], text=e["text"], **{k: e.get(k) for k in ("published_at", *core.TIME_FIELDS)})
            for e in req.get("evidence", [])]


def verify(req, llm=chat_json):
    if req.get("rules_version") not in (None, RULES_VERSION):
        raise core.InputError("rules_version_mismatch",
                              f"本服务的句段规则版本是{RULES_VERSION}，请求要的是{req['rules_version']}", 409)
    core.materials(req)
    sentences = C.sentences(req["draft"])
    if not sentences:
        raise core.InputError("empty_draft", "draft中没有可核验的句子")
    refs, evidence = core.references(req), {e["id"]: e for e in req.get("evidence", [])}
    budget = core.Budget(llm, req.get("max_llm_calls", 20))
    pending, unknown_ref, reads, source_problems = core.triage(sentences, refs, evidence, req.get("source_updates", []))
    inputs = Inputs(req, refs, evidence, sentences, _visible_sources(req), set(C.numeric_tokens(req["question"])),
                    frozenset(h["id"] for h in req.get("hypotheses", [])))
    sizes = {c["index"]: len(split_segments(c["text"])) for c in pending}
    batches, too_big = plan_batches(pending, sizes)
    for claim in too_big:
        _unchecked(claim, f"batch_limit: 本句有{sizes[claim['index']]}个句段，超过单批上限{BATCH_SEGMENTS}，待核")
    reads += _run_batches(batches, inputs, budget)
    core.apply_source_problems(pending, source_problems)
    for claim in sentences:
        if "units" not in claim:  # triage settled it (unknown reference), or the model did not answer
            _unchecked(claim)
        claim.pop("paragraph")
        claim.pop("quotes")
    coverage = core.check_coverage(req, sentences, budget, VERIFY_COVERAGE_UNITS, inputs.views)
    return {"status": core.report_status(sentences, unknown_ref, coverage), "claims": sentences,
            "coverage": coverage, "read_requests": core.dedupe_reads(reads), "llm_calls": budget.calls,
            "checker_version": CHECKER_VERSION, "rules_version": RULES_VERSION}
