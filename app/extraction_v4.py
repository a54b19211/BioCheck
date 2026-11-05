"""Extraction consistency, contract v2 / checker v4 (T-29 P3): measurements only, the v3 rules extended by the
smallest versioned set that lets explicit wording or structure prove what v3 left unsupported - the kind of a
count (flow / cumulative / stock), a canonical period with its type and precision, a scope stated or proven to
be the object's own total, a value qualifier and the modality. Everything the rules cannot prove stays
"unsupported". v3 (contract v1, /v1/extraction-consistency) is untouched and imported, not copied.

The quantity's own context is its local sentence: the v3 clause, also cut at an English sentence end, so other
numbers, dates or event words elsewhere in an English paragraph never support (or spoil) this value."""

import re
from datetime import date
from decimal import Decimal

from extraction_contract import normalize as norm

from . import extraction as v3
from .extraction import ContractError, outcome, squash

CONTRACT_VERSION = "extraction_consistency.v2"
# Bump whenever a rule, a word list, the unit table or canonical_period changes.
# v5 (Codex r135): exactness needs positive evidence; only the nearest effective heading may supply a field and
# any effective heading may restrict an object total; kind evidence, ratios and units belong to the value's own
# group; a unit span must be the whole recognized unit and "no unit" needs nothing but punctuation after it.
# v6 (Codex r136): the value's group is covered by the item's own fields (no qualifier left outside the metric);
# prefixes, suffixes and "no unit" are read over the bounded sentence, never a fixed short window; comparators are
# whole phrases with their negation; a percentage takes only a ratio written right next to it.
# v7 (Codex r137): a negation of the quantity's predicate outside the item's fields leaves the kind unproven.
# v8: v7 + extraction_normalize.v1 (numbers grouped by space / NBSP / NNBSP, English single years, the unit in
# the metric phrase bound to the value); v7 stays the frozen baseline on its own commit (45600af)
CHECKER_VERSION = "biocheck.extraction_rules.v8"
NORMALIZE_VERSION = norm.NORMALIZE_VERSION
FIELDS = ("object", "metric", "value", "unit", "period", "scope", "denominator", "measure_kind", "qualifier",
          "modality")
CANONICAL_DECIMAL = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d*[1-9])?")
MAX_DIGITS, MAX_DECIMALS = 18, 6

MONTHS = {m: i for i, m in enumerate(("january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"), 1)}
MONTH_RE = "(" + "|".join(m.capitalize() for m in MONTHS) + ")"
EN_DATE = re.compile(r"\b\d{1,2}\s+" + MONTH_RE + r"(?:\s+\d{4})?\b|\b" + MONTH_RE + r"\s+\d{4}\b|\b(?:19|20)\d{2}\b")
SENTENCE_END = re.compile(r"(?<=[a-z0-9\)\]%])\.\s+(?=[A-Z(\[])")

# Word lists (versioned with CHECKER_VERSION).
EVENT = re.compile(r"批准|获批|注册|受理|发布|报告|新增|新发|发生|上市|\b(?:recorded|reported|notified|approved|new)\b",
                   re.IGNORECASE)
CUMULATIVE = re.compile(r"累计|累积|\bcumulative(?:ly)?\b|\byear-to-date\b|年初至今|前\d{1,2}个?月|1\s*(?:至|-|—|~)\s*\d{1,2}月",
                        re.IGNORECASE)
AS_OF = re.compile(r"截至|截止|\bas of\b", re.IGNORECASE)
STATE = re.compile(r"现有|正在|在研|活跃|在册|有效|\b(?:active|currently|ongoing)\b", re.IGNORECASE)
TOTAL_WORD = re.compile(r"共|合计|总计|全部|\bin total\b", re.IGNORECASE)
RESTRICTION = re.compile(r"其中|包括|除|以外|不含|部分|省|市|地区|县|哨点|\b(?:among|of which|excluding|except|only|"
                         r"province|region|district)\b", re.IGNORECASE)
DEICTIC = re.compile(r"^(?:我国|本国|本省|本市|本院|我院|该|此|这|our\b|this\b|these\b|the country\b)", re.IGNORECASE)
CHANGE = re.compile(r"(?:下降|增长|上升|减少|提高|降低|增加|同比|环比|\bincreased?\b|\bdecreased?\b|\brose\b|\bfell\b|"
                    r"\bup\b|\bdown\b)\s*(?:了|by)?\s*$", re.IGNORECASE)
# exactness is proven, not presumed: what may stand right before an exact value (besides the start of its group
# or the end of one of the item's own field spans) - a closed list of neutral connectors
CN_CONNECT = set("共为是有达计了：:，,、（( |")
EN_CONNECT = {"of", "total", "is", "was", "were", "are", "reached", "reported", "recorded", "had", "has", "have",
              "been", "with", "at", "in", "from", "and", "were", "registered", "confirmed", "notified"}
