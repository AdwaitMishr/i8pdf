"""Recover the metric a number refers to, from the words around it.

This is deliberately syntactic rather than dictionary-driven: the assignment's
documents are meant to define what counts as a fact, so there is no list of
"metrics we know about".  The rule is that a measurement reads

    <metric phrase> <linking verb> <qualifiers/period> <number>

so the phrase is recovered by walking left from the number, stepping over the
spans other parsers already claimed (period, qualifier, an earlier quantity) and
over linking verbs, then collecting the noun phrase that governs them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Linking verbs, prepositions and adverbs that sit between a metric and its value.
BRIDGE = {
    "stood", "stands", "standing", "stand", "was", "were", "is", "are", "be", "been",
    "being", "has", "have", "had", "at", "to", "by", "of", "on", "in", "into", "for",
    "during", "with", "from", "up", "down", "about", "around", "over", "under",
    "approximately", "nearly", "roughly", "than", "as", "against", "versus", "vs",
    "reached", "reaching", "totalled", "totaled", "totalling", "totaling", "amounted",
    "amounting", "registering", "registered", "recording", "recorded", "posting",
    "posted", "reporting", "came", "remained", "remaining", "stayed", "hit",
    "touched", "clocked", "delivered", "achieved", "generated", "printed", "marked",
    "estimated", "projected", "expected", "forecast", "seen", "placed", "put",
    "exceeded", "crossed", "surpassed", "implied", "implying", "including",
    "compared", "comparing", "relative", "versus", "vis-a-vis", "alongside",
    "respectively", "only", "just", "also", "further", "some", "well", "still",
    "mostly", "largely", "broadly", "roughly", "generally", "relatively",
    "marginally", "slightly", "significantly", "substantially", "sharply",
    "modestly", "steadily", "gradually", "meanwhile", "overall", "modest",
    "strong", "robust", "healthy", "sharp", "steep", "notable", "moderate",
    "stable", "ample", "sizeable", "considerable", "muted", "subdued",
    "elevated", "benign", "buoyant", "resilient", "comfortable", "adequate",
    "now", "then", "there", "here", "it", "its",
}
# Verbs and nouns that turn a level into a rate of change.
GROWTH_WORDS = {
    "grew", "grow", "grows", "growing", "growth", "rose", "rise", "rises", "rising",
    "increased", "increase", "increases", "increasing", "declined", "decline",
    "declines", "declining", "fell", "fall", "falls", "falling", "dropped", "drop",
    "expanded", "expand", "expanding", "contracted", "contract", "contracting",
    "moderated", "moderate", "moderating", "accelerated", "decelerated", "improved",
    "eased", "easing", "reduction", "surged", "jumped", "slowed",
}
# Nouns that describe a derived measure and are meaningless without their subject.
DERIVATIVE = {
    "growth", "increase", "decrease", "decline", "rise", "fall", "drop", "reduction",
    "change", "margin", "share", "ratio", "rate", "contribution", "level", "value",
}
# Words too generic to identify a measure on their own.  A phrase made only of
# these names nothing: "net growth", "total change".
GENERIC_TOKENS = {
    "total", "net", "gross", "value", "amount", "number", "share", "rate",
    "growth", "income", "year", "change", "level", "index", "figure", "overall",
}
# Words that may sit inside a noun phrase but never start or end one.
INTERNAL = {"of", "from", "in", "on", "for", "to", "and", "&", "per", "by", "the", "a"}
# Reaching one of these means the noun phrase has ended.
STOPPERS = {
    "the", "a", "an", "our", "its", "their", "his", "her", "this", "that", "these",
    "those", "which", "who", "while", "whereas", "although", "though", "because",
    "if", "when", "after", "before", "however", "thereby", "we", "they", "company",
    "such", "namely", "etc", "e.g", "i.e", "viz", "including",
    # Unit words belong to the quantity, never inside the metric phrase.
    "cent", "percent", "percentage", "crore", "lakh", "million", "billion",
}
_PUNCT = set(",;:()[]{}—–/\"")
_TOKEN = re.compile(r"[A-Za-z][A-Za-z.'’&-]*|[^\sA-Za-z]")
_MAX_TOKENS = 7
# "India's real GDP" names its owner; that owner is the subject, not the metric.
_POSSESSIVE = re.compile(r"^(.*?)['’]s$")


@dataclass(frozen=True)
class MetricPhrase:
    text: str
    is_rate: bool                  # the number measures a change, not a level
    span: tuple[int, int] | None
    subject_hint: str | None = None   # possessive owner, e.g. "India's real GDP"


def _tokens(text: str) -> list[tuple[str, int, int]]:
    return [(m.group(), m.start(), m.end()) for m in _TOKEN.finditer(text)]


def _masked(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    return any(start < e and s < end for s, e in spans)


def _clean(words: list[str]) -> str:
    while words and words[0].lower() in INTERNAL:
        words.pop(0)
    while words and words[-1].lower() in INTERNAL:
        words.pop()
    return " ".join(words)


def phrase_for(text: str, value_start: int, claimed: list[tuple[int, int]],
               topic: str | None = None) -> MetricPhrase:
    """Metric phrase governing the value that starts at ``value_start``.

    ``claimed`` are spans already consumed by the period, qualifier and quantity
    parsers; they are stepped over rather than read.  ``topic`` is the metric of
    the first value in the same sentence, used to give a bare "growth of 11.9%"
    back the subject it is a growth *of*.
    """
    toks = [t for t in _tokens(text) if t[2] <= value_start]
    words: list[str] = []
    is_rate = False
    collecting = False
    first, last = None, None
    owner: str | None = None
    right_token = ""      # the token just to the right of the one being read

    for token, start, end in reversed(toks):
        low = token.lower()
        if _masked(start, end, claimed):
            continue
        if token in _PUNCT:
            if collecting:
                break
            right_token = low
            continue
        owner_match = _POSSESSIVE.match(token)
        if owner_match and owner_match.group(1):
            if collecting:
                owner = owner_match.group(1)
                break
            continue
        if low in GROWTH_WORDS:
            # "grew by 6.5 per cent" is a rate; "declined to 0.6 per cent of GDP"
            # is a level the measure fell to.  The preposition decides.
            is_rate = right_token != "to"
            if low in DERIVATIVE and not collecting:
                collecting = True
                words.append(token)
                first, last = start, end if last is None else last
                last = last if last is not None else end
                continue
            if collecting:
                break
            right_token = low
            continue
        if not collecting:
            if low in BRIDGE or low in STOPPERS:
                right_token = low
                continue
            collecting = True
            words.append(token)
            first, last = start, end
            right_token = low
            continue
        # "a modest growth of 1.6 per cent" leaves us holding only "growth",
        # which names nothing.  While that is all we have, keep stepping over
        # adjectives and verbs to reach the noun the growth is *of*.
        if low in STOPPERS or (low in BRIDGE and low not in INTERNAL):
            if all(w.lower() in DERIVATIVE for w in words):
                right_token = low
                continue
            break
        words.append(token)
        first = start
        # Every word counts towards the cap, including connectives, so a runaway
        # clause cannot masquerade as a metric name.
        if len(words) >= _MAX_TOKENS:
            break

    words.reverse()
    phrase = _clean(words)
    span = (first, last) if first is not None and last is not None else None

    # "registering a growth of 11.95%" measures the growth of whatever the
    # sentence was already talking about, so give the bare noun its subject back.
    if topic and phrase and phrase.lower() in DERIVATIVE:
        phrase = f"{topic} {phrase}"
    if is_rate and phrase and not any(w in phrase.lower().split() for w in DERIVATIVE):
        phrase = f"{phrase} growth"
    return MetricPhrase(phrase, is_rate, span, owner)


def phrase_after(text: str, value_end: int, claimed: list[tuple[int, int]]) -> MetricPhrase:
    """Fallback for layouts that put the number first ("₹8,142 Cr / FY24 revenue")."""
    words: list[str] = []
    first, last = None, None
    for token, start, end in _tokens(text):
        if end <= value_end:
            continue
        low = token.lower()
        if _masked(start, end, claimed):
            continue
        if token in _PUNCT:
            if words:
                break
            continue
        if not words and (low in BRIDGE or low in STOPPERS):
            continue
        if words and (low in STOPPERS or (low in BRIDGE and low not in INTERNAL)):
            break
        words.append(token)
        first = start if first is None else first
        last = end
        if len(words) >= _MAX_TOKENS:
            break
    phrase = _clean(words)
    return MetricPhrase(phrase, False, (first, last) if first is not None else None)


def choose(text: str, quantity, claimed: list[tuple[int, int]],
           topic: str | None) -> MetricPhrase:
    """Pick the phrase on the side of the number that actually names the metric.

    Currency and percentage values follow their metric ("revenue ... was X"),
    while counts and weights precede the noun they count ("33,000 customers").
    """
    before = phrase_for(text, quantity.start, claimed, topic)
    if quantity.unit in ("count", "tonne", "day"):
        after = phrase_after(text, quantity.end, claimed)
        if after.text and len(after.text) >= len(before.text) // 2:
            return MetricPhrase(after.text, before.is_rate, after.span, before.subject_hint)
    return before
