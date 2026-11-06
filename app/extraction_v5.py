"""Extraction consistency, contract v3 / checker v11 (T-24 complete domain, Codex 730/740).

v11 answers a measurement item exactly as v10 does (its fields, its status - with extraction_normalize.v4 for
the unit in the bound metric phrase) and, beside them, each business dimension the request states. A
dimension is "match" only when three things hold, each from the text alone:
1. its literal is written at its span;
2. it belongs to this value: in the value's own sentence with no other quantity between them, or in an
   effective ancestor heading of the value;
3. the role its kind names has evidence in the text - a closed grammar of explicit labels and forms (a
   disease named with a case or outbreak noun or a disease label; a population word or label; a date basis
   written as such; a denominator definition with its counting noun; a product class noun; a company form;
   an indication label or "用于…治疗"; a milestone verb; a stage word). An institution, a date or a bare number
   proves no role.
Anything else is "unsupported" (dim_relation_not_rule_checkable / dim_kind_not_proven) - left to the
investigation or the graph, never guessed from a family label or the producer's own claim. The item status
is v10's: dimensions never change it."""

import re

from extraction_contract import normalize as norm

from . import extraction as v3
from . import extraction_v4 as v10
from .extraction import outcome, squash

CONTRACT_VERSION = "extraction_consistency.v3"
# v12 (Codex r178): a dimension keeps its qualifying context - its phrase may not negate or exclude it, nor join
# it with another member; v11 stays as released on its own commit (e98b392)
# v13 (Codex r179): a dimension must fill a whole slot of a recognised structure (whole_phrase below); v12 stays
# as released on its own commit (1c08f7c)
# v14 (Codex r181): only independently proven, kind-compatible neighbours bound a metric slot (composite below);
# v13 stays as released on its own commit (978af0a)
# v15 (Codex 783, T-24 industrial): a reporting-period date basis written as such, a magnitude-and-classifier
# unit kept whole, output a flow only as actual output (extraction_v4 output=True); v14 stays as released on its
# own commit (2b395ae)
CHECKER_VERSION = "biocheck.extraction_rules.v15"
NORMALIZE_VERSION = norm.NORMALIZE_V4

_LABEL = r"[:：]\s*$"
_BEFORE = {
    "disease": re.compile(r"(?:病种|疾病|病名|传染病|disease)\s*" + _LABEL, re.IGNORECASE),
    "population": re.compile(r"(?:人群|对象|队列|研究人群|population|cohort)\s*" + _LABEL, re.IGNORECASE),
    "date_basis": re.compile(r"(?:日期口径|统计口径|统计方式|date basis)\s*" + _LABEL, re.IGNORECASE),
    "denominator_definition": re.compile(r"(?:分母|denominator)\s*(?:为|是|[:：])\s*$", re.IGNORECASE),
    "product_or_class": re.compile(r"(?:产品|类别|品种|产品类别|product|class)\s*" + _LABEL, re.IGNORECASE),
    "entity": re.compile(r"(?:企业|公司|单位|主体|entity|company)\s*" + _LABEL, re.IGNORECASE),
    "indication": re.compile(r"(?:适应症|适应证|indication)\s*(?:为|是|[:：])\s*$", re.IGNORECASE),
}
_AFTER = {
    "disease": re.compile(r"^(?:疫情|病例|确诊病例|疑似病例|确诊|疑似|患者|感染者|感染|死亡病例|\s*(?:outbreak|cases?\b|"
                          r"infections?\b|deaths?\b))", re.IGNORECASE),
}
_SELF = {
    "population": re.compile(
        r"^(?:成人|成年人|儿童|婴幼儿|婴儿|新生儿|青少年|学生|老年人|孕产妇|孕妇|医务人员|医护人员|密切接触者|"
        r"接触者|全人群|所有病例|全部病例|全部受试者|所有受试者|(?:\d+|十八|六十|六十五)岁(?:以上|以下|及以上)(?:人群|患者)?|"
        r"adults?|children|infants?|adolescents?|elderly|pregnant women|health(?:care)? workers|close contacts|"
        r"all cases|all participants|the general population)$",
        re.IGNORECASE,
    ),
    "date_basis": re.compile(
        r"^(?:按|以)?(?:报告|发病|采样|入院|诊断|死亡|就诊|检测)(?:日期|日|时间)(?:统计|计)?$|"
        r"^by (?:date of )?(?:report(?:ing)?|onset|sampling|admission|diagnosis|death)(?: date)?$|"
        # v15: a reporting period stated as the basis (never a bare 报告期, never a production or shipping date)
        r"^(?:按|以)报告期(?:统计|计)?$|^by reporting period$",
        re.IGNORECASE,
    ),
    "denominator_definition": re.compile(
        r"(?:检测|筛查|调查|接种|报告|受检|参与|就诊|监测)?(?:人数|例数|样本数|人次|病例数|总人数)$|"
        r"^(?:all|total) (?:\w+ )?(?:tested|screened|surveyed|vaccinated|cases|participants)$",
        re.IGNORECASE,
    ),
    "product_or_class": re.compile(
        r"(?:产品|器械|医疗器械|药品|疫苗|试剂|设备|仪器|制剂|耗材|devices?|drugs?|vaccines?|products?|reagents?)$",
        re.IGNORECASE,
    ),
    "entity": re.compile(r"(?:公司|集团|企业|厂|有限公司|股份|Inc\.?|Ltd\.?|Co\.?|Corporation|GmbH|plc)$",
                         re.IGNORECASE),
    "milestone_definition": re.compile(r"批准|注册|受理|获批|上市|审评|approv|regist|accept|launch|clearance",
                                       re.IGNORECASE),
    "stage": re.compile(r"^(?:计划|拟建|在建|开工|投运|投产|建成|实际产出|planned|under construction|operational|"
                        r"commissioned|in operation)$", re.IGNORECASE),
}
# an institution or an authority is never a product, an entity's class or a disease
_NOT_A_ROLE = re.compile(r"(?:局|委员会|委|中心|部|厅|署|办公室|政府|医院|agency|administration|ministry|"
                         r"department|commission|centre|center|hospital)$", re.IGNORECASE)
