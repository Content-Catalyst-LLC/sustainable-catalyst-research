"""One discovery run across providers: gating, retries, normalisation and duplicate handling."""
from collections import Counter
from datetime import datetime, timezone

from . import crossref, openalex
from .common import Rejected, canonical, classify, query_hash, request_hash, sha256, title_key
from .models import Candidate, CandidateSource, DiscoveryRun, FieldConflict, ProviderOutcome
from .ratelimit import RateLimiter, RetryPolicy
from .transport import (TRANSPORT_CODES, DisabledTransport, ProviderDisabled, ProviderHeld, TransportError,
                        TransportTimeout)

PROVIDER_ADAPTERS = {"openalex": openalex, "crossref": crossref}
ORDER = tuple(PROVIDER_ADAPTERS)  # merge precedence: earlier providers supply the first value of a field
CONFLICT_FIELDS = ("title", "publication_year", "venue")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def discover(query: str, *, providers=ORDER, enabled=frozenset(), transport=None, limiter: RateLimiter | None = None,
             retry: RetryPolicy = RetryPolicy(), publication_year: int | None = None, rows: int = 25,
             contact: str | None = None, now=_now) -> DiscoveryRun:
    """Search each provider that is both requested and explicitly enabled; nothing is persisted.

    ``enabled`` is empty by default, and ``transport`` defaults to ``DisabledTransport``, so a bare call
    makes no request at all.
    """
    if not 3 <= len(query.strip()) <= 240:
        raise ValueError("query must be 3-240 characters")
    transport = transport or DisabledTransport()
    limiter = limiter or RateLimiter()
    started, outcomes, found = now(), [], []
    for provider in providers:
        if provider not in PROVIDER_ADAPTERS:
            raise ValueError(f"unknown provider {provider!r}")
        if provider not in enabled:
            outcomes.append(ProviderOutcome(provider=provider, status="disabled", attempts=0, code="provider-not-enabled"))
            continue
        outcome, items = _run_provider(provider, query, transport, limiter, retry, publication_year, rows, contact, now)
        outcomes.append(outcome)
        found += items
    return DiscoveryRun(query_sha256=query_hash(query), started_at=started, transport=transport.kind,
                        external_request_performed=bool(getattr(transport, "external", False))
                        and any(o.attempts for o in outcomes),
                        outcomes=tuple(outcomes), candidates=tuple(merge(found)))


def _run_provider(provider, query, transport, limiter, retry, publication_year, rows, contact, now):
    adapter = PROVIDER_ADAPTERS[provider]
    request = adapter.build_request(query, publication_year=publication_year, rows=rows, contact=contact)
    attempt, status, code, wait_hint, data, response = 0, "failed", None, None, None, None
    while True:
        attempt += 1
        limiter.acquire(provider)
        try:
            response = transport.send(request)
            status, code, wait_hint, data = classify(response)
        except ProviderDisabled:
            return ProviderOutcome(provider=provider, status="disabled", attempts=0, code="transport-disabled"), []
        except ProviderHeld as held:  # a policy refused before sending, so this attempt made no request
            return ProviderOutcome(provider=provider, status=held.status, attempts=attempt - 1, code=held.code,
                                   retry_after_seconds=held.retry_after), []
        except TransportTimeout:
            status, code, wait_hint = "unavailable", "timeout", None
        except TransportError as error:
            status, code, wait_hint = "failed", str(error) if str(error) in TRANSPORT_CODES else "transport-error", None
        if status not in retry.retry_on:
            break
        wait = retry.wait_before(attempt, wait_hint)
        if wait is None:
            break
        limiter.sleep(wait)
    if status != "ok":
        return ProviderOutcome(provider=provider, status=status, attempts=attempt, code=code,
                               retry_after_seconds=wait_hint), []
    raw = adapter.records(data)
    if raw is None:
        return ProviderOutcome(provider=provider, status="invalid_response", attempts=attempt, code="unexpected-shape"), []
    retrieved, reasons, items = now(), Counter(), []
    limit = max(1, min(rows, adapter.MAX_ROWS))
    if len(raw) > limit:  # never more records than were asked for, whatever the provider returns
        reasons["beyond-requested-rows"] += len(raw) - limit
    for position, record in enumerate(raw[:limit]):
        try:
            fields = adapter.normalize(record)
        except Rejected as reason:
            reasons[str(reason)] += 1
            continue
        source = CandidateSource(provider=provider, provider_record_id=fields.pop("provider_record_id"),
                                 retrieved_at=retrieved, query_sha256=request.query_sha256,
                                 request_sha256=request_hash(request), response_sha256=sha256(response.body),
                                 record_sha256=sha256(canonical(record)))
        items.append((ORDER.index(provider), position, fields, source))
    return ProviderOutcome(provider=provider, status="ok", attempts=attempt, records_received=len(raw),
                           records_rejected=sum(reasons.values()), rejection_reasons=dict(reasons)), items


def _work_key(fields) -> str:
    first = fields["authors"][0]["name"].split()[-1].casefold() if fields["authors"] else ""
    return f"{title_key(fields['title'])}|{fields['publication_year'] or ''}|{first}"


def _comparable(field, value):
    return title_key(value) if field == "title" else value.casefold() if isinstance(value, str) else value


def merge(items) -> list[Candidate]:
    """Merge records of the same work: by DOI, and only without a DOI by title, year and first author.

    Different DOIs are never merged, even for the same title (a preprint and its article, say); such pairs
    and DOI-less records that look like a DOI-bearing one are marked ``possible_duplicate_of`` for review.
    """
    groups: dict[str, list] = {}
    for item in sorted(items, key=lambda i: (i[0], i[1])):
        fields = item[2]
        groups.setdefault(f"doi:{fields['doi']}" if fields["doi"] else f"work:{_work_key(fields)}", []).append(item)
    candidates, work_keys = [], {}
    for key, members in groups.items():
        candidate_id = "cand-" + sha256(key)[:24]
        work_keys.setdefault(_work_key(members[0][2]), []).append(candidate_id)
        merged, conflicts = dict(members[0][2]), []
        for field in ("venue", "work_type", "landing_url", "publication_year"):
            merged[field] = next((m[2][field] for m in members if m[2][field] is not None), None)
        if not merged["authors"]:
            merged["authors"] = next((m[2]["authors"] for m in members if m[2]["authors"]), [])
        flags = [m[2]["retracted"] for m in members if m[2]["retracted"] is not None]
        merged["retracted"] = True if True in flags else (False if flags else None)
        for field in CONFLICT_FIELDS:
            values = {}
            for _order, _pos, fields, source in members:
                if fields[field] is not None:
                    label = source.provider if source.provider not in values else f"{source.provider}#{len(values) + 1}"
                    values[label] = str(fields[field])
            if len({_comparable(field, v if field != "publication_year" else int(v)) for v in values.values()}) > 1:
                conflicts.append(FieldConflict(field=field, values=values))
        candidates.append({**merged, "candidate_id": candidate_id, "sources": tuple(m[3] for m in members),
                           "conflicts": tuple(conflicts), "_work": _work_key(members[0][2])})
    for candidate in candidates:
        others = [c for c in work_keys[candidate.pop("_work")] if c != candidate["candidate_id"]]
        candidate["possible_duplicate_of"] = tuple(sorted(others))
    return [Candidate(**c) for c in candidates]
