"""Shared parsing helpers: response classification, hashing and record normalisation."""
import hashlib
import json
import re
import unicodedata
from urllib.parse import urlsplit

from .models import ProviderRequest, ProviderResponse
from .ratelimit import retry_after_seconds

ORCID = re.compile(r"(\d{4}-\d{4}-\d{4}-\d{3}[\dX])$")


class Rejected(ValueError):
    """One provider record that cannot become a candidate; the reason is a short code."""


def sha256(data: bytes | str) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def query_hash(query: str) -> str:
    return sha256(" ".join(query.split()).casefold())


def classify(response: ProviderResponse):
    """(status, code, retry_after, parsed JSON or None) for one HTTP response."""
    if response.status == 429:
        return "rate_limited", "http-429", retry_after_seconds(response.headers), None
    if response.status >= 500:
        return "unavailable", f"http-{response.status}", retry_after_seconds(response.headers), None
    if response.status != 200:
        return "failed", f"http-{response.status}", None, None
    try:
        return "ok", None, None, json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "invalid_response", "body-not-json", None, None


def clean_text(value, limit: int = 1000) -> str | None:
    if isinstance(value, list):
        value = next((v for v in value if isinstance(v, str) and v.strip()), None)
    if not isinstance(value, str):
        return None
    text = " ".join(unicodedata.normalize("NFC", value).split())
    return text[:limit] or None


def https_url(value) -> str | None:
    """Provider landing URLs are kept only when they are plain https links."""
    if not isinstance(value, str):
        return None
    parts = urlsplit(value.strip())
    return value.strip() if parts.scheme == "https" and parts.netloc and not parts.username else None


def orcid(value) -> str | None:
    match = ORCID.search(value.strip()) if isinstance(value, str) else None
    return match.group(1) if match else None


def year(value) -> int | None:
    return value if isinstance(value, int) and 1600 <= value <= 2100 else None


def title_key(title: str) -> str:
    text = unicodedata.normalize("NFKD", title).casefold()
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w\s]", " ", text).split())


def request_hash(request: ProviderRequest) -> str:
    # The request names the search text; only its hash is kept as provenance.
    return sha256(request.key())