_INDICATION_FORM = re.compile(r"用于\s*$")
_INDICATION_TAIL = re.compile(r"^\s*(?:的)?治疗")


def role_proven(kind, text, start, end):
    """Whether the text gives evidence that text[start:end] is a value of the dimension `kind`."""
    value = text[start:end].strip()
    before, after = text[max(0, start - 12):start], text[end:end + 12]
    if not value or re.fullmatch(r"[\d\s.,%]+", value) or v3.DATE.fullmatch(value):
        return False  # a bare number or a date proves no role
    if kind in ("product_or_class", "entity", "disease") and _NOT_A_ROLE.search(value):
        return False
    if kind in _BEFORE and _BEFORE[kind].search(before):
        return True
    if kind in _AFTER and _AFTER[kind].search(after):
        return True
    if kind in _SELF and _SELF[kind].search(value):
        return True
    if kind == "indication" and _INDICATION_FORM.search(before) and _INDICATION_TAIL.search(after):
        return True
    return False


_SUBSET = re.compile(r"其中|包括|包含|含|另有|以及|及|和|including|of whom|of which|among them|and\b", re.IGNORECASE)


# the phrase a dimension stands in: bounded by clause punctuation and line ends ("、" joins, it does not bound)
_PHRASE_END = re.compile(r"[，。；;,:：()（）\n!?！？]")
_NEGATION = re.compile(r"非|不|未|无|排除|除去|去除|剔除|除了|除|不含|不包括|不计|以外|之外|"
                       r"\bnon-?|\bnot\b|\bno\b|excluding|except|other than|without", re.IGNORECASE)
_JOIN_LEFT = re.compile(r"(?:和|与|及|、|或|以及|跟|同|\band|\bor|\bplus|&|/)\s*$", re.IGNORECASE)
_JOIN_RIGHT = re.compile(r"^\s*(?:和|与|及|、|或|以及|跟|同|以外|之外|除外|外|and\b|or\b|plus\b|&|/)", re.IGNORECASE)


def qualifier_kept(text, start, end):
    """Whether text[start:end] is its phrase's own member as written: nothing in the phrase before it negates or
    excludes it, and no conjunction joins it with another member on either side (Codex r178)."""
    left = text[:start]
    cut = max((m.end() for m in _PHRASE_END.finditer(left)), default=0)
    before = left[cut:]
    m = _PHRASE_END.search(text, end)
    after = text[end:m.start() if m else len(text)]
    if _NEGATION.search(before) or _NEGATION.match(after.strip()):
        return False
    return not (_JOIN_LEFT.search(before) or _JOIN_RIGHT.search(after))


