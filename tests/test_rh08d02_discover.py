"""RH-08D-02: prepared HTTPS transport, gated search API, Library intake drafts and the Discover interface.

Everything is offline. A fake resolver and connection stand in for DNS and TLS; any real socket fails the run.
"""
import json
import socket
import ssl
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research-api"))
sys.path.insert(0, str(ROOT / "tests/fixtures/rh08d02"))

from fastapi.testclient import TestClient  # noqa: E402

from app import discovery  # noqa: E402
from app import discovery_adapters as da  # noqa: E402
from app.discovery_adapters import crossref, https_transport, library_intake, openalex  # noqa: E402
from app.discovery_adapters.https_transport import HttpsTransport  # noqa: E402
from app.main import app  # noqa: E402
import make_synthetic_search  # noqa: E402

FIX = ROOT / "tests/fixtures/rh08d01"
QUERY = "synthetic basalt weathering"
PUBLIC_V4, PUBLIC_V6 = "1.1.1.1", "2606:4700:4700::1111"  # public addresses; the fake connector never dials them


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("RH-08D-02 tests must never open a network connection")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


class FakeResponse:
    def __init__(self, status=200, body=b"{}", headers=None):
        self.status, self._body = status, body
        self._headers = list((headers or {"Content-Type": "application/json"}).items())

    def getheader(self, name):
        return next((v for k, v in self._headers if k.lower() == name.lower()), None)

    def getheaders(self):
        return self._headers

    def read(self, limit):
        return self._body[:limit]


class FakeConnection:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.requests, self.closed = response or FakeResponse(), error, [], False

    def request(self, method, path, headers):
        self.requests.append((method, path, headers))
        if self.error:
            raise self.error

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


def transport(connection=None, addresses=(PUBLIC_V4,), **kwargs):
    seen = {"connections": [], "resolved": []}
    connection = connection or FakeConnection()

    def resolver(host):
        seen["resolved"].append(host)
        return list(addresses)

    def connector(host, address, timeout):
        seen["connections"].append((host, address, timeout))
        return connection

    t = HttpsTransport(user_agent="test-agent", resolver=resolver, connector=connector, **kwargs)
    return t, connection, seen


# --- HTTPS transport: request vetting --------------------------------------------------------------------

def test_an_allowed_request_goes_to_the_vetted_address_with_safe_headers():
    t, connection, seen = transport(FakeConnection(FakeResponse(200, b'{"ok": 1}', {
        "Content-Type": "application/json", "Set-Cookie": "a=b", "X-RateLimit-Remaining": "9", "Server": "x"})))
    request = openalex.build_request(QUERY, publication_year=2024, contact="ops@example.org")
    response = t.send(request)
    assert seen["resolved"] == ["api.openalex.org"]
    assert seen["connections"] == [("api.openalex.org", PUBLIC_V4, 10.0)]
    method, path, headers = connection.requests[0]
    assert method == "GET"
    assert path == "/works?search=synthetic+basalt+weathering&per-page=25&filter=publication_year%3A2024&mailto=ops%40example.org"
    assert headers == {"Host": "api.openalex.org", "User-Agent": "test-agent", "Accept": "application/json",
                       "Accept-Encoding": "identity", "Connection": "close"}
    assert response.status == 200 and response.body == b'{"ok": 1}'
    assert response.headers == {"Content-Type": "application/json", "X-RateLimit-Remaining": "9"}
    assert connection.closed


