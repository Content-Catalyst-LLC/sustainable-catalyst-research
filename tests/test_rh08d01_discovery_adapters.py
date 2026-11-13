"""RH-08D-01: OpenAlex and Crossref discovery adapters, offline with synthetic fixtures only."""
import json
import socket
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research-api"))

from app import discovery_adapters as da  # noqa: E402
from app.discovery_adapters import crossref, openalex  # noqa: E402
from app.discovery_adapters.ratelimit import retry_after_seconds  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "rh08d01"
QUERY = "synthetic basalt weathering"
NOW = "2026-11-12T00:00:00+00:00"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("RH-08D-01 tests must never open a network connection")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


class Clock:
    def __init__(self):
        self.t, self.slept = 0.0, []

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.slept.append(round(seconds, 6))
        self.t += seconds


def response(status=200, body=b"", **headers):
    return da.ProviderResponse(status=status, headers=headers, body=body)


def fixture(name, status=200, **headers):
    return response(status, (FIX / name).read_bytes(), **headers)


def run(responses, *, enabled=("openalex", "crossref"), retry=da.RetryPolicy(), clock=None):
    clock = clock or Clock()
    transport = da.FixtureTransport({openalex.build_request(QUERY).key(): responses.get("openalex", []),
                                     crossref.build_request(QUERY).key(): responses.get("crossref", [])})
    result = da.discover(QUERY, enabled=frozenset(enabled), transport=transport, retry=retry,
                         limiter=da.RateLimiter(rate_per_second=10, burst=1, clock=clock, sleep=clock.sleep),
                         now=lambda: NOW)
    return result, transport, clock


def by_doi(result):
    return {c.doi: c for c in result.candidates if c.doi}


# --- DOI normalisation -----------------------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("10.5555/SC-SYN-001", "10.5555/sc-syn-001"),
    ("https://doi.org/10.5555/sc-syn-001", "10.5555/sc-syn-001"),
    ("http://dx.doi.org/10.5555/sc-syn-001", "10.5555/sc-syn-001"),
    ("doi:10.5555/sc-syn-002.", "10.5555/sc-syn-002"),
    ("DOI: 10.5555/sc-syn-002;", "10.5555/sc-syn-002"),
    ("https://doi.org/doi:10.5555/x", "10.5555/x"),
    ("info:doi/10.5555/x", "10.5555/x"),
    ("10.5555/a%2Fb", "10.5555/a/b"),
    ("10.5555/(a)b)", "10.5555/(a)b"),
    ("10.5555/a(b)", "10.5555/a(b)"),
    ("10.1000.10/xyz", "10.1000.10/xyz"),
])
def test_doi_normalisation(raw, expected):
    assert da.normalize_doi(raw) == expected


@pytest.mark.parametrize("raw", [None, 10.5555, "", "11.1234/x", "10.12/x", "10.5555", "10.5555/", "10.5555/a b",
                                 "10.5555/a\x00b", "https://example.org/10.5555/x", "10.5555/" + "x" * 300])
def test_invalid_dois_are_refused_not_guessed(raw):
    assert da.normalize_doi(raw) is None


# --- request builders ------------------------------------------------------------------------------------

def test_requests_are_bounded_carry_no_credentials_and_hash_the_query():
    o = openalex.build_request("  synthetic   basalt  ", publication_year=2024, rows=500)
    c = crossref.build_request("synthetic basalt", publication_year=2024, rows=0)
    assert o.url == "https://api.openalex.org/works" and c.url == "https://api.crossref.org/works"
    assert dict(o.params) == {"search": "synthetic basalt", "per-page": "50", "filter": "publication_year:2024"}
    assert dict(c.params) == {"query.bibliographic": "synthetic basalt", "rows": "1",
                              "filter": "from-pub-date:2024,until-pub-date:2024"}
    assert o.query_sha256 == c.query_sha256  # the same normalised search text
    assert not any(k in ("mailto", "api_key", "key") for k, _ in o.params + c.params)
    assert ("mailto", "ops@example.org") in openalex.build_request(QUERY, contact="ops@example.org").params


# --- gating: disabled by default, no request, no network ------------------------------------------------

