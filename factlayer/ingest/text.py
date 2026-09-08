"""Text normalisation applied before any character offset is recorded.

Every fact in the system points at a ``(page, char_start, char_end)`` span of the
*stored* page text.  That only stays true if normalisation happens once, here,
before the page text is assembled -- never afterwards.
"""

from __future__ import annotations

import re
import unicodedata

# Characters PDF producers emit that break naive matching.  Mapped to ASCII so
# that a quote typed by a user, a regex, and the stored evidence all agree.
_CHAR_MAP = {
    " ": " ",   # no-break space
    " ": " ",   # narrow no-break space
    " ": " ",   # thin space
    " ": " ",   # figure space
    "​": "",    # zero-width space
    "‌": "",    # zero-width non-joiner
    "‍": "",    # zero-width joiner
    "﻿": "",    # BOM
    "­": "",    # soft hyphen
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"',
    "–": "-", "—": "-", "‒": "-", "−": "-",
    "…": "...",
    " ": "\n", " ": "\n",
    "\t": " ",
}
_TRANSLATION = {ord(k): v for k, v in _CHAR_MAP.items()}

# A line ending in a hyphen followed by a lowercase continuation is almost
# always a soft line-break inside one word ("infra-\nstructure").
_HYPHEN_BREAK = re.compile(r"(?<=[a-z])-\n(?=[a-z])")
_SPACES = re.compile(r"[ ]{2,}")
_BLANKS = re.compile(r"\n{3,}")


def normalise(text: str) -> str:
    """Canonicalise PDF text.  Idempotent; must run before offsets are taken."""
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_TRANSLATION)
    text = _HYPHEN_BREAK.sub("", text)
    text = _SPACES.sub(" ", text)
    text = _BLANKS.sub("\n\n", text)
    return text.strip()


def collapse_ws(text: str) -> str:
    """Whitespace-insensitive form, for comparing quotes to page text."""
    return " ".join(text.split())