# "no unit" is proven only by punctuation or the end of the sentence after the number (whitespace skipped)
NO_UNIT_AFTER = re.compile(r"^\s*(?:$|[，。；;,.!?！？)）\]】|/])")
# the quantity's own phrase must be covered by the item's fields (versioned word lists):
# - English: from the value (its unit) to the first auxiliary verb - prepositions and articles may join the
#   fields, an event verb only after the metric (a predicate, not a case definition between value and metric);
# - Chinese: the whole value group, only these function characters outside the fields.
GLUE = {"of", "in", "at", "on", "the", "a", "an", "and", "from", "with", "among", "for", "to", "by"}
AUX = re.compile(r"\b(?:had|has|have|was|were|is|are|been|be)\b", re.IGNORECASE)
CN_FUNCTION = set("共有为是达计了的已被中")
NEGATED = re.compile(r"\b(?:not|no|never|neither|nor|without|none)\b|n't|尚未|未|没有|没|无|不", re.IGNORECASE)
CJK = re.compile(r"[一-鿿]")
QUALIFIER_OF = {"约": "approximate", "近": "approximate", "左右": "approximate", "approximately": "approximate",
                "about": "approximate", "nearly": "approximate", "逾": "more_than", "超过": "more_than",
                "over": "more_than", "more than": "more_than", "余": "more_than", "多": "more_than",
                "不少于": "at_least", "至少": "at_least", "at least": "at_least", "up to": "at_most",
                "小于": "less_than", "少于": "less_than", "低于": "less_than", "不到": "less_than", "不足": "less_than",
                "未满": "less_than", "<": "less_than", "＜": "less_than", "less than": "less_than",
                "fewer than": "less_than", "under": "less_than", "below": "less_than", "≤": "at_most",
                "大于": "more_than", "多于": "more_than", "高于": "more_than", ">": "more_than", "＞": "more_than",
                "greater than": "more_than", "above": "more_than", "≥": "at_least",
                "以上": "at_least", "以下": "at_most", "以内": "at_most", "上下": "approximate", "之多": "more_than",
                "不等": "approximate", "+": "at_least", "or more": "at_least", "or fewer": "at_most",
                "or less": "at_most", "plus": "at_least",
                # negated comparators are whole phrases of their own (不超过 is an upper bound, not 超过)
                "不超过": "at_most", "未超过": "at_most", "不多于": "at_most", "不高于": "at_most", "不大于": "at_most",
                "不低于": "at_least", "不小于": "at_least", "not more than": "at_most", "no more than": "at_most",
                "not exceeding": "at_most", "not less than": "at_least", "no less than": "at_least",
                "not fewer than": "at_least", "no fewer than": "at_least"}
AFTER_MARKERS = ("余", "多", "左右", "上下", "以上", "以下", "以内", "之多", "不等", "+", "or more", "or fewer",
                 "or less", "plus")


def _phrases(words):
    """An alternation of whole phrases, longest first, ASCII words bounded."""
    out = []
    for w in sorted(words, key=len, reverse=True):
        e = re.escape(w)
        out.append(rf"\b{e}\b" if re.fullmatch(r"[A-Za-z ]+", w) else e)
    return "|".join(out)


BEFORE = re.compile(r"(?:" + _phrases([k for k in QUALIFIER_OF if k not in AFTER_MARKERS]) + r")$", re.IGNORECASE)
NEGATION = re.compile(r"(?:不|未|没|非|\bnot|\bno|\bnever)\s*$", re.IGNORECASE)
ADJACENT = set(" （）()[]【】%％")  # all that may stand between a percentage and the ratio it states
PLANNED = re.compile(r"计划|拟|目标|将|\b(?:plan(?:ned|s)?|target(?:ed)?)\b", re.IGNORECASE)
PROJECTED = re.compile(r"预计|预测|预期|\b(?:expected|projected|forecast)\b", re.IGNORECASE)
LIST_SEPARATOR = re.compile(r"、|,|，|\band\b|/|&")

# Units v4 knows beyond v3's table: count classifiers and count nouns are their own canonical form (个 is not 件).
CLASSIFIERS = ("品种", "品规", "批次", "个", "件", "批", "例", "人", "家", "项", "条", "种")
COUNT_NOUNS = ("cases", "deaths", "patients", "participants", "provinces", "samples")
MONEY = ("亿元", "万元", "元")
MODIFIERS = ("suspected", "confirmed", "probable", "laboratory-confirmed", "new", "human", "associated")


# ---- canonical_period.v1 -------------------------------------------------------------------------------------

CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "1": 1, "2": 2, "3": 3, "4": 4}


def _ym(y, m):
    return f"{int(y):04d}-{int(m):02d}" if 1 <= int(m) <= 12 else None


def _ymd(y, m, d):
    try:
        return date(int(y), int(m), int(d)).isoformat()
    except ValueError:
        return None  # 30 February, 29 February of a common year