@pytest.mark.parametrize("provider, url", [
    ("openalex", "https://api.crossref.org/works"),              # the other provider's host
    ("crossref", "https://api.openalex.org/works"),
    ("openalex", "http://api.openalex.org/works"),               # not https
    ("openalex", "https://api.openalex.org:8443/works"),         # another port
    ("openalex", "https://user:pw@api.openalex.org/works"),      # user information
    ("openalex", "https://api.openalex.org/authors"),            # another path
    ("openalex", "https://api.openalex.org/works?x=1"),          # query smuggled into the URL
    ("openalex", "https://api.openalex.org.example.org/works"),  # look-alike host
    ("crossref", "https://169.254.169.254/works"),               # metadata service
])
def test_requests_outside_the_allowlist_are_refused_before_dns(provider, url):
    t, connection, seen = transport()
    request = da.ProviderRequest(provider=provider, url=url, params=(), query_sha256="0" * 64)
    with pytest.raises(da.TransportError, match="request-not-allowed"):
        t.send(request)
    assert seen["resolved"] == [] and seen["connections"] == []


@pytest.mark.parametrize("addresses", [
    ["127.0.0.1"], ["10.0.0.5"], ["172.16.0.1"], ["192.168.1.1"], ["169.254.169.254"], ["100.64.0.1"],
    ["::1"], ["fc00::1"], ["fe80::1"], ["0.0.0.0"], ["224.0.0.1"], ["192.0.2.1"], ["::ffff:127.0.0.1"],
    [PUBLIC_V4, "10.0.0.5"], [],
])
def test_hosts_resolving_to_non_public_addresses_are_refused(addresses):
    t, connection, seen = transport(addresses=addresses)
    with pytest.raises(da.TransportError):
        t.send(crossref.build_request(QUERY))
    assert seen["connections"] == []


def test_ipv6_public_addresses_are_accepted():
    t, _, seen = transport(addresses=[PUBLIC_V6])
    t.send(crossref.build_request(QUERY))
    assert seen["connections"][0][:2] == ("api.crossref.org", PUBLIC_V6)


def test_dns_failure_is_a_transport_error():
    def broken(host):
        raise socket.gaierror("synthetic")
    t = HttpsTransport(user_agent="x", resolver=broken, connector=lambda *a: pytest.fail("connected"))
    with pytest.raises(da.TransportError, match="dns-failure"):
        t.send(openalex.build_request(QUERY))


# --- HTTPS transport: responses and failures ---------------------------------------------------------------

def test_oversized_bodies_are_refused():
    t, connection, _ = transport(FakeConnection(FakeResponse(200, b"x" * 101)), max_bytes=100)
    with pytest.raises(da.TransportError, match="response-too-large"):
        t.send(openalex.build_request(QUERY))
    assert connection.closed


def test_compressed_bodies_are_refused():
    t, _, _ = transport(FakeConnection(FakeResponse(200, b"\x1f\x8b", {"Content-Encoding": "gzip"})))
    with pytest.raises(da.TransportError, match="unexpected-content-encoding"):
        t.send(openalex.build_request(QUERY))


def test_redirects_are_returned_not_followed():
    t, connection, _ = transport(FakeConnection(FakeResponse(301, b"", {"Location": "https://example.org/"})))
    response = t.send(openalex.build_request(QUERY))
    assert response.status == 301 and "Location" not in response.headers and len(connection.requests) == 1
    from app.discovery_adapters.common import classify  # any non-200 becomes a failed outcome
    assert classify(response)[:2] == ("failed", "http-301")


@pytest.mark.parametrize("error, expected", [
    (TimeoutError("synthetic"), da.TransportTimeout),
    (ssl.SSLCertVerificationError("synthetic"), da.TransportError),
    (ConnectionResetError("synthetic"), da.TransportError),
])
def test_network_failures_become_transport_exceptions(error, expected):
    t, connection, _ = transport(FakeConnection(error=error))
    with pytest.raises(expected):
        t.send(openalex.build_request(QUERY))
    assert connection.closed


def test_tls_context_verifies_certificates_and_host_names():
    context = https_transport.tls_context()
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname is True
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2
    connection = https_transport._connect("api.openalex.org", PUBLIC_V4, 5.0)  # constructing does not connect
    assert connection.host == "api.openalex.org" and connection.port == 443 and connection._sc_address == PUBLIC_V4