def test_a_bare_run_makes_no_request_and_returns_nothing():
    result = da.discover(QUERY, now=lambda: NOW)
    assert [o.status for o in result.outcomes] == ["disabled", "disabled"]
    assert all(o.attempts == 0 for o in result.outcomes)
    assert result.candidates == () and result.external_request_performed is False
    assert result.persisted is False and result.publishable is False


def test_enabled_provider_with_the_default_transport_is_still_refused():
    result = da.discover(QUERY, enabled=frozenset({"openalex"}), now=lambda: NOW,
                         limiter=da.RateLimiter(clock=Clock(), sleep=lambda s: None))
    assert result.outcomes[0].status == "disabled" and result.outcomes[0].code == "transport-disabled"
    assert result.outcomes[1].code == "provider-not-enabled"


def test_only_enabled_providers_are_called():
    result, transport, _ = run({"openalex": fixture("openalex-works.json")}, enabled=("openalex",))
    assert [r.provider for r in transport.sent] == ["openalex"]
    assert [o.status for o in result.outcomes] == ["ok", "disabled"]


def test_unknown_providers_and_bad_queries_are_refused():
    with pytest.raises(ValueError):
        da.discover(QUERY, providers=("europe_pmc",))
    with pytest.raises(ValueError):
        da.discover("ab")


def test_only_the_https_module_has_network_code():
    # RH-08D-02 adds https_transport; no other adapter module may touch the network. RH-08D-03: only the
    # guarded transport in live.py may import it, lazily, when a staging search builds it.
    package = ROOT / "research-api/app/discovery_adapters"
    source = "\n".join(p.read_text() for p in package.glob("*.py") if p.name not in ("https_transport.py", "live.py"))
    for forbidden in ("import urllib.request", "urlopen", "import requests", "import httpx", "http.client",
                      "socket.", "aiohttp", "from .https_transport", "import https_transport"):
        assert forbidden not in source, forbidden
    live = (package / "live.py").read_text()
    assert live.count("from .https_transport import HttpsTransport") == 1
    for forbidden in ("import urllib.request", "urlopen", "import requests", "import httpx", "http.client", "socket."):
        assert forbidden not in live, forbidden


# --- normalisation, provenance and classification ----------------------------------------------------------

def test_candidates_are_unreviewed_unpublishable_and_carry_provenance():
    result, _, _ = run({"openalex": fixture("openalex-works.json"), "crossref": fixture("crossref-works.json")})
    assert len(result.candidates) == 9
    body = (FIX / "openalex-works.json").read_bytes()
    for c in result.candidates:
        assert c.classification == "discovered-unreviewed" and c.review_status == "unreviewed"
        assert c.publishable is False and c.title.startswith("SYNTHETIC")
        for s in c.sources:
            assert s.retrieved_at == NOW and s.adapter_version == da.ADAPTER_VERSION
            assert s.query_sha256 == result.query_sha256 and len(s.record_sha256) == 64
    first = by_doi(result)["10.5555/sc-syn-001"]
    assert first.sources[0].response_sha256 == __import__("hashlib").sha256(body).hexdigest()
    assert first.authors[0].orcid == "0000-0002-1825-0097"
    assert first.landing_url == "https://example.org/synthetic/1"
    # The search text itself is stored nowhere in the run.
    assert QUERY not in result.model_dump_json(by_alias=True)


def test_records_that_cannot_be_candidates_are_counted_not_invented():
    result, _, _ = run({"openalex": fixture("openalex-works.json"), "crossref": fixture("crossref-works.json")})
    o, c = result.outcomes
    assert (o.records_received, o.records_rejected, o.rejection_reasons) == \
        (9, 2, {"missing-provider-id": 1, "missing-title": 1})
    assert (c.records_received, c.records_rejected, c.rejection_reasons) == \
        (7, 3, {"retraction-notice": 1, "missing-or-invalid-doi": 1, "missing-title": 1})
    invalid = [x for x in result.candidates if x.title == "SYNTHETIC Record with an invalid DOI"][0]
    assert invalid.doi is None  # kept as a candidate, but without a guessed DOI