def canonical_period(text):
    """(period dict, None) or (None, reason). A period is {"type": point|interval, "precision": day|month|quarter|
    half|year, "value"} or, for an interval spanning several units, {"start", "end"} instead of "value". No day or
    month the text does not state is added; a period without a year or relative to an unstated date is refused."""
    t = squash(text).translate(v3.FULLWIDTH)
    rules = [
        (r"(\d{4})年(\d{1,2})月", lambda y, m: ("interval", "month", _ym(y, m))),
        (r"(\d{4})年(?:第)?([一二三四1-4])季度", lambda y, q: ("interval", "quarter", f"{y}Q{CN_NUM[q]}")),
        (r"(\d{4})年(上|下)半年", lambda y, h: ("interval", "half", f"{y}H{1 if h == '上' else 2}")),
        (r"(\d{4})年(?:全年)?", lambda y: ("interval", "year", y)),
        (r"(\d{4})Q([1-4])", lambda y, q: ("interval", "quarter", f"{y}Q{q}")),
        (r"截[至止](\d{4})年(\d{1,2})月(\d{1,2})日", lambda y, m, d: ("point", "day", _ymd(y, m, d))),
        (r"截[至止](\d{4})年(\d{1,2})月(?:底|末)?", lambda y, m: ("point", "month", _ym(y, m))),
        (r"截[至止](\d{4})年(?:底|末)", lambda y: ("point", "year", y)),
        (r"(?:[Aa]s of )(\d{1,2}) " + MONTH_RE + r" (\d{4})", lambda d, mo, y: ("point", "day", _ymd(y, MONTHS[mo.lower()], d))),
        (r"(?:[Aa]s of )" + MONTH_RE + r" (\d{4})", lambda mo, y: ("point", "month", _ym(y, MONTHS[mo.lower()]))),
    ]
    for pattern, build in rules:
        m = re.fullmatch(pattern, t)
        if m:
            kind, precision, value = build(*m.groups())
            return ({"type": kind, "precision": precision, "value": value}, None) if value else (None, "invalid_date")
    m = re.fullmatch(r"(\d{4})年(?:前(\d{1,2})个?月|1\s*(?:至|-|—|~)\s*(\d{1,2})月)", t)
    if m:
        end = _ym(m.group(1), m.group(2) or m.group(3))
        return ({"type": "interval", "precision": "month", "start": f"{m.group(1)}-01", "end": end}, None) if end \
            else (None, "invalid_date")
    # English ranges: [from|between] [D1] M1 [Y1] to|and|until|through [D2] M2 Y2
    m = re.fullmatch(r"(?:(?:[Ff]rom|[Bb]etween|[Ss]ince)\s+)?(\d{1,2}\s+)?" + MONTH_RE + r"(\s+\d{4})?\s+"
                     r"(?:to|and|until|through)\s+(\d{1,2}\s+)?" + MONTH_RE + r"\s+(\d{4})", t)
    if m:
        d1, m1, y1, d2, m2, y2 = m.groups()
        m1, m2, y2 = MONTHS[m1.lower()], MONTHS[m2.lower()], int(y2)
        y1 = int(y1) if y1 else (y2 if m1 <= m2 else y2 - 1)  # the stated year carried back; across a new year, the one before
        if bool(d1) != bool(d2):
            return None, "mixed_precision_range"
        if d1:
            start, end, precision = _ymd(y1, m1, d1.strip()), _ymd(y2, m2, d2.strip()), "day"
        else:
            start, end, precision = _ym(y1, m1), _ym(y2, m2), "month"
        if not start or not end:
            return None, "invalid_date"
        if start > end:
            return None, "range_ends_before_it_starts"
        return {"type": "interval", "precision": precision, "start": start, "end": end}, None
    year = norm.english_year(t)
    if year:
        return year, None
    if re.search(r"近\d+年|今年|去年|上月|本月|上半年|下半年|\bpast\b|\blast\b|\bthis year\b|\brecent", t) and not re.search(r"\d{4}", t):
        return None, "relative_period"
    if re.search(MONTH_RE, t) and not re.search(r"\d{4}", t):
        return None, "period_year_not_stated"
    return None, "period_form_not_supported"


def inclusive_days(period):
    a, b = date.fromisoformat(period["start"]), date.fromisoformat(period["end"])
    return (b - a).days + 1


# ---- the quantity's local context ------------------------------------------------------------------------------

class Local:
    """The value's local sentence and group inside one passage, and the numbers in them."""

    def __init__(self, P, vspan):
        pid, c_start, c_end, _ = P.clause(vspan)
        self.passage = P.by_id[pid]
        base, text = self.passage["start"], self.passage["text"]
        # cut the v3 clause at English sentence ends around the value
        clause = text[c_start - base:c_end - base]
        cuts = [c_start + m.end() for m in SENTENCE_END.finditer(clause)]
        self.start = max([c for c in cuts if c <= vspan["start"]], default=c_start)
        ends = [c_start + m.start() + 1 for m in SENTENCE_END.finditer(clause) if c_start + m.start() + 1 >= vspan["end"]]
        self.end = min(ends, default=c_end)
        g_start, g_end = P.group(vspan)
        self.g_start, self.g_end = max(g_start, self.start), min(g_end, self.end)
        self.pid, self.base = pid, base

    def text(self, a=None, b=None):
        a, b = self.start if a is None else a, self.end if b is None else b
        return self.passage["text"][a - self.base:b - self.base]

    def inside(self, span, group=False):
        a, b = (self.g_start, self.g_end) if group else (self.start, self.end)
        return span["passage"] == self.pid and a <= span["start"] and span["end"] <= b

    def numbers(self, a, b, exclude=()):
        """Numbers in [a, b) that are not dates and not inside any excluded span."""
        text = self.text(a, b)
        blank = lambda m: " " * len(m.group())  # noqa: E731
        text = EN_DATE.sub(blank, v3.DATE.sub(blank, v3.TIMESTAMP.sub(blank, text)))
        out = []
        for ms, me in norm.number_tokens(text):
            s, e = a + ms, a + me
            if not any(x["start"] <= s and e <= x["end"] for x in exclude):
                out.append((s, e))
        return out