def test_transport_limits_are_bounded():
    for kwargs in ({"timeout": 0}, {"timeout": 31}, {"max_bytes": 0}, {"max_bytes": 10_000_001}):
        with pytest.raises(ValueError):
            HttpsTransport(user_agent="x", **kwargs)


def test_https_transport_end_to_end_with_synthetic_bodies():
    bodies = {"api.openalex.org": (FIX / "openalex-works.json").read_bytes(),
              "api.crossref.org": (FIX / "crossref-works.json").read_bytes()}

    def connector(host, address, timeout):
        return FakeConnection(FakeResponse(200, bodies[host]))

    t = HttpsTransport(user_agent="x", resolver=lambda h: [PUBLIC_V4], connector=connector)
    run = da.discover(QUERY, enabled=frozenset({"openalex", "crossref"}), transport=t,
                      limiter=da.RateLimiter(rate_per_second=10, clock=lambda: 0.0, sleep=lambda s: None))
    assert run.transport == "https" and run.external_request_performed is True
    assert len(run.candidates) == 9 and run.publishable is False and run.persisted is False


# --- search API: gated -------------------------------------------------------------------------------------

client = TestClient(app)


def test_readiness_reports_prepared_but_inactive():
    data = client.get("/api/research/v1/discovery/readiness").json()
    assert data["stage"] == "RH-08D-02" and data["status"] == "not_activated"
    assert data["transport_prepared"] is True
    for key in ("external_retrieval_enabled", "saved_searches_enabled", "library_intake_enabled",
                "publication_enabled", "research_evidence_promotion_enabled"):
        assert data[key] is False, key


def test_live_providers_are_not_authorized_and_the_transport_is_never_built(monkeypatch):
    assert discovery.LIVE_PROVIDERS_AUTHORIZED is False
    monkeypatch.setattr(discovery, "live_transport", lambda: pytest.fail("live transport constructed"))
    response = client.post("/api/research/v1/discovery/search", json={"query": QUERY})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "DISCOVERY_PROVIDERS_DISABLED"
    assert response.json()["detail"]["stage"] == "RH-08D-02"
    assert all(p["enabled"] is False for p in client.get("/api/research/v1/discovery/providers").json()["providers"])


def test_live_gate_is_a_reviewed_code_constant_not_configuration():
    source = (ROOT / "research-api/app/discovery.py").read_text()
    assert "LIVE_PROVIDERS_AUTHORIZED = False" in source
    assert "LIVE_PROVIDERS_AUTHORIZED = os" not in source and "getenv(\"RESEARCH_DISCOVERY_LIVE" not in source


def gated(monkeypatch, transport):
    monkeypatch.setattr(discovery, "LIVE_PROVIDERS_AUTHORIZED", True)
    monkeypatch.setattr(discovery, "live_transport", lambda: transport)
    monkeypatch.setattr(discovery, "_LIMITER", da.RateLimiter(rate_per_second=10, clock=lambda: 0.0, sleep=lambda s: None))


def test_the_gated_path_returns_a_typed_unsaved_run(monkeypatch):
    def reply(name):
        return da.ProviderResponse(status=200, body=(FIX / name).read_bytes())
    gated(monkeypatch, da.FixtureTransport({
        openalex.build_request(QUERY, rows=25).key(): reply("openalex-works.json"),
        crossref.build_request(QUERY, rows=25).key(): reply("crossref-works.json")}))
    response = client.post("/api/research/v1/discovery/search", json={"query": QUERY})
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    data = response.json()
    assert data["schema"] == "sc-research-discovery-search/1.0" and data["saved"] is False and data["publishable"] is False
    assert data["run"]["schema"] == "sc-research-discovery-run/1.0" and len(data["run"]["candidates"]) == 9
    assert data["library_intake"]["enabled"] is False
    assert set(data["library_intake"]["blocking_issues"]) == {c["candidate_id"] for c in data["run"]["candidates"]}
    assert QUERY not in response.text  # the search text appears only as a hash