def test_retraction_states_and_unsafe_links():
    result, _, _ = run({"openalex": fixture("openalex-works.json"), "crossref": fixture("crossref-works.json")})
    dois = by_doi(result)
    assert dois["10.5555/sc-syn-007"].retracted is True       # OpenAlex is_retracted
    assert dois["10.5555/sc-syn-010"].retracted is True       # Crossref updated-by retraction
    assert dois["10.5555/sc-syn-001"].retracted is False
    assert dois["10.5555/sc-syn-013"].retracted is None       # unknown is not "not retracted"
    assert "10.5555/sc-syn-011" not in dois                    # the notice itself is not a work
    assert dois["10.5555/sc-syn-007"].landing_url is None      # http, not https


def test_no_abstract_or_full_text_is_kept():
    fields = set(da.Candidate.model_fields)
    assert not fields & {"abstract", "full_text", "abstract_inverted_index"}


# --- duplicate handling ------------------------------------------------------------------------------------

def test_duplicates_merge_by_doi_within_and_across_providers():
    result, _, _ = run({"openalex": fixture("openalex-works.json"), "crossref": fixture("crossref-works.json")})
    dois = by_doi(result)
    within = dois["10.5555/sc-syn-001"]
    assert [s.provider_record_id for s in within.sources] == ["https://openalex.org/W9000001", "https://openalex.org/W9000004"]
    assert within.conflicts == (da.FieldConflict(field="venue", values={
        "openalex": "Synthetic Journal of Soil Studies", "openalex#2": "Synthetic Soil Studies"}),)
    across = dois["10.5555/sc-syn-002"]
    assert [s.provider for s in across.sources] == ["openalex", "crossref"]
    assert across.publication_year == 2023  # first provider's value; the disagreement is recorded, not hidden
    assert across.conflicts == (da.FieldConflict(field="publication_year", values={"openalex": "2023", "crossref": "2022"}),)
    assert across.authors[0].name == "C. Example"
    # Title differences in case and punctuation only are not conflicts.
    assert not [x for x in across.conflicts if x.field == "title"]


def test_different_dois_are_never_merged_but_are_flagged():
    result, _, _ = run({"crossref": fixture("crossref-works.json")}, enabled=("crossref",))
    dois = by_doi(result)
    pre, art = dois["10.5555/sc-syn-013"], dois["10.5555/sc-syn-014"]
    assert pre.candidate_id != art.candidate_id
    assert pre.possible_duplicate_of == (art.candidate_id,) and art.possible_duplicate_of == (pre.candidate_id,)


def test_a_record_without_doi_is_flagged_against_the_matching_doi_record():
    result, _, _ = run({"openalex": fixture("openalex-works.json")}, enabled=("openalex",))
    no_doi = [c for c in result.candidates if c.title == "SYNTHETIC Weathering model notes" and c.doi is None][0]
    with_doi = by_doi(result)["10.5555/sc-syn-006"]
    assert no_doi.possible_duplicate_of == (with_doi.candidate_id,)
    assert with_doi.possible_duplicate_of == (no_doi.candidate_id,)


def test_candidate_ids_are_stable_across_runs_and_provider_sets():
    a, _, _ = run({"openalex": fixture("openalex-works.json"), "crossref": fixture("crossref-works.json")})
    b, _, _ = run({"crossref": fixture("crossref-works.json")}, enabled=("crossref",))
    assert by_doi(a)["10.5555/sc-syn-002"].candidate_id == by_doi(b)["10.5555/sc-syn-002"].candidate_id
    assert [c.candidate_id for c in a.candidates] == [c.candidate_id for c in run(
        {"openalex": fixture("openalex-works.json"), "crossref": fixture("crossref-works.json")})[0].candidates]


# --- failure handling ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("provider, reply, status, code", [
    ("crossref", fixture("crossref-validation-failure.json", 400), "failed", "http-400"),
    ("crossref", fixture("crossref-validation-failure.json"), "invalid_response", "unexpected-shape"),
    ("openalex", fixture("openalex-unexpected-shape.json"), "invalid_response", "unexpected-shape"),
    ("openalex", fixture("not-json.txt"), "invalid_response", "body-not-json"),
    ("openalex", response(404), "failed", "http-404"),
    ("openalex", da.TransportError("synthetic"), "failed", "transport-error"),
])
def test_provider_failures_return_a_typed_outcome_and_no_candidates(provider, reply, status, code):
    result, transport, _ = run({provider: reply}, enabled=(provider,))
    outcome = result.outcomes[0 if provider == "openalex" else 1]
    assert (outcome.status, outcome.code, outcome.attempts) == (status, code, 1)
    assert result.candidates == ()


