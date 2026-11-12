"""Crossref works search: request builder and record normaliser (no network)."""
from .common import Rejected, clean_text, https_url, orcid, query_hash, year
from .doi import normalize_doi
from .models import ProviderRequest

PROVIDER = "crossref"
URL = "https://api.crossref.org/works"
MAX_ROWS = 50
RETRACTION_TYPES = {"retraction", "withdrawal", "removal"}


def build_request(query: str, *, publication_year: int | None = None, rows: int = 25,
                  contact: str | None = None) -> ProviderRequest:
    params = [("query.bibliographic", " ".join(query.split())), ("rows", str(max(1, min(rows, MAX_ROWS))))]
    if publication_year is not None:
        params.append(("filter", f"from-pub-date:{publication_year},until-pub-date:{publication_year}"))
    if contact:
        params.append(("mailto", contact))  # the polite pool; only an operator-configured address
    return ProviderRequest(provider=PROVIDER, url=URL, params=tuple(params), query_sha256=query_hash(query))


def records(data) -> list | None:
    """The item list, or None when the body is not a successful Crossref work list."""
    if not isinstance(data, dict) or data.get("status") != "ok" or data.get("message-type") != "work-list":
        return None
    items = (data.get("message") or {}).get("items")
    return items if isinstance(items, list) else None


def _issued_year(record) -> int | None:
    for key in ("issued", "published", "published-print", "published-online"):
        parts = ((record.get(key) or {}).get("date-parts") or [[None]])[0]
        if parts and year(parts[0]):
            return parts[0]
    return None


def normalize(record) -> dict:
    if not isinstance(record, dict):
        raise Rejected("not-an-object")
    if any((u or {}).get("type") in RETRACTION_TYPES for u in record.get("update-to") or []):
        raise Rejected("retraction-notice")  # a notice about another work, not a work to cite
    doi = normalize_doi(record.get("DOI"))
    if not doi:
        raise Rejected("missing-or-invalid-doi")  # Crossref records are DOI registrations
    title = clean_text(record.get("title"))
    if not title:
        raise Rejected("missing-title")
    authors = []
    for author in record.get("author") or []:
        author = author or {}
        name = clean_text(" ".join(p for p in (author.get("given"), author.get("family")) if isinstance(p, str)), 300) \
            or clean_text(author.get("name"), 300)
        if name:
            authors.append({"name": name, "orcid": orcid(author.get("ORCID"))})
    updates = [(u or {}).get("type") for u in record.get("updated-by") or []]
    return {
        "provider_record_id": doi,
        "doi": doi,
        "title": title,
        "authors": authors,
        "publication_year": _issued_year(record),
        "venue": clean_text(record.get("container-title"), 500),
        "work_type": clean_text(record.get("type"), 80),
        "landing_url": https_url(record.get("URL")),
        "retracted": True if any(t in RETRACTION_TYPES for t in updates) else None,
    }