# v3's ratio and total patterns with the shared number form (comma or space / NBSP / NNBSP groups of three)
_N = r"(\d{1,3}(?:,\d{3})+|\d{1,3}(?:[ \u00a0\u202f]\d{3})+|\d+)(?![\d,])"
RATIOS = (
    (re.compile(_N + r"\s*/\s*" + _N), 1, 2),
    (re.compile(_N + r"\s*(?:例|人|名)?\s*(?:of|out of)\s+" + _N, re.IGNORECASE), 1, 2),
    (re.compile(_N + r"\s*(?:例|人|名)(?:患者|受试者|病例)?中[^0-9；;。]{0,12}?" + _N), 2, 1),
)
TOTAL = re.compile(r"(?:\b[Nn]\s*=\s*|共\s*|总计\s*|合计\s*)" + _N)


def ratios_in(clause_text, offset):
    """v3.ratios_in with the shared number form: [(num_start, num_end, num, den_start, den_end, den)]."""
    text = v3.DATE.sub(lambda m: " " * len(m.group()), v3.TIMESTAMP.sub(lambda m: " " * len(m.group()), clause_text))
    out = []
    for pattern, n, d in RATIOS:
        for m in pattern.finditer(text):
            num, den = norm.decimal_text(m.group(n)), norm.decimal_text(m.group(d))
            out.append((offset + m.start(n), offset + m.end(n), Decimal(num) if num else None,
                        offset + m.start(d), offset + m.end(d), Decimal(den) if den else None))
    return out


def whole_number(P, span):
    """The span is exactly one written number of its passage - a space-grouped number is one (v8)."""
    p = P.by_id[span["passage"]]
    return norm.is_whole_number(p["text"], span["start"] - p["start"], span["end"] - p["start"])


def bound_unit(P, f, unit, vspan):
    """The stated unit is a word of the stated metric phrase that starts right after the value, nothing between
    the value and the unit but words (extraction_normalize.v1 unit_bound); the metric span must hold its value."""
    metric = f["metric"]
    if not v3.stated(metric) or len({vspan["passage"], unit["span"]["passage"], metric["span"]["passage"]}) != 1:
        return False
    mtext = P.text(metric["span"])
    if mtext is None or squash(mtext).casefold() != squash(metric["value"]).casefold():
        return False
    p = P.by_id[vspan["passage"]]
    b = p["start"]
    return norm.unit_bound(p["text"], vspan["end"] - b, unit["span"]["start"] - b, unit["span"]["end"] - b,
                           metric["span"]["start"] - b, metric["span"]["end"] - b)


def heading_ancestor(P, span, vspan):
    """True when span lies in a markdown heading line that is an effective ancestor of the value: same passage,
    before it, no heading of the same or a higher level in between, and the heading names one thing (no list)."""
    if span["passage"] != vspan["passage"]:
        return False
    p = P.by_id[span["passage"]]
    text, base = p["text"], p["start"]
    ls = text.rfind("\n", 0, span["start"] - base) + 1
    le = text.find("\n", span["start"] - base)
    line = text[ls:len(text) if le < 0 else le]
    m = re.match(r"(#{1,6})\s", line)
    if not m or span["end"] - base > (len(text) if le < 0 else le) or span["start"] >= vspan["start"]:
        return False
    level = len(m.group(1))
    between = text[(len(text) if le < 0 else le):vspan["start"] - base]
    if any(len(h) <= level for h in re.findall(r"(?m)^(#{1,6})\s", between)):
        return False
    return not LIST_SEPARATOR.search(line[m.end():])


def ancestor_headings(P, vspan):
    """The value's effective ancestor headings within its passage, nearest first: [(start, end, text)] in
    artifact offsets of each heading line's text (after the #s)."""
    p = P.by_id[vspan["passage"]]
    text = p["text"][:vspan["start"] - p["start"]]
    out, level, pos = [], 7, 0
    lines = []
    for line in text.split("\n")[:-1]:
        lines.append((pos, line))
        pos += len(line) + 1
    for at, line in reversed(lines):
        m = re.match(r"(#{1,6})\s+(.*)", line)
        if m and len(m.group(1)) < level:
            out.append((p["start"] + at + m.start(2), p["start"] + at + m.end(2), m.group(2)))
            level = len(m.group(1))
    return out


def headings_over(P, vspan):
    """The text of the value's effective ancestor headings (nearest first), within its passage."""
    return [t for _, _, t in ancestor_headings(P, vspan)]


def in_nearest_heading(P, span, vspan):
    """The span lies in the nearest effective ancestor heading: a more specific heading below a broader one
    always wins, so a field taken from the broader one is not proven."""
    heads = ancestor_headings(P, vspan)
    return bool(heads) and heads[0][0] <= span["start"] and span["end"] <= heads[0][1] \
        and heading_ancestor(P, span, vspan)


def table_caption(P, vspan):
    """The non-table line right above the value's table, when the value is in a table row."""
    if not P.row(vspan):
        return ""
    p = P.by_id[vspan["passage"]]
    lines = p["text"][:vspan["start"] - p["start"]].split("\n")[:-1]
    for line in reversed(lines):
        if not line.startswith("|"):
            return line
    return ""


# ---- the checker -------------------------------------------------------------------------------------------------

