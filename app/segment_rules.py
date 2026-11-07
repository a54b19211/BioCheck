"""Candidate segments, structure markers and time clues of a sentence. Rules only, no model call.

A candidate segment is bookkeeping: the program cuts a sentence at commas, semicolons, enumeration commas and
connector words so that it can check that every piece was handled. A segment is never judged on its own, because
the scope of a negation or a condition can reach into the next segment (unit_rules.py, governed_by). The word
lists below are split points and hints, never proof of meaning (after ConText: triggers, and pseudo-triggers
that look like one and are not). RULES_VERSION changes whenever one of them does.
"""
import re
from dataclasses import dataclass
from datetime import date
from typing import Literal

from pydantic import BaseModel

from .claims import CITE, citations

RULES_VERSION = "biocheck.segment_rules.v1"

# ---- candidate segments

DELIMITERS = "，；、,;"
BRACKETS = {"（": "）", "(": ")", "【": "】", "[": "]", "「": "」", "『": "』", "“": "”", "《": "》"}
THOUSANDS = re.compile(r"\d,\d{3}(?!\d)")  # the comma of 3,462 is not a delimiter

# A one-character connector joins clauses only where it is a word of its own. These words contain 并/且/而/但
# without joining anything, so they are skipped whole; the lookaheads keep "并发布" or "并重新" (and + verb) apart
# from 并发症 and 并重.
NOT_JOINING = re.compile(
    r"合并|兼并|吞并|归并|并发(?![现布出展生表送给起动射放挥])|并存(?!在)|并列(?!出)|并用(?!于)|并购|并入(?!院)|并行(?!动)|"
    r"并网|并肩|并轨|并重(?!新)|并举|并[未不无没非]|并且|而且|暂且|姑且|况且|尚且|苟且|从而|因而|反而|进而|继而|转而|"
    r"而今|而后|而已|不但|但愿|但凡")
ZH_CONNECTOR = re.compile(r"以及|但是|然而|不过|虽然|尽管|即使|即便|[并且而但虽]")
EN_CONNECTOR = re.compile(r"(?<![A-Za-z0-9_-])(?:and|but|although|though|while|whereas|however)(?![A-Za-z0-9_/-])",
                          re.IGNORECASE)


@dataclass(frozen=True)
class Marker:
    kind: Literal["negation", "condition"]
    text: str
    start: int  # offset in the sentence


@dataclass(frozen=True)
class Segment:
    id: str  # S1, S2, ... in sentence order
    start: int  # offsets in the sentence text; text == sentence[start:end]
    end: int
    text: str
    markers: tuple[Marker, ...]


def _protected(text):
    """Offsets nothing is cut at: inside a bracket pair (a citation is one) and the comma of a thousands group."""
    inside, stack = set(), []
    for i, char in enumerate(text):
        if char in BRACKETS:
            stack.append((BRACKETS[char], i))
        elif stack and char == stack[-1][0]:
            inside.update(range(stack.pop()[1], i + 1))
    inside.update(m.start() + 1 for m in THOUSANDS.finditer(text))
    return inside


def _cuts(text):
    """[(offset, width)]: where a segment ends and the next one starts; width is the dropped delimiter (a
    connector word is not dropped, it opens the next segment)."""
    inside, cuts, i = _protected(text), [], 0
    while i < len(text):
        if i in inside:
            i += 1
        elif text[i] in DELIMITERS:
            cuts.append((i, 1))
            i += 1
        elif skip := NOT_JOINING.match(text, i):
            i = skip.end()
        elif word := ZH_CONNECTOR.match(text, i):
            cuts.append((i, 0))
            i = word.end()
        else:
            i += 1
    cuts += [(m.start(), 0) for m in EN_CONNECTOR.finditer(text) if m.start() not in inside]
    return sorted(cuts)


def _has_content(piece):
    return any(char.isalnum() for char in CITE.sub("", piece))


