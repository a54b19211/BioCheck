"""Fixed-evidence QA demonstrator (stdlib only).

The implemented checks are deterministic, limited demonstrations, NOT
validated medical factuality models or substitutes for domain review.

Inputs are frozen upstream records; there is no search, graph processing,
statistical recomputation or answer editing.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any
import hashlib
import json
import re


VALUE_RE = re.compile(r'(?<!\d)(\d+(?:\.\d+)?)\s*(%|％|例|人|项|个)')
CITATION_RE = re.compile(r'\[([A-Za-z0-9_.-]+)\]')
SENTENCE_RE = re.compile(r'[^。！？；;\n]+[。！？；;]?')


def canonical(s: Any) -> str:
    return re.sub(r'\s+', '', str(s or '')).replace('％', '%').replace('≥', '>=').lower()


def digest(value: Any) -> str:
    blob = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(blob.encode('utf-8')).hexdigest()[:16]


@dataclass
class Fact:
    evidence_id: str
    study: str
    arm: str
    metric: str
    value: float
    unit: str
    condition: str
    time_range: str
    dedup_rule: str
    source_span: str
    required: bool
    kind: str


def build_fact_objects(packet: dict[str, Any]) -> list[Fact]:
    """Build task-scoped records from supplied structured upstream facts.

    It does not hallucinate missing attributes or run an LLM extraction;
    source_span is retained for independent human checking.
    """
    result = []
    ids = set()
    for e in packet.get('evidence', []):
        eid = e.get('evidence_id')
        if not isinstance(eid, str) or not eid or eid in ids:
            raise ValueError(f'Invalid/duplicate evidence ID: {eid!r}')
        ids.add(eid)
        if 'value' not in e or 'metric' not in e:
            raise ValueError(f'evidence {eid}: missing metric/value')
        result.append(Fact(
            evidence_id=eid, study=str(e.get('study', '')),
            arm=str(e.get('arm', '')), metric=str(e['metric']),
            value=float(e['value']), unit=str(e.get('unit', '')),
            condition=str(e.get('condition', '')),
            time_range=str(e.get('time_range', '')),
            dedup_rule=str(e.get('dedup_rule', '')),
            source_span=str(e.get('source_span', '')),
            required=bool(e.get('required', False)), kind=str(e.get('kind', 'core'))))
    return result


def plan_answer(facts: list[Fact]) -> list[dict]:
    """Transparent content-plan demo using source-linked fact objects."""
    return [dict(fact_id=f.evidence_id, study=f.study, arm=f.arm,
                 metric=f.metric, value=f.value, unit=f.unit,
                 condition=f.condition, time_range=f.time_range,
                 dedup_rule=f.dedup_rule) for f in facts]


def fmt_num(number: float) -> str:
    return str(int(number)) if number.is_integer() else f'{number:g}'


def generate_template_answer(facts: list[Fact]) -> str:
    """A simple template demonstration, not the proposed M3 LLM system."""
    lines = []
    for f in facts:
        head = f'{f.study}{f.arm}' if f.study else ''
        conditions = f'在{f.condition}条件下，' if f.condition else ''
        period = f'{f.time_range}期间，' if f.time_range else ''
        dedup = f'按{f.dedup_rule}，' if f.dedup_rule else ''
        lines.append(f'{period}{dedup}{head}{conditions}{f.metric}为{fmt_num(f.value)}{f.unit}[{f.evidence_id}]。')
    return ''.join(lines)


def chunk_claims(answer: str) -> list[str]:
    return [s.strip() for s in SENTENCE_RE.findall(answer) if s.strip()]


def issue(code: str, severity: str, claim: str, evidence_id: str, reason: str) -> dict:
    return {'type': code, 'severity': severity, 'answer_span': claim,
            'evidence_id': evidence_id, 'reason': reason}


def check_answer(packet: dict[str, Any], answer: str) -> dict:
    """Rule-limited reference verifier; never silently corrects the answer."""
    facts = build_fact_objects(packet)
    known = {f.evidence_id: f for f in facts}
    checked, unchecked, undetermined, issues = [], [], [], []
    claims = chunk_claims(answer)
    cited_ids = set()
    supported_ids = set()

    if not facts:
        return {'status': 'REVIEW', 'runtime_status': 'OK',
                'issues': [issue('INPUT', 'review', '', '', '未提供有效固定证据')],
                'scope': {'checked': [], 'unchecked': claims, 'undetermined': claims}}

    for claim in claims:
        citations = CITATION_RE.findall(claim)
        cited_ids.update(citations)
        values = [(float(num), unit.replace('％', '%')) for num, unit in VALUE_RE.findall(claim)]
        if not citations:
            unchecked.append(claim)
            if values:
                issues.append(issue('REF', 'definite', claim, '', '数字陈述缺少证据编号'))
            else:
                undetermined.append(claim)
            continue

        verified_any = False
        for cid in citations:
            if cid not in known:
                issues.append(issue('REF', 'definite', claim, cid, '引用编号不在冻结证据中'))
                continue
            f = known[cid]
            verified_any = True
            # Check source assignment when a different named study explicitly appears.
            other_studies = [x.study for x in facts if x.study and x.study != f.study and x.study in claim]
            if other_studies and (not f.study or f.study not in claim):
                issues.append(issue('OBJ', 'definite', claim, cid,
                                    f'引用证据归属为{f.study or "（无研究名）"}，陈述出现其他研究名：{other_studies[0]}'))
            if f.metric and canonical(f.metric) not in canonical(claim):
                issues.append(issue('REF', 'review', claim, cid, '引用证据指标未能与陈述对应，需人工检查别名'))

            if values:
                matches = any(abs(v-f.value) <= 1e-8 and u == f.unit.replace('％','%') for v,u in values)
                if not matches:
                    # A value belonging to a *different* study's same metric is an attribution issue.
                    belongs_other = any(
                        x.evidence_id != cid and x.metric == f.metric and
                        x.study != f.study and any(abs(v-x.value) < 1e-8 and u == x.unit for v,u in values)
                        for x in facts
                    )
                    kind = 'OBJ' if belongs_other else 'NUM'
                    reason = ('陈述数值属于另一个研究的相同指标，可能发生事实错配' if belongs_other
                              else f'陈述数值或单位与引用证据不一致（参考值{fmt_num(f.value)}{f.unit}）')
                    issues.append(issue(kind, 'definite', claim, cid, reason))
                else:
                    supported_ids.add(cid)
            else:
                issues.append(issue('REF', 'review', claim, cid, '未识别到可比对的数值，尚不能完成本项规则检查'))

            if f.condition and canonical(f.condition) not in canonical(claim):
                issues.append(issue('COND', 'review', claim, cid, '证据中的必要条件未在本条陈述中识别到，可能由上下文承接'))
            if f.time_range and canonical(f.time_range) not in canonical(claim):
                issues.append(issue('TIME', 'review', claim, cid, '证据时间范围未在本条陈述中识别到'))
            if f.dedup_rule and canonical(f.dedup_rule) not in canonical(claim):
                issues.append(issue('COND', 'review', claim, cid, '未在本条陈述中识别到去重口径'))
        if verified_any:
            checked.append(claim)
        else:
            unchecked.append(claim)

    # Task-scoped coverage only when upstream marked a fact required.
    for f in facts:
        if f.required and f.evidence_id not in cited_ids:
            issues.append(issue('COVERAGE', 'definite', '', f.evidence_id,
                                '上游指定的必答证据未被引用；需人工复核其是否通过其他表述覆盖'))

    if not claims:
        status = 'REVIEW'
    elif any(x['severity'] == 'definite' for x in issues):
        status = 'FAIL'
    elif any(x['severity'] == 'review' for x in issues) or unchecked:
        status = 'REVIEW'
    else:
        status = 'PASS'

    return {
        'status': status,
        'status_definition': '仅表示本次限定的规则检查状态，不是医学审核或发布许可',
        'runtime_status': 'OK',
        'answer_version': packet.get('answer_version', 'unspecified'),
        'evidence_version': packet.get('evidence_version', 'unspecified'),
        'analysis_version': packet.get('analysis_version', 'unspecified'),
        'answer_hash': digest(answer),
        'evidence_hash': digest(packet.get('evidence', [])),
        'checked_rules': ['evidence_id','metric_surface','number_unit','study_surface','required_condition_surface','time_range_surface','dedup_surface','required_coverage'],
        'unchecked_rules': ['CONCLUSION_STRENGTH_semantics','semantic_entailment','unseen_external_evidence','cross_sentence_coreference'],
        'scope': {'checked': checked, 'unchecked': unchecked,
                  'undetermined': undetermined},
        'issues': issues,
    }


def process(packet: dict[str, Any], use_supplied_answer: bool = True) -> dict:
    try:
        facts = build_fact_objects(packet)
        answer = packet.get('answer') if use_supplied_answer else None
        if not isinstance(answer, str):
            answer = generate_template_answer(facts)
        return {'question': packet.get('question', ''),
                'facts': [asdict(f) for f in facts],
                'content_plan': plan_answer(facts),
                'answer': answer,
                'verification': check_answer(packet, answer)}
    except Exception as err:
        # Process failure has a distinct runtime status, not a factual FAIL.
        return {'question': packet.get('question',''), 'answer': packet.get('answer'),
                'verification': {'runtime_status': 'ERROR', 'error': str(err),
                                 'status': None, 'issues': []}}