def unit_after(text, pos):
    """(spelling, start, end) of the unit at the value: a classifier, %, money or a v3 unit right at it, or a count
    noun after at most two listed modifiers; None."""
    p = pos
    while p < len(text) and text[p] == " ":
        p += 1
    for spelling in sorted(CLASSIFIERS + MONEY, key=len, reverse=True):
        if text.startswith(spelling, p) and not text.startswith("个月", p):
            return spelling, p, p + len(spelling)
    known = v3.unit_at(text, pos)
    if known:
        return known[0], known[1], known[2]
    q, words = p, 0
    while words <= 2:
        for noun in COUNT_NOUNS:
            if text[q:q + len(noun)].lower() == noun and not text[q + len(noun):q + len(noun) + 1].isalpha():
                return noun, q, q + len(noun)
        m = re.match(r"(" + "|".join(MODIFIERS) + r")\s+", text[q:], re.IGNORECASE)
        if not m or words == 2:
            break
        q, words = q + m.end(), words + 1
    return None


def cover_of(f, vspan, partners, ptext, pbase, L):
    """The spans of the value's group that the item's own fields account for: every stated field span, the
    ratio partners, and the words whose meaning a checked field carries - the cumulative marker of a claimed
    cumulative, the plan / forecast word of a claimed modality, the comparator of a claimed qualifier (a wrong
    claim fails that field). Event verbs never do: they belong in the metric."""
    cover = [vspan] + [x["span"] for x in (f["object"], f["metric"], f["scope"], f["unit"]) if v3.stated(x)]
    cover += [x["span"] for x in (f["period"], f["denominator"]) if v3.stated(x)] + list(partners)
    markers = []
    if f["measure_kind"] == "cumulative":
        markers.append(CUMULATIVE)
    if f["modality"] != "actual":
        markers += [PLANNED, PROJECTED]
    if f["qualifier"] != "exact":
        markers.append(re.compile(_phrases(list(QUALIFIER_OF)), re.IGNORECASE))
    for pattern in markers:
        for m in pattern.finditer(ptext, L.g_start - pbase, L.g_end - pbase):
            cover.append({"start": pbase + m.start(), "end": pbase + m.end()})
    return cover


def next_to(ptext, pbase, ratio, vspan):
    """The ratio is written right next to the percentage - 26% (5/19), 23 of 26 (88%) - with nothing but
    spaces, brackets or the percent sign in between: a ratio elsewhere in the group may be another population's."""
    a, b = min(ratio[0], ratio[3]), max(ratio[1], ratio[4])
    between = ptext[b - pbase:vspan["start"] - pbase] if b <= vspan["start"] else ptext[vspan["end"] - pbase:a - pbase]
    return set(between) <= ADJACENT


def own_place(L, P, span, vspan, partners, exclude):
    """Where a field span stands to the value: "group" (its group, no other quantity), "before" (same sentence,
    before the value, no other quantity in between), "heading" (an effective ancestor heading), or why not."""
    if L.inside(span, group=True):
        others = L.numbers(L.g_start, L.g_end, exclude + [span] + partners)
        return "several" if others else "group"
    if L.inside(span) and span["end"] <= vspan["start"]:
        return "several" if L.numbers(span["end"], vspan["start"], exclude + partners) else "before"
    if in_nearest_heading(P, span, vspan):
        return "heading"
    return "outside"