def split_segments(sentence):
    """The candidate segments of one sentence, numbered S1.. in order, with the markers each carries. Pieces
    without a letter or digit (a lone citation, a stray punctuation mark) are not segments."""
    bounds, begin = [], 0
    for at, width in _cuts(sentence):
        bounds.append((begin, at))
        begin = at + width
    bounds.append((begin, len(sentence)))
    segments = []
    for start, end in bounds:
        piece = sentence[start:end]
        start, end = start + len(piece) - len(piece.lstrip()), end - (len(piece) - len(piece.rstrip()))
        if _has_content(sentence[start:end]):
            segments.append(Segment(f"S{len(segments) + 1}", start, end, sentence[start:end],
                                    tuple(find_markers(sentence[start:end], start))))
    return segments


# ---- negation and condition markers

NEGATION = re.compile(r"[未没无不非]|n['’]t\b"
                      r"|(?<![A-Za-z0-9_-])(?:no|not|never|none|neither|nor|without|cannot)(?![A-Za-z0-9_-])",
                      re.IGNORECASE)
# look like a negation and are not one (ConText pseudo-triggers): 不过 is "but", 非洲 is Africa, 不明 modifies a noun
NEGATION_PSEUDO = re.compile(
    r"不仅|不但|不只|不光|不限于|不断|不同|不少|不良|不得不|不明|不论|不管|不过|非洲|非常|非典|未来|无论|无疑|无数|"
    r"无限|无锡|没收|not only|not limited to|cannot rule out|can not rule out|not rule out|no doubt|whether or not|"
    r"(?-i:\bNo\.?\s*\d)", re.IGNORECASE)
CONDITION = re.compile(r"如果|假如|假设|假使|倘若|倘使|若|一旦|除非|只要|万一|要是|否则"
                       r"|(?<![A-Za-z0-9_-])(?:if|unless|provided that|in case|assuming)(?![A-Za-z0-9_-])",
                       re.IGNORECASE)
CONDITION_PSEUDO = re.compile(r"若干")
CONSEQUENT = re.compile(r"\s*(?:则|那么)")  # opens the clause that follows an if-clause


def _blank(pattern, text):
    return pattern.sub(lambda m: " " * len(m.group()), text)


def find_markers(text, offset=0):
    """The negation and condition triggers in text, in order, at `offset` + position. Hints only: that a word is
    there says nothing about what it negates or conditions."""
    masked = _blank(CONDITION_PSEUDO, _blank(NEGATION_PSEUDO, _blank(CITE, text)))
    found = [Marker("negation", m.group(), offset + m.start()) for m in NEGATION.finditer(masked)]
    found += [Marker("condition", m.group(), offset + m.start()) for m in CONDITION.finditer(masked)]
    lead = CONSEQUENT.match(masked)
    if lead:
        found.append(Marker("condition", lead.group().strip(), offset + lead.end() - len(lead.group().strip())))
    return sorted(found, key=lambda marker: marker.start)


# ---- the condition a consequent answers

def condition_context(segments):
    """{segment id: ids of the segments that hold the condition it answers} for each segment that opens with 则/那么
    (in "如果A，或B，则C", S3 answers S1 and S2). A hint for the semantic check, like the markers: how far a
    condition reaches is the model's reading of the sentence."""
    context, opened = {}, None
    for at, segment in enumerate(segments):
        if CONSEQUENT.match(segment.text):
            if opened is not None:
                context[segment.id] = [s.id for s in segments[opened:at]]
            opened = None
        elif opened is None and any(m.kind == "condition" for m in segment.markers):
            opened = at
    return context


# ---- time clues

MONTHS = {name: number for number, names in enumerate(
    ("january jan", "february feb", "march mar", "april apr", "may", "june jun", "july jul", "august aug",
     "september sep sept", "october oct", "november nov", "december dec"), 1) for name in names.split()}