def test_one_provider_failing_does_not_hide_the_other():
    result, _, _ = run({"openalex": response(500), "crossref": fixture("crossref-works.json")},
                       retry=da.RetryPolicy(max_attempts=1))
    assert [o.status for o in result.outcomes] == ["unavailable", "ok"]
    assert {s.provider for c in result.candidates for s in c.sources} == {"crossref"}


# --- rate limits and retries ---------------------------------------------------------------------------------

def test_rate_limited_then_success_honours_retry_after():
    result, transport, clock = run({"openalex": [response(429, **{"Retry-After": "2"}), fixture("openalex-works.json")]},
                                   enabled=("openalex",))
    assert result.outcomes[0].status == "ok" and result.outcomes[0].attempts == 2
    assert 2.0 in clock.slept and len(transport.sent) == 2


def test_server_errors_back_off_exponentially_then_give_up():
    result, transport, clock = run({"openalex": [response(503), response(503), response(503), response(503)]},
                                   enabled=("openalex",), retry=da.RetryPolicy(max_attempts=3, backoff_seconds=1))
    outcome = result.outcomes[0]
    assert (outcome.status, outcome.code, outcome.attempts) == ("unavailable", "http-503", 3)
    assert [s for s in clock.slept if s >= 1] == [1.0, 2.0] and len(transport.sent) == 3


def test_a_retry_after_beyond_the_budget_stops_without_waiting():
    result, transport, clock = run({"openalex": response(429, **{"Retry-After": "3600"})}, enabled=("openalex",))
    outcome = result.outcomes[0]
    assert (outcome.status, outcome.attempts, outcome.retry_after_seconds) == ("rate_limited", 1, 3600.0)
    assert 3600.0 not in clock.slept


def test_timeouts_are_retried_as_unavailable():
    result, _, _ = run({"openalex": [da.TransportTimeout("synthetic"), fixture("openalex-works.json")]},
                       enabled=("openalex",))
    assert result.outcomes[0].status == "ok" and result.outcomes[0].attempts == 2


def test_token_bucket_spaces_requests():
    clock = Clock()
    limiter = da.RateLimiter(rate_per_second=2, burst=1, clock=clock, sleep=clock.sleep)
    assert [limiter.acquire("openalex") for _ in range(3)] == [0.0, 0.5, 0.5]
    assert limiter.acquire("crossref") == 0.0  # buckets are per provider
    with pytest.raises(ValueError):
        da.RateLimiter(rate_per_second=0)


def test_retry_after_parsing():
    assert retry_after_seconds({"retry-after": "7"}) == 7.0
    assert retry_after_seconds({"Retry-After": "Thu, 12 Nov 2026 00:00:10 GMT"}, now_epoch=1794441600.0) == 10.0
    assert retry_after_seconds({"Retry-After": "soon"}) is None and retry_after_seconds({}) is None


# --- publication guard ---------------------------------------------------------------------------------------

def test_candidates_and_runs_cannot_be_marked_publishable_or_reviewed():
    result, _, _ = run({"crossref": fixture("crossref-works.json")}, enabled=("crossref",))
    data = json.loads(result.candidates[0].model_dump_json())
    for change in ({"publishable": True}, {"review_status": "approved"}, {"classification": "evidence"},
                   {"unexpected": 1}):
        with pytest.raises(ValidationError):
            da.Candidate(**{**data, **change})
    with pytest.raises(ValidationError):
        da.DiscoveryRun(**{**json.loads(result.model_dump_json(by_alias=True)), "persisted": True})


def test_the_public_search_route_stays_disabled():
    from fastapi.testclient import TestClient
    from app.main import app
    response = TestClient(app).post("/api/research/v1/discovery/search", json={"query": QUERY, "provider": "openalex"})
    assert response.status_code == 503
    assert response.json()["detail"]["external_request_performed"] is False
