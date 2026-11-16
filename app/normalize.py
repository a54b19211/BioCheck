"""BioCheck's normalization (extraction_normalize.v1 and later): how the checkers read a written number, an
English year and a unit. BioCheck owns these rules; Fabric's measure producer keeps a copy of the number rules it
needs (clean/numbers.py) and follows this file.

- Numbers: plain digits, thousands commas, or thousands grouped by a space, NO-BREAK SPACE or NARROW NO-BREAK SPACE -
  every group after the first exactly three digits, one separator kind per number; optional decimals. More than
  MAX_DIGITS significant digits or MAX_DECIMALS decimals is not a number here. A wrongly grouped run ("78 51",
  "1 44 4") is not one number: its parts are separate tokens.
- English single year: "2018", "in 2018", "In 2018", "during 2018" -> the year as an interval of year precision.
  Nothing else is inferred (no month or day added; a range of mixed precision stays unsupported elsewhere).
- Unit in the bound metric phrase: the unit is a word inside the metric phrase that starts right after the value
  (only spaces between), with nothing between the value and the unit but words - no other number, no negation or
  comparison word, no separator (, ; : / ( ) or 'and'/'or') - so "14 close contacts" binds contacts to 14, and
  "14 cases and 3 contacts" never binds contacts to 14.

What the later versions add to v1 (the v2 and v3 functions are gone):

- v4: unit_subject_head_v4 - the unit is read on the metric phrase's own subject; a subset named after including /
  excluding / such as / a comma is never the unit, and a single letter is a word ("hepatitis E cases").
- v5: period_span_whole_v5 - a period span must be the whole written period; a complete written date before the
  span is read as its own.
- v6: decimal_text_v6 / is_whole_number_v6 - a count written as one of the words one to twelve is a number.
"""

import re
from decimal import Decimal, InvalidOperation

NORMALIZE_VERSION = "extraction_normalize.v1"
MAX_DIGITS, MAX_DECIMALS = 18, 6
GROUP_SPACES = "   "
FULLWIDTH = str.maketrans("０１２３４５６７８９．，－", "0123456789.,-")
_SPACE_GROUPED = r"\d{1,3}(?:(?P<sep>[   ])\d{3})(?:(?P=sep)\d{3})*"
# Scanned left to right: a grouped number is consumed whole, so its tail ("851" of "7 851") never starts a token;
# a four-digit run cannot head a space group, so "2023 1 444" is two numbers.
_TOKEN = re.compile(
    r"(?<![\d.,])[-−]?(?:\d{1,3}(?:,\d{3})+|" + _SPACE_GROUPED + r"|\d+)(?:\.\d+)?(?![\d]|[.,]\d)"
)
_WHOLE = re.compile(r"[-−]?(?:\d{1,3}(?:,\d{3})+|" + _SPACE_GROUPED + r"|\d+)(?:\.\d+)?")
_YEAR = re.compile(r"(?:(?:[Ii]n|[Dd]uring)\s+)?((?:19|20)\d{2})")
_NEGATION_OR_COMPARISON = re.compile(
    r"\b(?:not|no|never|none|without|non|than|more|less|fewer|over|under|above|below|about|approximately|nearly|"
    r"almost|around|least|most|up|and|or)\b", re.IGNORECASE)
_SEPARATOR = re.compile(r"[,;:/()\[\]]|\d")


def decimal_text(written):
    """The canonical decimal string of a written number, or None (never through a float)."""
    t = (written or "").strip().translate(FULLWIDTH).replace("−", "-")
    if not _WHOLE.fullmatch(t):
        return None
    plain = t.replace(",", "")
    for s in GROUP_SPACES:
        plain = plain.replace(s, "")
    try:
        d = Decimal(plain)
    except InvalidOperation:
        return None
    text = format(d.normalize(), "f") if d != 0 else "0"
    whole, _, frac = text.lstrip("-").partition(".")
    if len(whole.lstrip("0") + frac) > MAX_DIGITS or len(frac) > MAX_DECIMALS:
        return None
    return text


