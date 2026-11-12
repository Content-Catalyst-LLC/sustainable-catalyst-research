"""OpenAlex works search: request builder and record normaliser (no network)."""
from .common import Rejected, clean_text, https_url, orcid, query_hash, year
from .doi import normalize_doi
from .models import ProviderRequest

PROVIDER = "openalex"
URL = "https://api.openalex.org/works"
MAX_ROWS = 50


def build_request(query: str, *, publication_year: int | None = None, rows: int = 25,
                  contact: str | None = None) -> ProviderRequest:
    params = [("search", " ".join(query.split())), ("per-page", str(max(1, min(rows, MAX_ROWS))))]
    if publication_year is not None:
        params.append(("filter", f"publication_year:{publication_year}"))
    if contact:
        params.append(("mailto", contact))  # the polite pool; only an operator-configured address
    return ProviderRequest(provider=PROVIDER, url=URL, params=tuple(params), query_sha256=query_hash(query))


def records(data) -> list | None:
    """The result list, or None when the body is not an OpenAlex works page."""
    results = data.get("results") if isinstance(data, dict) else None
    return results if isinstance(results, list) and isinstance(data.get("meta"), dict) else None


def normalize(record) -> dict:
    if not isinstance(record, dict):
        raise Rejected("not-an-object")
    record_id = record.get("id")
    if not isinstance(record_id, str) or not record_id.startswith("https://openalex.org/W"):
        raise Rejected("missing-provider-id")
    title = clean_text(record.get("title") or record.get("display_name"))
    if not title:
        raise Rejected("missing-title")
    authors = []
    for authorship in record.get("authorships") or []:
        author = (authorship or {}).get("author") or {}
        name = clean_text(author.get("display_name"), 300)
        if name:
            authors.append({"name": name, "orcid": orcid(author.get("orcid"))})
    location = record.get("primary_location") or {}
    source = location.get("source") or {}
    retracted = record.get("is_retracted")
    return {
        "provider_record_id": record_id,
        "doi": normalize_doi(record.get("doi")),
        "title": title,
        "authors": authors,
        "publication_year": year(record.get("publication_year")),
        "venue": clean_text(source.get("display_name"), 500),
        "work_type": clean_text(record.get("type"), 80),
        "landing_url": https_url(location.get("landing_page_url")),
        "retracted": retracted if isinstance(retracted, bool) else None,
    }