# the positive labels of each kind, matched as the WHOLE text before the colon / 为 (a prefix makes it another
# label: "不纳入人群" is no population label)
_LABELS = {
    "disease": r"病种|疾病|病名|传染病|disease",
    "population": r"人群|对象|研究人群|纳入人群|研究对象|队列|population|cohort",
    "date_basis": r"日期口径|统计口径|统计方式|date basis",
    "denominator_definition": r"分母|denominator",
    "product_or_class": r"产品|类别|品种|产品类别|product|class",
    "entity": r"企业|公司|单位|主体|entity|company",
    "indication": r"适应症|适应证|indication",
    "stage": r"阶段|状态|stage",
    "milestone_definition": r"里程碑|事项|milestone",
}
_HEADING_MARK = re.compile(r"^\s*#{1,6}\s*")
_VERB_BEFORE = re.compile(r"(?:报告|新增|累计|共|发现|出现|确诊|批准|注册|获批|受理|上市|投运|建成|在建|计划)$")
_CASE_AFTER = re.compile(r"^(?:确诊病例|疑似病例|死亡病例|病例|确诊|疑似|患者|感染者|感染|疫情|死亡|\s*(?:cases?|deaths?|"
                         r"infections?|outbreak)\b)", re.IGNORECASE)
_NUMBER_AFTER = re.compile(r"^\s*[\d０-９]")
_FRAME = re.compile(r"^(?:在|于)?(?P<x>.+?)(?:中|内|里|之中)?$")


def _bounds(text, start, end):
    """[ps, pe) of the phrase holding text[start:end] (clause marks and line ends bound it; 、 does not), and the
    mark just before it ("" at the start)."""
    left = text[:start]
    cut = max((m.end() for m in _PHRASE_END.finditer(left)), default=0)
    m = _PHRASE_END.search(text, end)
    return cut, (m.start() if m else len(text)), (text[cut - 1] if cut else "")


# the composites of the metric phrase: the kinds a run may hold, in order, and what must follow it
_CASE_RUN = ("population", "disease")
_NUMBER_RUN = ("product_or_class", "stage")


def whole_phrase(kind, text, start, end, members):
    """Whether text[start:end] fills a whole slot of a recognised structure for `kind`. members: (start, end,
    kind) of the item's OTHER dimensions in the same passage that are independently proven (v14) - the only
    ones a metric slot may border."""
    ps, pe, mark = _bounds(text, start, end)
    phrase = text[ps:pe].strip()
    value = text[start:end].strip()
    form = _SELF.get(kind)
    if mark and mark in ":：":
        # a labelled value: the label is the whole phrase before the colon, the value the whole phrase after it
        lps, _lpe, _ = _bounds(text, ps - 1, ps - 1)
        label = _HEADING_MARK.sub("", text[lps:ps - 1]).strip()
        return phrase == value and bool(re.fullmatch(_LABELS.get(kind, r"(?!)"), label, re.IGNORECASE))
    m = re.match(r"^(?:" + _LABELS.get(kind, r"(?!)") + r")\s*(?:为|是)\s*(?P<v>.+)$", phrase, re.IGNORECASE)
    if m:
        return m.group("v").strip() == value  # "人群为成人", "分母为检测人数"
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    line = text[line_start:len(text) if line_end < 0 else line_end]
    if _HEADING_MARK.match(line) and line.lstrip().startswith("#"):
        return _HEADING_MARK.sub("", line).strip() == value and bool(form and form.search(value))
    frame = _FRAME.match(phrase)
    if phrase == value or (frame and frame.group("x") == value and kind == "population"):
        return bool(form and form.search(value))  # a phrase of its own, or "在X中": the whole has the form
    # a slot of the metric phrase: the contiguous run of independently proven members holding this span
    run = [(start, end, kind)]
    while True:
        left = next((m for m in members if m[1] == run[0][0] and m not in run), None)
        if left is None:
            break
        run.insert(0, left)
    while True:
        right = next((m for m in members if m[0] == run[-1][1] and m not in run), None)
        if right is None:
            break
        run.append(right)
    kinds = [k for _a, _b, k in run]
    before, after = text[ps:run[0][0]], text[run[-1][1]:pe]
    if not _VERB_BEFORE.search(before):
        return False
    if all(k in _CASE_RUN for k in kinds) and kinds == sorted(set(kinds), key=_CASE_RUN.index):
        if "population" in kinds:
            pa, pb, _ = run[kinds.index("population")]
            if not (_SELF["population"].search(text[pa:pb].strip())):
                return False
        return bool(_CASE_AFTER.match(after))
    if kinds == [kind] and kind in _NUMBER_RUN:
        return bool(form and form.search(value)) and bool(_NUMBER_AFTER.match(after))
    return False