def number_tokens(text):
    """[(start, end)] of every written number in text, a grouped number being one token."""
    t = (text or "").translate(FULLWIDTH)
    return [m.span() for m in _TOKEN.finditer(t)]


def is_whole_number(text, start, end):
    """Whether text[start:end] is exactly one whole number token (not part of a grouped number)."""
    return (start, end) in number_tokens(text)


def english_year(text):
    """{"type": "interval", "precision": "year", "value": "YYYY"} for an English single year, else None."""
    m = _YEAR.fullmatch(" ".join((text or "").split()))
    return {"type": "interval", "precision": "year", "value": m.group(1)} if m else None


def unit_bound(text, value_end, unit_start, unit_end, metric_start, metric_end):
    """Whether the unit span is a word of the metric phrase bound to the value (see the module doc)."""
    if not (metric_start <= unit_start < unit_end <= metric_end):
        return False
    if text[value_end:metric_start].strip(" ") or metric_start < value_end:
        return False  # the metric phrase does not start right after the value
    between = text[value_end:unit_start]
    if _SEPARATOR.search(between) or _NEGATION_OR_COMPARISON.search(between):
        return False
    unit = text[unit_start:unit_end]
    if not re.fullmatch(r"[A-Za-z][A-Za-z-]*", unit):
        return False  # a word, whole: not a fragment or a number
    before = text[unit_start - 1] if unit_start else " "
    after = text[unit_end] if unit_end < len(text) else " "
    return not (before.isalnum() or after.isalnum())


# ---- extraction_normalize.v5: the whole period span ------------------------------------------------
# A period span is the whole written period: a canonical reading of the span says nothing unless no temporal
#   material continues it on either side. "2018" of "January 2018", "2018-2019", "Since 2018" or "2018 to 2019",
#   and "2018年" of "2018年1月" or "2017年至2018年", are parts of another period, never the year itself.

_MONTH = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|"
          r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?")
# words that make an adjacent year part of a larger or other period (a range, a start, an end, a sub-period,
# a non-calendar year)
_TEMPORAL = (r"since|from|until|till|through|thru|between|before|after|by|to|and|or|of|mid|early|late|end|"
             r"beginning|start|fiscal|fy|financial|academic|school|season|seasons|week|weeks|wk|quarter|half|"
             r"month|months|year|years|onward|onwards|first|second|third|fourth|q[1-4]|h[12]")
# a word token may carry digits (Q1, H2); a number token is a day or an ordinal
_EN_PREV = re.compile(
    r"(?:(?P<word>[A-Za-z][A-Za-z0-9]*\.?)|(?P<num>\d+(?:st|nd|rd|th)?)|(?P<mark>[,\-–—/~]))\s*$"
)
_EN_NEXT = re.compile(r"^\s*(?:(?P<word>[A-Za-z][A-Za-z0-9]*)|(?P<mark>[,\-–—/~]))")
_TEMPORAL_RE = re.compile(r"(?:" + _TEMPORAL + r"|" + _MONTH + r")", re.IGNORECASE)
_IN_DURING = re.compile(r"in|during", re.IGNORECASE)
_RANGE_OPENER = re.compile(r"from|between", re.IGNORECASE)
_RANGE_IN = re.compile(r"\b(?:to|and|until|through)\b|[-–—~至到]", re.IGNORECASE)
# CJK continuations: a range, a start or end mark, a sub-period of the year, a list of periods
_CJK_BEFORE = re.compile(r"(?:自|从|由|至|到|截至|截止|和|与|及|、|或|年|月|日|～|－)$")
_CJK_AFTER = re.compile(r"^(?:\d|[０-９]|第|[一二三四]季|[上下]半|全年|至|到|～|－|以来|以前|以后|之前|之后|前|后|"
                        r"底|末|初(?!步)|中期|中旬|起|和|与|及|、|或|年|月|度)")
_ATTACHED = "-–—/~.－'’"


