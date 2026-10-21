"""Deterministic checks on a finished answer against the excerpts it was written from.

No LLM call: page and number checks are regex + Decimal comparisons, a few ms per
answer. They started as grounding-eval metrics (scripts/eval_grounding.py imports
them from here), where an LLM judge rubber-stamped citations that code caught.
"""
import re
from decimal import ROUND_HALF_UP, Decimal

# "page 47", "pages 50–51 and 60", "pages 1, 5, and 41", "p. 9", "pp. 3-4"
# ponytail: "(page 47, 2022)" also collects 2022; tighten if fake_page_rate flags it
PAGE_RE = re.compile(r"\bp(?:ages?|p?\.)\s*(\d+(?:\s*(?:,\s*(?:and|&)?|–|-|and|&)\s*\d+)*)", re.IGNORECASE)
_LIST_MARK_RE = re.compile(r"^\s*\d+[.)]\s", re.MULTILINE)  # "2. Expanding data center…"

# "25,000", "$23.6 billion", "16%", "$5B"; not the 300 in "MI300" or the 1 in "Q1"
_NUM_RE = re.compile(r"(?<![\w.])(\d+(?:,\d{3})*(?:\.\d+)?)(?!\d)"
                     r"(?:\s*(%|percent\b|thousand\b|million\b|billion\b)|([BM])\b)?", re.IGNORECASE)
_SCALE = {"thousand": 3, "million": 6, "m": 6, "billion": 9, "b": 9}

# A refusal says so in its first sentence ("The provided excerpts do not contain ...");
# answers that only miss a detail say it later. "The document states AMD does not expect
# ..." is an answer, hence the excerpt/document subject and the verb list. On 788 judged
# AMD answers: all 212 judge-flagged abstentions match, 3 of 576 answers do (hedges like
# "do not explicitly state ... However, ...").
_ABSTAIN_RE = re.compile(
    r"\b(?:excerpts?|documents?|sources?|text|filings?|reports?)\b[^.]{0,80}?\b(?:do|does)\s+not\s+(?:\w+ly\s+)?"
    r"(?:contain|specify|state|mention|provide|include|say|give|list|disclose|discuss|address|cover|indicate|define)\b"
    r"|^no relevant context", re.IGNORECASE)
_FIRST_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n")


def cited_pages(answer: str) -> list[int]:
    return sorted({int(n) for m in PAGE_RE.findall(answer) for n in re.findall(r"\d+", m)})


def fake_pages(cited: list[int], ex: list[dict]) -> list[int]:
    """Cited pages that fall outside every excerpt's page span: the model never saw them."""
    spans = [(e["page_start"], e["page_end"] or e["page_start"]) for e in ex if e["page_start"] is not None]
    return [p for p in cited if not any(a <= p <= b for a, b in spans)]


def numbers(text: str) -> list[tuple[Decimal, Decimal, bool, str]]:
    """(value, precision, is_percent, as written) per number; page citations and list markers
    are not numbers here."""
    out = []
    for m in _NUM_RE.finditer(_LIST_MARK_RE.sub(" ", PAGE_RE.sub(" ", text))):
        unit = (m[2] or m[3] or "").lower()
        d, k = Decimal(m[1].replace(",", "")), _SCALE.get(unit, 0)
        out.append((d.scaleb(k), Decimal(1).scaleb(d.as_tuple().exponent + k), unit in ("%", "percent"), m[0]))
    return out


def has_number(want: tuple, text: str) -> bool:
    """`text` states `want`, possibly more precisely: $23,601 million counts for $23.6 billion.
    In text saying "in millions" (10-K tables), a bare 23,601 also counts as 23,601 million."""
    v, q, pct = want[:3]
    have = [(h, p) for h, _, p, _ in numbers(text)]
    if re.search(r"in millions", text, re.IGNORECASE):  # ponytail: per text, not per table
        have += [(h.scaleb(6), p) for h, p in have if not p]
    return any(p == pct and (h / q).to_integral_value(ROUND_HALF_UP) * q == v for h, p in have)


def unsourced_numbers(answer: str, ex: list[dict]) -> list[str]:
    """Numbers the answer states that no excerpt states (to the answer's precision)."""
    src = "\n".join(e["text"] for e in ex)
    return [n[3].strip() for n in numbers(answer) if not has_number(n, src)]


def abstained(answer: str) -> bool:
    """The answer's main message is that the excerpts don't cover the question."""
    return bool(_ABSTAIN_RE.search(_FIRST_SENTENCE_END.split(answer.strip(), maxsplit=1)[0]))


def check(answer: str, ex: list[dict]) -> dict:
    """{"status": "ok" | "warning" | "insufficient_evidence", "issues": [...], "needs_review": bool}.

    A refusal skips the number/page checks: the figures it mentions in passing are not claims."""
    if abstained(answer):
        return {"status": "insufficient_evidence", "issues": [], "needs_review": True}
    issues = []
    if fake := fake_pages(cited_pages(answer), ex):
        issues.append({"kind": "fake_page", "detail": f"cites page(s) {', '.join(map(str, fake))}, "
                                                      "outside every excerpt it was given"})
    if nums := unsourced_numbers(answer, ex):
        issues.append({"kind": "unsourced_number", "detail": f"states {', '.join(nums)}, which no excerpt states"})
    return {"status": "warning" if issues else "ok", "issues": issues, "needs_review": bool(issues)}