def related(L, P, span, vspan):
    """How the dimension span stands to the value, or None: the value's own sentence with no other quantity
    between them, or an effective ancestor heading. After the value, a subset mark between them, or another
    quantity later in the sentence, makes the dimension that quantity's ("10例，其中儿童3例"), never this one's."""
    if L.inside(span):
        if span["end"] <= vspan["start"]:
            if not L.numbers(span["end"], vspan["start"], [span, vspan]):
                return "dim_in_value_sentence"
            return None
        if span["start"] >= vspan["end"]:
            between = L.text(vspan["end"], span["start"])
            if (not L.numbers(vspan["end"], span["start"], [span, vspan]) and not _SUBSET.search(between)
                    and not L.numbers(span["end"], L.end, [span, vspan])):
                return "dim_in_value_sentence"
            return None
        if span["start"] <= vspan["start"] and vspan["end"] <= span["end"]:
            return None  # a dimension written over the value itself
        return "dim_in_value_sentence" if not L.numbers(span["start"], span["end"], [vspan]) else None
    if v10.heading_ancestor(P, span, vspan):
        return "dim_in_heading_ancestor"
    return None


def dims_of(item, P):
    """{kind: result} for every dimension the item states. Each dimension is first checked on its own (literal,
    role, qualifier, relation); only those that pass may bound another's metric slot (v14)."""
    out, ready = {}, []
    vspan = item["fields"]["value"]["span"]
    L = v10.Local(P, vspan)
    for d in item.get("dims") or []:
        span = d["span"]
        written = P.text(span)
        if written is None:
            out[d["kind"]] = outcome("mismatch", "dim_span_outside_passage")
            continue
        if squash(written).casefold() != squash(d["value"]).casefold():
            out[d["kind"]] = outcome("mismatch", "dim_differs_from_span")
            continue
        p = P.by_id[span["passage"]]
        if not role_proven(d["kind"], p["text"], span["start"] - p["start"], span["end"] - p["start"]):
            out[d["kind"]] = outcome("unsupported", "dim_kind_not_proven")  # the role first: never implied
            continue
        if not qualifier_kept(p["text"], span["start"] - p["start"], span["end"] - p["start"]):
            # negated, excluded or one member of a joined group: not the value as written (v12)
            out[d["kind"]] = outcome("unsupported", "dim_qualifier_dropped")
            continue
        place = related(L, P, span, vspan)
        if place is None:
            out[d["kind"]] = outcome("unsupported", "dim_relation_not_rule_checkable")
            continue
        ready.append((d, p, place))
    for d, p, place in ready:  # the whole slot, bounded only by independently proven neighbours (v14)
        span = d["span"]
        members = [(o["span"]["start"] - p["start"], o["span"]["end"] - p["start"], o["kind"])
                   for o, q, _ in ready if o is not d and o["span"]["passage"] == span["passage"]]
        if not whole_phrase(d["kind"], p["text"], span["start"] - p["start"], span["end"] - p["start"], members):
            # not a whole slot of a recognised structure: a part of a longer qualified phrase (v13/v14)
            out[d["kind"]] = outcome("unsupported", "dim_phrase_not_whole")
            continue
        out[d["kind"]] = outcome("match", place, span)
    return out


def check(request):
    """Contract v3: v10's measurement check with extraction_normalize.v4, and each stated dimension."""
    base = dict(request, items=[{k: v for k, v in i.items() if k != "dims"} for i in request["items"]])
    out = v10.check(base, unit_head=norm.unit_subject_head_v4, output=True)
    P = v3.Passages(request["passages"])
    for answered, item in zip(out["items"], request["items"]):
        answered["dims"] = dims_of(item, P)
    out["contract_version"], out["checker_version"] = CONTRACT_VERSION, CHECKER_VERSION
    return out