def check_v4(item, P):
    """Field results of one measurement item under v4 (locator checked first, as v3)."""
    fields = {}
    quote = P.text(item["locator"])
    if quote is None:
        fields["locator"] = outcome("mismatch", "locator_outside_passage")
    elif squash(quote) == squash(item["quote"]):
        fields["locator"] = outcome("match", span=item["locator"])
    else:
        anywhere = any(squash(item["quote"]) in squash(p["text"]) for p in P.by_id.values())
        fields["locator"] = outcome("mismatch", "quote_elsewhere_in_passages" if anywhere else "quote_not_in_passages")
    if fields["locator"]["result"] != "match":
        for name in FIELDS:
            fields[name] = outcome("unsupported", "no_valid_location")
        return fields
    f = item["fields"]
    vf = f["value"]
    vspan = vf["span"]
    vtext = P.text(vspan)
    if vtext is None:
        fields["value"] = outcome("mismatch", "span_outside_passage")
        for name in FIELDS:
            fields.setdefault(name, outcome("unsupported", "no_valid_value_span"))
        return fields
    if not P.inside(vspan, item["locator"]):
        fields["value"] = outcome("mismatch", "value_outside_quote")
    elif not whole_number(P, vspan):
        fields["value"] = outcome("mismatch", "value_span_not_a_whole_number")
    elif norm.decimal_text(vtext) is None:
        fields["value"] = outcome("unsupported", "value_not_a_plain_number")
    elif Decimal(norm.decimal_text(vtext)) != Decimal(vf["value"]):
        fields["value"] = outcome("mismatch", "value_differs_from_span")
    else:
        fields["value"] = outcome("match", span=vspan)
    L = Local(P, vspan)
    ptext, pbase = L.passage["text"], L.base
    after = vspan["end"] - pbase
    # unit
    at_value = unit_after(ptext, after)
    unit = f["unit"]
    if v3.stated(unit):
        utext = P.text(unit["span"])
        if utext is None or squash(utext).casefold() != squash(unit["value"]).casefold():
            fields["unit"] = outcome("mismatch", "unit_differs_from_span")
        elif at_value and (unit["span"]["passage"], unit["span"]["start"], unit["span"]["end"]) == (
                L.pid, pbase + at_value[1], pbase + at_value[2]):
            fields["unit"] = outcome("match", "unit_at_value", unit["span"])  # the whole recognized unit, exactly
        elif at_value:
            fields["unit"] = outcome("mismatch", "unit_span_is_not_the_unit_at_value:" + at_value[0])
        elif bound_unit(P, f, unit, vspan):
            fields["unit"] = outcome("match", "unit_in_bound_metric", unit["span"])
        else:
            fields["unit"] = outcome("unsupported", "unit_not_at_value_or_not_in_table")
    else:
        if at_value:
            fields["unit"] = outcome("mismatch", "unit_written_at_value:" + at_value[0])
        elif NO_UNIT_AFTER.match(ptext[after:L.end - pbase]):  # the rest of the sentence, whitespace skipped
            fields["unit"] = outcome("match", "no_unit_at_value")  # only punctuation follows the number
        else:
            fields["unit"] = outcome("unsupported", "unrecognized_text_after_value")  # not proven unitless
    # denominator: a ratio of the value's own group (a percentage never borrows another population's ratio);
    # any ratio of the sentence still keeps "no denominator" from being proven
    ratios = ratios_in(L.text(), L.start)
    own_ratios = [r for r in ratios if L.g_start <= min(r[0], r[3]) and max(r[1], r[4]) <= L.g_end]
    mine = [r for r in own_ratios if (r[0], r[1]) == (vspan["start"], vspan["end"])]
    den = f["denominator"]
    partners = []
    percent_written = ptext[after:after + 1] in ("%", "％")
    if v3.stated(den):
        dtext = P.text(den["span"])
        if dtext is None or norm.decimal_text(dtext) is None or Decimal(norm.decimal_text(dtext)) != Decimal(
                den["value"]):
            fields["denominator"] = outcome("mismatch", "denominator_differs_from_span")
        elif any((r[3], r[4]) == (den["span"]["start"], den["span"]["end"]) for r in mine) or (
                percent_written and any((r[3], r[4]) == (den["span"]["start"], den["span"]["end"])
                                        and next_to(ptext, pbase, r, vspan) for r in own_ratios)):
            fields["denominator"] = outcome("match", span=den["span"])
            for r in own_ratios:
                if (r[3], r[4]) == (den["span"]["start"], den["span"]["end"]):
                    partners += [{"start": r[0], "end": r[1]}, {"start": r[3], "end": r[4]}]
        else:
            fields["denominator"] = outcome("unsupported", "denominator_relation_not_rule_checkable")
    else:
        # a total word of the value's own group (国家药监局共批准…318个) is the value itself, not another total
        other_total = [m for m in TOTAL.finditer(L.text())
                       if not (L.start + m.start(1) == vspan["start"] and L.start + m.end(1) == vspan["end"])]
        fields["denominator"] = (outcome("mismatch", "the_value_has_a_stated_denominator") if mine
                                 else outcome("unsupported", "other_ratio_or_total_in_value_sentence")
                                 if (ratios or other_total)
                                 else outcome("match", "no_denominator_in_value_sentence"))
    for r in mine:
        partners += [{"start": r[0], "end": r[1]}, {"start": r[3], "end": r[4]}]
    partners.append({"start": vspan["start"], "end": vspan["end"]})
    spans = [x["span"] for x in (f["object"], f["metric"], f["scope"], f["period"]) if v3.stated(x)]
    # period
    pf = f["period"]
    period = None
    if not v3.stated(pf):
        fields["period"] = outcome("unsupported", "period_not_stated")
    else:
        ptxt = P.text(pf["span"])
        got, why = canonical_period(ptxt or "")
        if ptxt is None:
            fields["period"] = outcome("mismatch", "period_span_outside_passage")
        elif got is None:
            fields["period"] = outcome("unsupported", why)
        elif got != pf["value"]:
            fields["period"] = outcome("mismatch", "period_differs_from_canonical_span")
        else:
            place = own_place(L, P, pf["span"], vspan, partners, [s for s in spans if s is not pf["span"]])
            before = ptext[max(0, pf["span"]["start"] - pbase - 24):pf["span"]["start"] - pbase]
            past = re.search(r"past (\d+) days\s*\(\s*$", before, re.IGNORECASE)
            if past and got.get("precision") == "day" and "start" in got and int(past.group(1)) != inclusive_days(got):
                fields["period"] = outcome("mismatch", "past_n_days_disagrees_with_dates")
            elif place in ("group", "before"):
                fields["period"] = outcome("match", "period_" + place, pf["span"])
                period = got
            else:
                fields["period"] = outcome("unsupported", "several_quantities_in_value_sentence" if place == "several"
                                           else "period_not_in_value_sentence")
    # object and metric
    for name in ("object", "metric"):
        x = f[name]
        if not v3.stated(x):
            fields[name] = outcome("unsupported", f"unknown_{name}_not_rule_checkable")
            continue
        text = P.text(x["span"])
        if text is None or squash(text).casefold() != squash(x["value"]).casefold():
            fields[name] = outcome("mismatch", f"{name}_differs_from_span")
            continue
        if name == "object" and DEICTIC.search(squash(x["value"])):
            fields[name] = outcome("unsupported", "deictic_object")
            continue
        place = own_place(L, P, x["span"], vspan, partners, [s for s in spans if s is not x["span"]])
        if place in ("group", "before", "heading"):
            fields[name] = outcome("match", f"{name}_{place}", x["span"])
        elif place == "several":
            fields[name] = outcome("unsupported", "several_quantities_in_value_sentence")
        else:
            column = v3.header_column(P, x["span"], vspan)
            fields[name] = (outcome("match", "header_of_value_column", x["span"]) if column is True
                            else outcome("mismatch", "header_of_" + column) if column in ("another_column", "another_table")
                            else outcome("unsupported", f"{name}_not_in_value_sentence"))
    # scope
    sf, mode = f["scope"], f["scope_mode"]
    if mode == "stated":
        if not v3.stated(sf):
            raise ContractError(f"items[{item['id']}]: scope_mode stated needs a stated scope")
        text = P.text(sf["span"])
        if text is None or squash(text).casefold() != squash(sf["value"]).casefold():
            fields["scope"] = outcome("mismatch", "scope_differs_from_span")
        else:
            place = own_place(L, P, sf["span"], vspan, partners, [s for s in spans if s is not sf["span"]])
            fields["scope"] = (outcome("match", "scope_" + place, sf["span"]) if place in ("group", "before", "heading")
                               else outcome("unsupported", "scope_not_in_value_sentence"))
    elif mode == "object_metric_total":
        if v3.stated(sf):
            raise ContractError(f"items[{item['id']}]: scope_mode object_metric_total states no scope")
        group = L.text(L.g_start, L.g_end)
        covered = [x["span"] for x in (f["object"], f["metric"]) if v3.stated(x)] + [vspan]
        if pf and v3.stated(pf):
            covered.append(pf["span"])
        rest = "".join(" " if any(c["start"] <= L.g_start + i < c["end"] for c in covered) else ch
                       for i, ch in enumerate(group))
        lead = L.text(L.start, L.g_start)
        lead_rest = "".join(" " if any(c["start"] <= L.start + i < c["end"] for c in covered) else ch
                            for i, ch in enumerate(lead))
        total = TOTAL_WORD.search(rest)
        # an effective heading over the value may narrow it (# 湖北省内): a total is proven only under headings
        # that name the object itself and carry no restriction
        heads = [t for _, _, t in ancestor_headings(P, vspan)]
        obj = squash(f["object"]["value"]) if v3.stated(f["object"]) else ""
        if fields["object"]["result"] != "match" or fields["metric"]["result"] != "match":
            fields["scope"] = outcome("unsupported", "object_metric_total_needs_checked_object_and_metric")
        elif not total:
            fields["scope"] = outcome("unsupported", "no_total_word_in_value_group")
        elif RESTRICTION.search(rest) or RESTRICTION.search(lead_rest):
            fields["scope"] = outcome("unsupported", "restriction_outside_the_fields")
        elif any(obj not in squash(h) or RESTRICTION.search(h.replace(obj, " ")) for h in heads):
            fields["scope"] = outcome("unsupported", "heading_may_restrict_the_total")
        else:
            fields["scope"] = outcome("match", "object_metric_total",
                                      {"passage": L.pid, "start": L.g_start + total.start(), "end": L.g_start + total.end()})
    else:
        fields["scope"] = outcome("unsupported", "scope_not_determined")
    # the quantity's own phrase is covered by the item's fields: a qualifier left out of the metric
    # (suspected, in humans, 批准注册) changes what is counted, so the metric does not hold
    cover = cover_of(f, vspan, partners, ptext, pbase, L)

    def covered(pos):
        return any(c["start"] <= pos < c["end"] for c in cover)

    # a negated predicate of the quantity (had not been recorded, 未报告) is no affirmative event: whatever
    # negation stands in the value's group outside the item's own fields leaves the kind unproven
    negation = [m.group(0) for m in NEGATED.finditer(ptext, L.g_start - pbase, L.g_end - pbase)
                if not covered(pbase + m.start())]
    if fields["metric"]["result"] == "match":
        gtext = ptext[L.g_start - pbase:L.g_end - pbase]
        left_out = []
        if CJK.search(gtext):
            left_out = [ch for i, ch in enumerate(gtext)
                        if CJK.match(ch) and ch not in CN_FUNCTION and not covered(L.g_start + i)]
        else:
            aux = AUX.search(ptext, vspan["end"] - pbase, L.g_end - pbase)
            zone_end = pbase + aux.start() if aux else L.g_end
            metric_end = f["metric"]["span"]["end"]
            for w in re.finditer(r"[A-Za-z][A-Za-z-]*", ptext[vspan["end"] - pbase:zone_end - pbase]):
                pos = vspan["end"] + w.start()
                word = w.group(0).lower()
                if covered(pos) or word in GLUE or (pos >= metric_end and EVENT.fullmatch(word)):
                    continue
                left_out.append(w.group(0))
        if left_out:
            fields["metric"] = outcome("unsupported", "quantity_words_outside_the_fields:" + " ".join(left_out)[:60])
    # measure kind
    kind = f["measure_kind"]
    sentence, vgroup = L.text(), L.text(L.g_start, L.g_end)
    # positive evidence of the kind belongs to the value's own group and its own period - never a word said of
    # another object elsewhere in the sentence; such a word anywhere still vetoes the simpler reading (flow)
    own = vgroup + " " + ((P.text(pf["span"]) or "") if v3.stated(pf) else "")
    cumulative = CUMULATIVE.search(own)
    cumulative_anywhere = CUMULATIVE.search(sentence)
    if negation:
        fields["measure_kind"] = outcome("unsupported", "negated_predicate:" + negation[0])
    elif kind == "rate":
        before = ptext[max(0, vspan["start"] - pbase - 12):vspan["start"] - pbase]
        if CHANGE.search(before):
            fields["measure_kind"] = outcome("unsupported", "percent_change")
        elif fields["value"]["result"] == "match" and (percent_written or fields["denominator"]["result"] == "match"
                                                       and v3.stated(den)):
            fields["measure_kind"] = outcome("match", "rate_with_percentage_or_ratio")
        else:
            fields["measure_kind"] = outcome("unsupported", "measure_kind_not_rule_checkable")
    elif period is None:
        fields["measure_kind"] = outcome("unsupported", "kind_needs_a_checked_period")
    elif kind == "flow":
        if period["type"] == "point":
            fields["measure_kind"] = outcome("mismatch", "flow_needs_an_interval")
        elif cumulative_anywhere or AS_OF.search(sentence):
            fields["measure_kind"] = outcome("unsupported", "cumulative_or_as_of_wording")
        elif EVENT.search(vgroup):
            fields["measure_kind"] = outcome("match", "event_in_interval")
        else:
            fields["measure_kind"] = outcome("unsupported", "no_event_wording")
    elif kind == "cumulative":
        fields["measure_kind"] = (outcome("match", "cumulative_wording") if cumulative
                                  else outcome("unsupported", "no_cumulative_wording"))
    elif kind == "stock":
        if period["type"] != "point":
            fields["measure_kind"] = outcome("mismatch", "stock_needs_a_point")
        elif cumulative_anywhere or EVENT.search(vgroup):
            fields["measure_kind"] = outcome("unsupported", "event_or_cumulative_wording")
        elif AS_OF.search(P.text(pf["span"]) or "") and STATE.search(vgroup):
            fields["measure_kind"] = outcome("match", "as_of_with_state_word")
        else:
            fields["measure_kind"] = outcome("unsupported", "no_state_wording")
    # qualifier: a marker at the value must agree with the stated qualifier; "exact" is proven, not presumed -
    # what stands right before the value is the start of its group, the end of one of the item's own field spans
    # or a neutral connector, and no bounding suffix follows it
    rel = vspan["start"] - pbase
    left = ptext[L.g_start - pbase:rel].rstrip()  # from the group's start, whitespace before the value skipped
    right_at = at_value[2] if at_value else after
    right = ptext[right_at:L.end - pbase].lstrip()  # to the sentence's end, whitespace after the value skipped
    marks, negated = [], False
    m = BEFORE.search(left)
    if m:
        marks.append(QUALIFIER_OF[squash(m.group(0)).lower()])
        negated = bool(NEGATION.search(left[:m.start()]))  # 不 / not before a phrase this list does not hold
    for marker in sorted(AFTER_MARKERS, key=len, reverse=True):
        if right.lower().startswith(marker):
            marks.append(QUALIFIER_OF[marker])
            break
    want = f["qualifier"]
    if negated:
        fields["qualifier"] = outcome("unsupported", "negated_comparator_not_supported")
    elif marks:
        fields["qualifier"] = (outcome("match", "qualifier_marker") if want in marks
                               else outcome("mismatch", "value_is_qualified:" + marks[0]))
    elif want != "exact":
        fields["qualifier"] = outcome("mismatch", "no_qualifier_marker")
    else:
        gap = ptext[L.g_start - pbase:rel].rstrip()
        field_ends = {x["span"]["end"] for x in (f["object"], f["metric"], f["scope"]) if v3.stated(x)}
        if v3.stated(pf):
            field_ends.add(pf["span"]["end"])
        word = re.search(r"([A-Za-z-]+)$", gap)
        if not gap.strip(" ，,、（(|") or L.g_start + len(gap) in field_ends or gap[-1] in CN_CONNECT \
                or (word and word.group(1).lower() in EN_CONNECT):
            fields["qualifier"] = outcome("match", "exact_by_position")
        else:
            fields["qualifier"] = outcome("unsupported", "exactness_not_provable")
    # modality: the sentence, the effective headings and the table caption
    context = " ".join([sentence] + headings_over(P, vspan) + [table_caption(P, vspan)])
    found = "projected" if PROJECTED.search(context) else "planned" if PLANNED.search(context) else "actual"
    fields["modality"] = (outcome("match", "modality_" + found) if f["modality"] == found
                          else outcome("mismatch", "modality_is_" + found))
    return fields