# v5 (found on real NMPA notices): a number that ends a complete written date before the span - the day of
# "发布时间：2026-03-13" on the line above "2026年3月，…" - is that date's own, never a day or an ordinal of the
# span's period. Any other number, a month word or a range mark before it still makes the span part of a larger
# period, on the same line or across a line break ("January\n2018", "2017 -\n2018", "15\n2018" stay not whole).
NORMALIZE_V5 = "extraction_normalize.v5"
_COMPLETE_DATE_END = re.compile(r"(?<![\d/.\-])(?:19|20)\d{2}([-/.])\d{1,2}\1\d{1,2}$")


# Whether text[start:end] is the whole written period: nothing temporal continues it on either side.
def period_span_whole_v5(text, start, end):
    if not (0 <= start < end <= len(text)):
        return False
    before, after = text[max(0, start - 60):start], text[end:end + 60]
    # attached on the left: mid-2018, 1/2018, or a CJK range or sub-period mark
    if before and not before[-1].isspace():
        if before[-1].isascii() and (before[-1].isalnum() or before[-1] in _ATTACHED):
            return False
        if _CJK_BEFORE.search(before):
            return False
    # attached on the right: 2018-19, 2018s, 2018's, 2018/19, or a CJK continuation
    if after and not after[0].isspace():
        if after[0].isascii() and (after[0].isalnum() or after[0] in _ATTACHED) and not (
            after[0] == "." and not after[1:2].isdigit()
        ):
            return False
        if _CJK_AFTER.search(after):
            return False
    # the English word before (past one 'in' / 'during') and after
    b = before
    prev = _EN_PREV.search(b)
    if prev and prev.group("word") and _IN_DURING.fullmatch(prev.group("word")):
        b = b[: prev.start()]
        prev = _EN_PREV.search(b)
    if prev and prev.group("word") and _RANGE_OPENER.fullmatch(prev.group("word")) and _RANGE_IN.search(
        text[start:end]
    ):
        # the opener of a range the span writes out in full ("From" of "From May to June 2018"): no other period
        b = b[: prev.start()]
        prev = _EN_PREV.search(b)
    if prev:
        if prev.group("word") and _TEMPORAL_RE.fullmatch(prev.group("word")):
            return False
        own_date = prev.group("num") and _COMPLETE_DATE_END.search(b[: prev.end()].rstrip())
        if (prev.group("num") and not own_date) or (prev.group("mark") and prev.group("mark") != ","):
            return False
        if prev.group("mark") == ",":  # "January 1, 2018", "1 January, 2018"
            again = _EN_PREV.search(b[: prev.start()])
            if again and (again.group("num") or (again.group("word") and re.fullmatch(_MONTH, again.group("word"),
                                                                                       re.IGNORECASE))):
                return False
    nxt = _EN_NEXT.search(after)
    if nxt:
        if nxt.group("word") and _TEMPORAL_RE.fullmatch(nxt.group("word")):
            return False
        if nxt.group("mark") and nxt.group("mark") != ",":
            return False
        if nxt.group("mark") == "," and re.match(r",\s*(?:(?:19|20)\d{2}\b|" + _MONTH + r"\b)",
                                                 after.lstrip(), re.IGNORECASE):
            return False  # a list of periods: "2018, 2019 and 2020"
    return True


# Counting and measuring nouns that may stand without a regular plural (singular after 1, irregular plurals,
# units of measure). A general vocabulary, not the words of any sample.
UNIT_WORDS = frozenset(
    "case death person people child children man men woman women patient contact dose bed site unit test sample "
    "specimen outbreak cluster event birth admission visit hospitalization hospitalisation infection isolate "
    "animal cattle sheep swine poultry livestock deer fish staff personnel household facility hospital country "
    "district village farm herd flock bird vial course kg g mg mcg ug l ml km m cm mm t tonne ton percent".split()
)


def _counting_noun(word):
    w = word.casefold()
    if w in UNIT_WORDS:
        return True
    return len(w) >= 4 and w.endswith("s") and not re.search(r"(?:ss|us|is|ics)$", w)


