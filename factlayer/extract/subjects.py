"""Work out what entity a document is mostly about.

Used for display and as a soft signal when comparing facts; the hard scope for
comparison is the collection a document is ingested into.  Auto-detection is a
convenience, and every ingest path allows an explicit override.
"""

from __future__ import annotations

import re
from collections import Counter

_CAPITALISED = re.compile(
    r"\b([A-Z][A-Za-z&.]+(?:\s+(?:of|and|the)\s+|\s+)?(?:[A-Z][A-Za-z&.]+)?"
    r"(?:\s+(?:of|and|the)\s+|\s+)?(?:[A-Z][A-Za-z&.]+)?)\b")
_LEGAL_SUFFIX = re.compile(
    r"\b(limited|ltd|pvt|private|inc|incorporated|corp|corporation|plc|llp|llc|company|co)\b\.?",
    re.IGNORECASE)
_NOISE = {
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december", "jan", "feb", "mar", "apr",
    "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
    "annual", "report", "survey", "prospectus", "chapter", "table", "figure",
    "chart", "box", "annexure", "annex", "appendix", "section", "note", "notes",
    "page", "source", "total", "net", "gross", "other", "others", "the", "this",
    "our", "their", "its", "all", "per", "cent", "usd", "inr", "gdp", "ebitda",
    "yoy", "qoq", "fy", "q1", "q2", "q3", "q4", "statements", "consolidated",
    "standalone", "financial", "year", "board", "directors", "committee", "group",
    "offer", "equity", "shares", "non", "particulars", "company", "companies",
    "act", "regulation", "regulations", "rules", "members", "management",
    "business", "statement", "risk", "risks", "million", "crore", "lakh",
    "billion", "percent", "chart", "index", "contents", "overview", "summary",
    "fiscal", "revenue", "earnings", "presentation", "quarter", "half", "results",
}
# Section markers ("II.", "IV") are not entities.
_ROMAN = re.compile(r"^[IVXLCivxlc]+\.?$")
# A name carrying a legal suffix is almost certainly the entity a filing is
# about, which frequency alone cannot tell you -- "India" appears constantly in
# an Indian company's annual report without being its subject.
_LEGAL_ENTITY = re.compile(
    r"\b([A-Z][A-Za-z&.\-]*(?:\s+[A-Z][A-Za-z&.\-]*){0,3})\s+"
    r"(?:Limited|Ltd\.?|Inc\.?|Incorporated|Corporation|Corp\.?|PLC|LLP|LLC)\b")
_LEGAL_WEIGHT = 6
_COVER_PAGES = 3
_MIN_LENGTH = 3


def _candidates(text: str) -> Counter:
    counts: Counter = Counter()
    for m in _CAPITALISED.finditer(text):
        phrase = " ".join(m.group(1).split())
        words = phrase.split()
        while words and words[-1].lower() in {"of", "and", "the"}:
            words.pop()
        if not words:
            continue
        if all(w.lower() in _NOISE for w in words):
            continue
        if len(words) == 1 and (words[0].lower() in _NOISE or len(words[0]) < _MIN_LENGTH):
            continue
        if any(_ROMAN.match(w) for w in words):
            continue
        # A shouted banner line ("SYNTHETIC TEST FIXTURE") is not an entity,
        # while a short acronym ("IMF", "RBI") may well be.
        if all(w.isupper() for w in words) and any(len(w) >= 5 for w in words):
            continue
        if not normalise_subject(" ".join(words)):
            continue
        counts[" ".join(words)] += 1
    return counts


def normalise_subject(name: str) -> str:
    """Key used for equality: case-folded, legal suffixes and punctuation dropped."""
    cleaned = _LEGAL_SUFFIX.sub("", name)
    cleaned = re.sub(r"[^\w\s]", " ", cleaned)
    return " ".join(cleaned.lower().split())


def detect_subject(title: str, page_texts: list[str]) -> str | None:
    """Best guess at the document's principal entity.

    Weighted towards the cover pages and the PDF title, because a report's
    subject is announced up front and then referred to by pronoun.
    """
    scores: Counter = Counter()
    for phrase, n in _candidates(title or "").items():
        scores[phrase] += 8 * n
    for text in page_texts:
        for m in _LEGAL_ENTITY.finditer(text):
            name = " ".join(m.group(1).split()[-3:])
            if name and not all(w.lower() in _NOISE for w in name.split()):
                scores[name] += _LEGAL_WEIGHT
    # Front matter names the issuer; the body then says "the Company".  This is
    # only a hint: detection is unreliable on excerpted documents that have no
    # cover page, so comparability never depends on it (see ``collection``).
    for text in page_texts[:_COVER_PAGES]:
        for phrase, n in _candidates(text).items():
            scores[phrase] += 3 * n
    for text in page_texts[_COVER_PAGES:]:
        for phrase, n in _candidates(text).items():
            scores[phrase] += n
    if not scores:
        return None
    # Prefer the most-mentioned; break ties towards the more specific phrase.
    best = max(scores.items(), key=lambda kv: (kv[1], len(kv[0].split())))
    return best[0]