def check(request):
    """Contract v2: measurement items only. Raises ContractError on a contract break."""
    passages = request["passages"]
    if len({p["id"] for p in passages}) != len(passages):
        raise ContractError("passages: ids must be unique")
    for p in passages:
        if p["end"] - p["start"] != len(p["text"]):
            raise ContractError(f"passages[{p['id']}]: end - start must equal the length of its text")
    ordered = sorted(passages, key=lambda p: p["start"])
    if any(a["end"] > b["start"] for a, b in zip(ordered, ordered[1:])):
        raise ContractError("passages: ranges of one artifact must not overlap")
    if sum(len(p["text"]) for p in passages) > 20000:
        raise ContractError("passages: at most 20000 characters in all")
    if len({i["id"] for i in request["items"]}) != len(request["items"]):
        raise ContractError("items: ids must be unique")
    P = v3.Passages(passages)
    items = []
    for item in request["items"]:
        fields = check_v4(item, P)
        items.append({"id": item["id"], "kind": item["kind"], "status": v3.status(fields), "fields": fields})
    counts = {s: sum(1 for i in items if i["status"] == s) for s in ("consistent", "inconsistent", "unchecked")}
    return {"contract_version": CONTRACT_VERSION, "checker_version": CHECKER_VERSION,
            "subject": request["subject"], "items": items, "counts": counts, "llm_calls": []}