# ---- extraction_normalize.v4: the unit is the head of the metric phrase's own subject -----------------------
# The subject of the metric phrase is the words before its first boundary: punctuation (, ; : ( ) [ ] dash), a
# preposition or conjunction, an inclusion / exclusion / example / apposition marker (including, excluding,
# except, such as, e.g., namely ...), or an -ing / -ed word that follows a counting noun ("contacts excluding
# children", "staff infected"). The unit must be the last word of that subject and a counting noun. A subset
# named after the boundary ("children" of "close contacts excluding children") is never the unit; a subject
# whose last word is no counting noun gives no unit (unchecked) - nothing is guessed. "e.g." / "i.e." are one
# boundary mark and a single letter is a word, so "14 hepatitis E cases" keeps its head noun.

NORMALIZE_V4 = "extraction_normalize.v4"
_SUBJECT_STOP_V4 = re.compile(
    r"of|in|among|per|at|from|for|with|without|within|by|to|on|over|during|since|under|aged|who|that|which|"
    r"across|between|than|as|and|or|nor|but|versus|vs|including|excluding|except|besides|plus|such|namely|"
    r"like|especially|particularly|notably|mainly|mostly|primarily|comprising|consisting|involving|incl|excl|"
    r"eg|ie|viz|respectively|alone|only|each|both|all|whom|whose|where|when",
    re.IGNORECASE,
)
_SUBJECT_PUNCT_V4 = re.compile(r"[,;:()\[\]{}—–/]|\s-\s|\b(?:e\.g|i\.e)\.|\.(?=\s|$)", re.IGNORECASE)


def unit_subject_head_v4(text, value_end, unit_start, unit_end, metric_start, metric_end):
    """v4: unit_bound (v1); the unit is the last word of the metric phrase's subject (see above) and a counting
    noun (a regular plural or a word of UNIT_WORDS)."""
    if not unit_bound(text, value_end, unit_start, unit_end, metric_start, metric_end):
        return False
    phrase = text[metric_start:metric_end]
    cut = _SUBJECT_PUNCT_V4.search(phrase)
    subject_end = cut.start() if cut else len(phrase)
    head = None
    counted = False
    for i, w in enumerate(re.finditer(r"[A-Za-z][A-Za-z-]*", phrase[:subject_end])):
        word = w.group()
        if i and _SUBJECT_STOP_V4.fullmatch(word):
            break
        if counted and re.search(r"(?:ing|ed)$", word, re.IGNORECASE):
            break
        head = w
        counted = counted or _counting_noun(word)
    if head is None or (metric_start + head.start(), metric_start + head.end()) != (unit_start, unit_end):
        return False
    return _counting_noun(head.group())


# ---- extraction_normalize.v6: spelled counts (checker v17) ----------------------------------------------
# The functions above are unchanged. v6 reads a count written as an English word of one to twelve ("nine cases",
# "Seven of the reported cases") the way v1 reads digits: the word is the number, whole - never a part of a longer word
# ("none", "someone", "one-third", "twenty-one"). Only these small counts: larger spelled numbers, ordinals and
# fractions are not numbers here, and number_tokens (which finds the quantities of a sentence) is not extended.

NORMALIZE_V6 = "extraction_normalize.v6"
SPELLED_NUMBERS = {word: n for n, word in enumerate(
    ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"), 1)}
_WORD_EDGE = "-–—'’"  # a hyphen or an apostrophe keeps the word part of a longer one: "one-third", "one's"


def decimal_text_v6(written):
    """decimal_text (v1), and a spelled number of one to twelve (any case) as its decimal text."""
    spelled = SPELLED_NUMBERS.get((written or "").strip().casefold())
    return str(spelled) if spelled else decimal_text(written)


def is_whole_number_v6(text, start, end):
    """is_whole_number (v1), or text[start:end] is a spelled number of one to twelve standing as a whole word."""
    if is_whole_number(text, start, end):
        return True
    if text[start:end].casefold() not in SPELLED_NUMBERS:
        return False
    around = (text[start - 1] if start > 0 else " ", text[end] if end < len(text) else " ")
    return not any(c.isalnum() or c in _WORD_EDGE for c in around)
