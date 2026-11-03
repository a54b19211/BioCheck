"""Deterministic sentence work: split, cite, locate quotes, check source use. No model calls."""
import re
from decimal import Decimal, InvalidOperation

LABELS = ("OBJ", "NUM", "TIME", "COND", "STR", "REF")
ID = r"[A-Za-z][A-Za-z_]*\d[A-Za-z0-9_.:-]*"  # a ref has a digit: [E1], [A2]; [A] or [scope] is prose
CITE = re.compile(r"\[(" + ID + r"(?:\s*[,，;；]\s*" + ID + r")*)\]")
TRAILING = re.compile(r"[ \t]*(?:" + CITE.pattern + r"[ \t]*)+")
SENTENCE_END = re.compile(r"[。！？]+[”’」』]?|[.!?]+(?=\s|\[|$)|\n+|\Z")
NUMBER = re.compile(r"(?<![A-Za-z0-9_.])(?<![A-Za-z]-)[-−]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+)%?")
MAX_BATCH = 4

# Wording a sentence needs when it cites an unverified or social-media source.
ATTRIBUTION = re.compile(r"网传|网帖|据称|传言|帖|贴文|声称|自称|称|所述|转发|转述|转载|社媒|微博|账号|"
                         r"报道|通报|声明|来源|"
                         r"reported|post|alleged|claim|rumou?r|according to", re.I)
UNCERTAIN = re.compile(r"未证实|未经证实|未获证实|未核实|未经核实|无法核实|难以核实|无法核验|无可核|不可核|"
                       r"未能证实|难以证实|待核|待验证|尚待|"
                       r"尚未确认|未经确认|无法确认|不能确认|无法证实|不能证实|真实性不明|存疑|疑似|假设|"
                       r"置信低|可信度低|"
                       r"unconfirmed|unverified|alleged|hypothes", re.I)
HYPOTHESIS = re.compile(r"假设|待验证|hypothes", re.I)
EVENT = re.compile(r"删帖|删除|辟谣|处罚|澄清|delet|denial|penalt|censor", re.I)
SOURCE_NUMBER = re.compile(r"[-−]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+)%?")  # sources glue numbers: CI0.4%
TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:?\d{2}|Z)?")


def citations(text):
    found = [part.strip() for m in CITE.finditer(text) for part in re.split(r"[,，;；]", m.group(1))]
    return list(dict.fromkeys(found))


def paragraphs(draft):
    """(start, end) of each paragraph; paragraphs are separated by blank lines."""
    spans = []
    for m in re.finditer(r".+?(?=\n[ \t]*\n|\Z)", draft, re.S):
        text = m.group()
        start, end = m.start() + len(text) - len(text.lstrip()), m.end() - (len(text) - len(text.rstrip()))
        if start < end:
            spans.append((start, end))
    return spans


def sentences(draft):
    """Numbered sentences with draft offsets. Citations right after the full stop belong to it."""
    result = []
    for number, (p_start, p_end) in enumerate(paragraphs(draft), 1):
        text, cursor = draft[p_start:p_end], 0
        for m in SENTENCE_END.finditer(text):
            end = m.end()
            if end <= cursor:
                continue
            tail = TRAILING.match(text, end)
            if tail:
                end = tail.end()
            piece, start = text[cursor:end], cursor
            cursor = end
            start += len(piece) - len(piece.lstrip())
            stop = end - (len(piece) - len(piece.rstrip()))
            if not CITE.sub("", text[start:stop]).strip(" \t\n。！？.!?"):
                continue
            result.append({"index": len(result) + 1, "paragraph": number, "text": text[start:stop],
                           "start": p_start + start, "end": p_start + stop,
                           "citations": citations(text[start:stop])})
    return result


def batches(claims, size=MAX_BATCH):
    """At most `size` claims per batch, never mixing paragraphs."""
    groups = []
    for claim in claims:
        if groups and len(groups[-1]) < size and groups[-1][0]["paragraph"] == claim["paragraph"]:
            groups[-1].append(claim)
        else:
            groups.append([claim])
    return groups


def numeric_tokens(text):
    text = re.sub(r"\b(?:NCT\d+|PMID\s*:?\s*\d+)\b", "", TIMESTAMP.sub("", CITE.sub("", text)))
    text = re.sub(r"[（(]\s*\d{1,2}\s*[)）]", "", text)  # list markers such as （1） are not data
    return list(dict.fromkeys(m.group() for m in NUMBER.finditer(text)))


def _value(token):
    try:
        return Decimal(token.replace(",", "").replace("−", "-").rstrip("%"))
    except InvalidOperation:
        return None


def numeric_hints(sentence, cited_texts):
    """Numbers in the sentence that do not literally occur in the cited texts."""
    available = {_value(m.group()) for text in cited_texts for m in SOURCE_NUMBER.finditer(text)}
    return [t for t in numeric_tokens(sentence) if _value(t) not in available]


def locate_quote(text, quote):
    """'found' (exactly once), 'ambiguous' (several times) or 'not_found'. Whitespace runs count as one."""
    text, quote = " ".join(text.split()), " ".join((quote or "").split())
    first = text.find(quote) if quote else -1
    if first < 0:
        return "not_found"
    return "ambiguous" if text.find(quote, first + 1) >= 0 else "found"


def source_use_problems(text, cited, evidence, updates):
    """Rules on corrected/retracted and unverified sources. Returns (reasons, read_requests)."""
    body, reasons, reads = CITE.sub("", text), [], []
    for update in updates:
        target, replacement = update["target"], update["replacement"]
        if target not in cited:
            continue
        kind = "撤回" if update["kind"] == "retraction" else "更正"
        # Without the notice nobody can tell which part changed. With it, the model judges whether the
        # sentence uses a changed part as current (it sees source_updates and both texts); unchanged
        # content of the old source may be cited alone.
        if replacement not in evidence:
            reasons.append(f"source_updated: {target}已被{replacement}{kind}，未读{kind}通知前旧内容不能当作当前事实")
            reads.append({"ref": replacement, "reason": f"读取{target}的{kind}通知{replacement}，再核对旧数的适用范围"})
    unverified = [r for r in cited if r in evidence
                  and (evidence[r]["source_kind"] == "social" or not evidence[r]["verified"])]
    if unverified:
        attributed = UNCERTAIN.search(body) and (ATTRIBUTION.search(body) or HYPOTHESIS.search(body))
        if not (attributed or EVENT.search(body)):
            reasons.append(f"unconfirmed_source: {'、'.join(unverified)}未核实，本句须保留转述者和未证实限定，"
                           "或写成待验证的假设，不能写成已确认事实")
    return reasons, reads


def scope_span_ok(text, span):
    """The span the model says turns 'not reported' into 'not done' must be exact, unique and local."""
    return (isinstance(span, str) and span.strip() == span and span and text.count(span) == 1
            and not CITE.search(span) and not re.search(r"[。！？!?\n]", span)
            and re.search(r"未|没|无|不|非|not|no\b|never", span, re.I) is not None)


def scope_revision(text, span):
    """未报告≠未实施: wrap only the over-reaching assertion, keep every other character."""
    start = text.index(span)
    return text[:start] + "本次引用材料不足以确认“" + span + "”这一说法" + text[start + len(span):]