DATE_FORM = re.compile(
    r"(?<!\d)(?P<y1>\d{4})[-/](?P<m1>\d{1,2})[-/](?P<d1>\d{1,2})(?!\d)"
    r"|(?P<y2>\d{4})年(?P<m2>\d{1,2})月(?P<d2>\d{1,2})日"
    r"|(?<!\d)(?P<d3>\d{1,2})(?:st|nd|rd|th)?\s+(?P<mn3>[A-Za-z]{3,9})\.?,?\s+(?P<y3>\d{4})(?!\d)"
    r"|(?P<mn4>[A-Za-z]{3,9})\.?\s+(?P<d4>\d{1,2})(?:st|nd|rd|th)?,?\s+(?P<y4>\d{4})(?!\d)")
VERSION_DATE_AFTER = re.compile(r"\s*(?:的\s*)?(?:发布|公布|发表|刊发|出版|通报|公告)")
VERSION_DATE_BEFORE = re.compile(r"(?:published|released|issued|dated)\s+(?:on\s+)?$", re.IGNORECASE)
# wording that says the text is a later version than the date it is named by
CURRENT_VERSION = re.compile(
    r"现存版本|现有版本|当前版本|现行版本|最新版本|更新后|更新版|修订版|后续版本|本系统于|采集于|访问于|读取于|检索于|"
    r"current version|latest version|updated version|revised version|retrieved on|accessed on|as retrieved|as accessed",
    re.IGNORECASE)
MAX_LATER_DATES = 5


class VisibleSource(BaseModel):
    """One cited source as the time check sees it: its text and the time facts that came with it."""

    ref: str
    text: str
    published_at: str | None = None
    first_seen_at: str | None = None
    proof_available_at: str | None = None
    replay_mode: Literal["current", "operational_replay", "public_history_reconstruction"] | None = None


def _date_of(match):
    g = match.groupdict()
    if g["y1"]:
        parts = g["y1"], g["m1"], g["d1"]
    elif g["y2"]:
        parts = g["y2"], g["m2"], g["d2"]
    else:
        parts = g["y3"] or g["y4"], MONTHS.get((g["mn3"] or g["mn4"]).lower()), g["d3"] or g["d4"]
    try:
        return date(*map(int, parts))
    except (TypeError, ValueError):
        return None


def dates_in(text):
    """[(date, start, end)] for each full date (year, month, day) written in text. A date without a year is not one."""
    found = [(_date_of(m), m.start(), m.end()) for m in DATE_FORM.finditer(text)]
    return [item for item in found if item[0] is not None]


def stated_version_dates(sentence):
    """The dates the sentence gives as the date of the document it relies on ("根据2020年5月7日发布的通报")."""
    body = CITE.sub("", sentence)
    return [when for when, start, end in dates_in(body)
            if VERSION_DATE_AFTER.match(body, end) or VERSION_DATE_BEFORE.search(body[max(0, start - 24):start])]


def time_clues(sentence, sources):
    """Clues for the semantic check, never a verdict: a cited text that holds dates later than the date the
    sentence names its document by, unless the sentence says it is a later version. A later date alone proves
    nothing (a plan, a review of several periods and an accurate citation of the version on file are all fine);
    the time facts of the source go along so the reader can tell which it is."""
    if CURRENT_VERSION.search(CITE.sub("", sentence)):
        return []
    cited, clues = set(citations(sentence)), []
    for stated in stated_version_dates(sentence):
        for source in (s for s in sources if s.ref in cited):
            later = sorted({when for when, _, _ in dates_in(source.text) if when > stated})
            if later:
                clues.append({"code": "later_than_stated_date", "ref": source.ref, "stated": stated.isoformat(),
                              "later_dates": [d.isoformat() for d in later[:MAX_LATER_DATES]],
                              **source.model_dump(include={"published_at", "first_seen_at", "proof_available_at",
                                                           "replay_mode"})})
    return clues
