"""DOI normalisation: one canonical, comparable form, or ``None``.

DOIs are case-insensitive (DOI Handbook 2.4), so the canonical form is lower case, without resolver
prefixes, percent-encoding or trailing sentence punctuation. Anything that is not ``10.<registrant>/<suffix>``
is refused rather than guessed.
"""
import re
from urllib.parse import unquote

_PREFIXES = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|(?:dx\.)?doi\.org/|doi:\s*|info:doi/)", re.IGNORECASE)
_DOI = re.compile(r"^10\.\d{4,9}(?:\.\d+)*/\S+$")
_TRAILING = ".,;:"
MAX_LENGTH = 300


def normalize_doi(value) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    for _ in range(2):  # "doi:" after a resolver prefix also occurs
        text = _PREFIXES.sub("", text).strip()
    if "%" in text:
        text = unquote(text)
    text = text.rstrip(_TRAILING)
    # A closing bracket belongs to the DOI only when it is balanced inside it.
    while text.endswith(")") and text.count(")") > text.count("("):
        text = text[:-1]
    text = text.lower()
    if len(text) > MAX_LENGTH or any(ord(c) < 32 or c.isspace() for c in text) or not _DOI.fullmatch(text):
        return None
    return text