def test_the_gated_path_refuses_sources_without_adapters(monkeypatch):
    gated(monkeypatch, da.FixtureTransport({}))
    response = client.post("/api/research/v1/discovery/search", json={"query": QUERY, "provider": "arxiv"})
    assert response.status_code == 503 and response.json()["detail"]["code"] == "DISCOVERY_PROVIDER_NOT_AVAILABLE"


def test_the_gated_path_reports_provider_failures_without_candidates(monkeypatch):
    gated(monkeypatch, da.FixtureTransport({openalex.build_request(QUERY, rows=25).key(): da.TransportTimeout("x")}))
    data = client.post("/api/research/v1/discovery/search", json={"query": QUERY, "provider": "openalex"}).json()
    assert data["run"]["outcomes"][0]["status"] == "unavailable" and data["run"]["candidates"] == []


# --- Library intake: drafts only ---------------------------------------------------------------------------

def synthetic_candidates():
    data = make_synthetic_search.build()
    return [da.Candidate(**c) for c in data["run"]["candidates"]]


def test_intake_drafts_copy_provenance_and_list_blocking_issues():
    by_title = {c.title: c for c in synthetic_candidates()}
    clean = by_title["SYNTHETIC Basalt weathering rates in field trials"]
    proposal = library_intake.draft_proposal(clean, article_slug="example-article")
    assert proposal.status == "draft-not-submitted" and proposal.library_review_required is True
    assert proposal.evidence_status == "not-evidence" and proposal.sources == clean.sources
    assert proposal.blocking_issues == ("metadata-conflict",)
    retracted = [c for c in by_title.values() if c.retracted is True][0]
    assert "retracted" in library_intake.draft_proposal(retracted).blocking_issues
    no_doi = [c for c in by_title.values() if c.doi is None][0]
    assert "no-doi" in library_intake.blocking_issues(no_doi)


def test_intake_cannot_be_submitted_or_marked_otherwise():
    candidate = synthetic_candidates()[0]
    proposal = library_intake.draft_proposal(candidate)
    assert library_intake.LIBRARY_INTAKE_ENABLED is False
    with pytest.raises(library_intake.LibraryIntakeDisabled):
        library_intake.submit(proposal)
    data = proposal.model_dump(by_alias=True)
    for change in ({"status": "submitted"}, {"library_review_required": False}, {"evidence_status": "evidence"},
                   {"article_slug": "../private"}, {"workspace_id": "x"}):
        with pytest.raises(ValidationError):
            library_intake.LibraryIntakeProposal(**{**data, **change})


# --- Discover interface ------------------------------------------------------------------------------------

def test_the_committed_synthetic_response_is_current():
    committed = (ROOT / "tests/fixtures/rh08d02/discovery-search-synthetic.json").read_text(encoding="utf-8")
    assert committed == make_synthetic_search.render(make_synthetic_search.build()), \
        "regenerate with: python tests/fixtures/rh08d02/make_synthetic_search.py"
    assert json.loads(committed)["run"]["transport"] == "fixture"


def test_page_loads_the_discover_module_under_the_existing_policy():
    html = (ROOT / "research-web/index.html").read_text(encoding="utf-8")
    assert html.index("/assets/research-discovery.js") < html.index("/assets/app.js")
    assert '<link rel="stylesheet" href="/assets/research-discovery.css">' in html
    nginx = (ROOT / "research-web/nginx.conf").read_text()
    assert "script-src 'self';" in nginx and "connect-src 'self';" in nginx
    js = (ROOT / "research-web/assets/research-discovery.js").read_text()
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "eval(", "new Function", "fetch(",
                      "XMLHttpRequest", "localStorage", "sessionStorage", "document.write"):
        assert forbidden not in js, forbidden
