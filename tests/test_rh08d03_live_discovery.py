"""RH-08D-03: controlled live discovery, integration-tested offline.

Each search goes through the real route, activation, guarded transport, HTTPS transport, adapters and merge.
Only the network is fake: a resolver and connector that serve synthetic provider bodies. Sockets and DNS are
blocked, so a test that tried to reach a provider would fail.
"""
import ast
import hashlib
import json
import logging
import socket
import ssl
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research-api"))

from fastapi.testclient import TestClient  # noqa: E402

from app import discovery  # noqa: E402
from app import discovery_adapters as da  # noqa: E402
from app.discovery_adapters import activation, https_transport, live  # noqa: E402
from app.discovery_adapters.https_transport import HttpsTransport  # noqa: E402
from app.main import app  # noqa: E402

FIX = ROOT / "tests/fixtures/rh08d01"
QUERY = "synthetic basalt weathering"
CONTACT = "discovery-tests@synthetic-contact.sustainablecatalyst.com"
KEY = "synthetic-openalex-key-0123456789"
KEY_FILE = Path(tempfile.mkdtemp()) / "openalex-key"
KEY_FILE.write_text(KEY + "\n")
PUBLIC_V4 = "1.1.1.1"
SEARCH = "/api/research/v1/discovery/search"
OPENALEX, CROSSREF = (FIX / "openalex-works.json").read_bytes(), (FIX / "crossref-works.json").read_bytes()
client = TestClient(app)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("RH-08D-03 tests must never open a network connection")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FakeResponse:
    def __init__(self, status=200, body=b"{}", headers=None, seconds_per_chunk=0.0, clock=None):
        self.status, self._body, self._delay, self._clock = status, body, seconds_per_chunk, clock
        self._headers = list((headers or {"Content-Type": "application/json"}).items())
        self._original = (status, body, headers, seconds_per_chunk, clock)

    def fresh(self):
        return FakeResponse(*self._original)

    def getheader(self, name):
        return next((v for k, v in self._headers if k.lower() == name.lower()), None)

    def getheaders(self):
        return self._headers

    def read(self, limit):
        if self._clock and self._body:
            self._clock.sleep(self._delay)
        chunk, self._body = self._body[:limit], self._body[limit:]
        return chunk


class FakeConnection:
    def __init__(self, reply):
        self.reply, self.closed = reply, False

    def request(self, method, path, headers):
        self.method, self.path, self.headers = method, path, headers
        if isinstance(self.reply, Exception):
            raise self.reply

    def getresponse(self):
        return self.reply

    def close(self):
        self.closed = True


class Network:
    """Per-host queues of replies (a FakeResponse or an exception). The last reply repeats."""

    def __init__(self, clock, routes, addresses=(PUBLIC_V4,)):
        self.clock, self.routes, self.addresses = clock, {h: list(r) for h, r in routes.items()}, addresses
        self.sent, self.connections = [], []

    def resolve(self, host):
        if isinstance(self.addresses, Exception):
            raise self.addresses
        return list(self.addresses)

    def connect(self, host, address, timeout):
        queue = self.routes[host]
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        connection = FakeConnection(reply.fresh() if isinstance(reply, FakeResponse) else reply)
        self.connections.append(connection)
        self.sent.append({"host": host, "address": address, "timeout": timeout, "connection": connection})
        return connection

    def hosts(self):
        return [s["host"] for s in self.sent]


def ok(body):
    return FakeResponse(200, body)


@pytest.fixture
def staging(monkeypatch):
    monkeypatch.setenv("RESEARCH_ENV", "staging")
    monkeypatch.setenv("RESEARCH_DISCOVERY_MODE", "staging")
    monkeypatch.setenv("RESEARCH_DISCOVERY_PROVIDERS", "openalex,crossref")
    monkeypatch.setenv("RESEARCH_DISCOVERY_CONTACT", CONTACT)
    monkeypatch.setenv("RESEARCH_OPENALEX_API_KEY_FILE", str(KEY_FILE))


@pytest.fixture
def wire(monkeypatch, staging):
    """Connect the route to a fake network through the real guarded and HTTPS transports."""
    def install(routes, *, addresses=(PUBLIC_V4,), policies=None, deadline=activation.RUN_DEADLINE_SECONDS):
        clock = Clock()
        net = Network(clock, routes, addresses)
        guard = live.ProviderGuard(policies or activation.POLICIES, clock=clock, day=lambda: clock.day)
        clock.day = "2026-11-12"

        monkeypatch.setattr(discovery, "_GUARD", guard)
        monkeypatch.setattr(discovery, "_LIMITER", da.RateLimiter(rate_per_second=activation.RATE_PER_SECOND,
                                                                  burst=activation.BURST, clock=clock, sleep=clock.sleep))
        monkeypatch.setattr(discovery, "live_transport", lambda state: live.GuardedTransport(
            guard, state.providers, live.https_factory(state.user_agent, openalex_key=state.openalex_key,
                                                       resolver=net.resolve, connector=net.connect, clock=clock),
            deadline_seconds=deadline, clock=clock))
        return net, clock, guard
    return install


def search(**body):
    return client.post(SEARCH, json={"query": QUERY, **body})


def outcome(data, provider):
    return next(o for o in data["run"]["outcomes"] if o["provider"] == provider)


# --- activation: disabled by default ------------------------------------------------------------------------

BASE = {"RESEARCH_ENV": "staging", "RESEARCH_DISCOVERY_MODE": "staging",
        "RESEARCH_DISCOVERY_PROVIDERS": "openalex,crossref", "RESEARCH_DISCOVERY_CONTACT": CONTACT,
        "RESEARCH_OPENALEX_API_KEY_FILE": str(KEY_FILE)}


@pytest.mark.parametrize("change, reason", [
    ({"RESEARCH_DISCOVERY_MODE": None}, "mode-disabled"),
    ({"RESEARCH_DISCOVERY_MODE": "disabled"}, "mode-disabled"),
    ({"RESEARCH_DISCOVERY_MODE": "live"}, "unknown-mode"),
    ({"RESEARCH_DISCOVERY_MODE": "production"}, "unknown-mode"),
    ({"RESEARCH_ENV": "production"}, "production-not-authorized"),
    ({"RESEARCH_ENV": "development"}, "environment-not-staging"),
    ({"RESEARCH_ENV": None}, "environment-not-staging"),
    ({"RESEARCH_DISCOVERY_PROVIDERS": ""}, "no-providers"),
    ({"RESEARCH_DISCOVERY_PROVIDERS": "openalex,arxiv"}, "unknown-provider"),
    ({"RESEARCH_DISCOVERY_CONTACT": None}, "contact-missing"),
    ({"RESEARCH_DISCOVERY_CONTACT": "not-an-address"}, "contact-invalid"),
    ({"RESEARCH_DISCOVERY_CONTACT": "ops@sustainablecatalyst.com\r\nX-Injected: 1"}, "contact-invalid"),
    ({"RESEARCH_DISCOVERY_CONTACT": "ops@example.org"}, "contact-reserved-domain"),
    ({"RESEARCH_DISCOVERY_CONTACT": "ops@mail.example.com"}, "contact-reserved-domain"),
    ({"RESEARCH_DISCOVERY_CONTACT": "ops@research.test"}, "contact-reserved-domain"),
    ({"RESEARCH_DISCOVERY_CONTACT": "ops@host.localhost"}, "contact-reserved-domain"),
    ({"RESEARCH_OPENALEX_API_KEY_FILE": None}, "openalex-key-missing"),
    ({"RESEARCH_OPENALEX_API_KEY_FILE": "/nonexistent/openalex-key"}, "openalex-key-unreadable"),
])
def test_activation_fails_closed(change, reason):
    env = {k: v for k, v in {**BASE, **change}.items() if v is not None}
    state = activation.resolve(env)
    assert (state.active, state.reason, state.providers, state.contact) == (False, reason, (), None)


def test_activation_defaults_to_disabled_and_production_is_a_code_constant():
    assert activation.resolve({}).reason == "mode-disabled"
    assert activation.PRODUCTION_DISCOVERY_AUTHORIZED is False
    state = activation.resolve({**BASE, "RESEARCH_DISCOVERY_PROVIDERS": " Crossref , openalex "})
    assert state.active and state.providers == ("openalex", "crossref") and state.contact == CONTACT
    assert state.openalex_key.reveal() == KEY
    assert CONTACT not in repr(state) and KEY not in repr(state) and KEY not in str(state.openalex_key)


def test_production_stays_refused_even_with_every_other_setting(monkeypatch, staging):
    monkeypatch.setenv("RESEARCH_ENV", "production")
    monkeypatch.setattr(discovery, "live_transport", lambda state: pytest.fail("live transport constructed"))
    response = search()
    assert response.status_code == 503
    assert response.json()["detail"] | {"reason": "production-not-authorized"} == response.json()["detail"]
    ready = client.get("/api/research/v1/discovery/readiness").json()
    assert ready["external_retrieval_enabled"] is False and ready["production_discovery_authorized"] is False


def test_rollback_is_switching_the_mode_off(monkeypatch, wire):
    net, _, _ = wire({"api.openalex.org": [ok(OPENALEX)], "api.crossref.org": [ok(CROSSREF)]})
    assert search().status_code == 200
    monkeypatch.setenv("RESEARCH_DISCOVERY_MODE", "disabled")
    sent = len(net.sent)
    response = search()
    assert response.status_code == 503 and response.json()["detail"]["reason"] == "mode-disabled"
    assert len(net.sent) == sent


def test_readiness_and_providers_report_staging_without_the_contact(wire):
    wire({"api.openalex.org": [ok(OPENALEX)], "api.crossref.org": [ok(CROSSREF)]})
    ready = client.get("/api/research/v1/discovery/readiness")
    data = ready.json()
    assert data["status"] == "active_staging" and data["mode"] == "staging" and data["stage"] == "RH-08D-03"
    assert data["external_retrieval_enabled"] is True and data["enabled_providers"] == ["openalex", "crossref"]
    for key in ("saved_searches_enabled", "library_intake_enabled", "publication_enabled",
                "research_evidence_promotion_enabled", "production_discovery_authorized"):
        assert data[key] is False, key
    providers = client.get("/api/research/v1/discovery/providers").json()
    assert providers["enabled_count"] == 2 and providers["status"] == "staging"
    assert {p["id"] for p in providers["providers"] if p["enabled"]} == {"openalex", "crossref"}
    assert CONTACT not in ready.text and CONTACT not in json.dumps(providers)


# --- a live search, end to end ----------------------------------------------------------------------------

def test_live_search_returns_unreviewed_metadata_with_provenance(wire):
    net, _, _ = wire({"api.openalex.org": [ok(OPENALEX)], "api.crossref.org": [ok(CROSSREF)]})
    response = search()
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    data = response.json()
    run = data["run"]
    assert run["transport"] == "https" and run["external_request_performed"] is True
    assert (data["saved"], data["publishable"], run["persisted"], run["publishable"]) == (False, False, False, False)
    assert data["separation"] == {"tier": "discovered-metadata", "library_reviewed": False, "research_evidence": False,
                                  "statement": discovery.SEPARATION["statement"]}
    assert len(run["candidates"]) == 9
    bodies = {"openalex": OPENALEX, "crossref": CROSSREF}
    for candidate in run["candidates"]:
        assert candidate["classification"] == "discovered-unreviewed" and candidate["review_status"] == "unreviewed"
        assert candidate["publishable"] is False
        for source in candidate["sources"]:
            assert source["response_sha256"] == hashlib.sha256(bodies[source["provider"]]).hexdigest()
            assert len(source["record_sha256"]) == 64 and source["query_sha256"] == run["query_sha256"]
            assert source["adapter_version"] == da.ADAPTER_VERSION and source["retrieved_at"]
    assert QUERY not in response.text  # the search text appears only as a hash


def test_requests_follow_the_provider_policy(wire):
    net, _, _ = wire({"api.openalex.org": [ok(OPENALEX)], "api.crossref.org": [ok(CROSSREF)]})
    search(publication_year=2024)
    assert net.hosts() == ["api.openalex.org", "api.crossref.org"]
    for sent in net.sent:
        policy = activation.POLICIES["openalex" if "openalex" in sent["host"] else "crossref"]
        c = sent["connection"]
        assert sent["address"] == PUBLIC_V4 and sent["timeout"] <= policy.timeout_seconds
        assert c.method == "GET" and c.path.startswith("/works?") and c.closed
        assert c.headers["User-Agent"] == activation.user_agent(CONTACT) and c.headers["Accept-Encoding"] == "identity"
        assert "mailto:" + CONTACT in c.headers["User-Agent"] and activation.PROJECT_URL in c.headers["User-Agent"]
        assert "mailto=" + CONTACT.replace("@", "%40") in c.path
        assert ("rows=25" if sent["host"] == "api.crossref.org" else "per-page=25") in c.path
        assert "Cookie" not in c.headers and KEY not in c.path
        if sent["host"] == "api.openalex.org":
            assert c.headers["Authorization"] == "Bearer " + KEY
        else:
            assert "Authorization" not in c.headers


def test_requests_are_spaced_by_the_rate_limiter(wire):
    _, clock, _ = wire({"api.openalex.org": [ok(OPENALEX)], "api.crossref.org": [ok(CROSSREF)]})
    start = clock.now
    first = search(provider="openalex").json()
    second = search(provider="openalex").json()
    assert clock.now - start >= 1 / activation.RATE_PER_SECOND
    assert first["run"]["candidates"] == second["run"]["candidates"]


def test_retractions_are_flagged_and_retraction_notices_are_not_candidates(wire):
    wire({"api.openalex.org": [ok(OPENALEX)], "api.crossref.org": [ok(CROSSREF)]})
    data = search().json()
    retracted = [c for c in data["run"]["candidates"] if c["retracted"] is True]
    assert sorted(c["doi"] for c in retracted) == ["10.5555/sc-syn-007", "10.5555/sc-syn-010"]
    assert outcome(data, "crossref")["rejection_reasons"]["retraction-notice"] == 1
    for c in retracted:
        assert "retracted" in data["library_intake"]["blocking_issues"][c["candidate_id"]]


def test_duplicates_merge_by_doi_and_otherwise_stay_separate_for_review(wire):
    wire({"api.openalex.org": [ok(OPENALEX)], "api.crossref.org": [ok(CROSSREF)]})
    candidates = {c["candidate_id"]: c for c in search().json()["run"]["candidates"]}
    both = next(c for c in candidates.values() if c["doi"] == "10.5555/sc-syn-002")
    assert [s["provider"] for s in both["sources"]] == ["openalex", "crossref"]
    assert [x["field"] for x in both["conflicts"]] == ["publication_year"]
    linked = [c for c in candidates.values() if c["possible_duplicate_of"]]
    assert len(linked) == 4
    for c in linked:
        for other in c["possible_duplicate_of"]:
            assert c["candidate_id"] in candidates[other]["possible_duplicate_of"]


def test_one_provider_can_be_enabled_alone(monkeypatch, wire):
    monkeypatch.setenv("RESEARCH_DISCOVERY_PROVIDERS", "openalex")
    net, _, _ = wire({"api.openalex.org": [ok(OPENALEX)], "api.crossref.org": [ok(CROSSREF)]})
    data = search().json()
    assert outcome(data, "crossref")["status"] == "disabled" and net.hosts() == ["api.openalex.org"]
    refused = search(provider="crossref")
    assert refused.status_code == 503 and refused.json()["detail"]["code"] == "DISCOVERY_PROVIDER_NOT_ENABLED"
    unavailable = search(provider="arxiv")
    assert unavailable.json()["detail"]["code"] == "DISCOVERY_PROVIDER_NOT_AVAILABLE"
    assert net.hosts() == ["api.openalex.org"]


# --- provider errors ---------------------------------------------------------------------------------------

def test_a_server_error_is_retried_once_then_succeeds(wire):
    net, _, _ = wire({"api.openalex.org": [FakeResponse(503, b"busy"), ok(OPENALEX)], "api.crossref.org": [ok(CROSSREF)]})
    data = search(provider="openalex").json()
    assert outcome(data, "openalex")["status"] == "ok" and outcome(data, "openalex")["attempts"] == 2
    assert net.hosts() == ["api.openalex.org", "api.openalex.org"]


def test_a_short_retry_after_is_honoured(wire):
    _, clock, _ = wire({"api.openalex.org": [FakeResponse(429, b"", {"Retry-After": "2"}), ok(OPENALEX)],
                        "api.crossref.org": [ok(CROSSREF)]})
    start = clock.now
    data = search(provider="openalex").json()
    assert outcome(data, "openalex")["status"] == "ok" and clock.now - start >= 2


def test_a_long_retry_after_stops_without_waiting(wire):
    net, clock, _ = wire({"api.openalex.org": [FakeResponse(429, b"", {"Retry-After": "120"})],
                          "api.crossref.org": [ok(CROSSREF)]})
    start = clock.now
    data = search().json()
    o = outcome(data, "openalex")
    assert (o["status"], o["code"], o["attempts"], o["retry_after_seconds"]) == ("rate_limited", "http-429", 1, 120.0)
    assert clock.now - start < 5
    assert outcome(data, "crossref")["status"] == "ok" and len(data["run"]["candidates"]) > 0


@pytest.mark.parametrize("reply, status, code", [
    (FakeResponse(400, b'{"status":"failed"}'), "failed", "http-400"),
    (FakeResponse(404, b""), "failed", "http-404"),
    (FakeResponse(301, b"", {"Location": "https://example.org/works"}), "failed", "http-301"),
    (FakeResponse(500, b"oops"), "unavailable", "http-500"),
])
def test_provider_http_errors_become_typed_outcomes(wire, reply, status, code):
    net, _, _ = wire({"api.openalex.org": [reply], "api.crossref.org": [ok(CROSSREF)]})
    data = search().json()
    o = outcome(data, "openalex")
    assert (o["status"], o["code"]) == (status, code)
    assert all(s["provider"] == "crossref" for c in data["run"]["candidates"] for s in c["sources"])
    assert all(s["connection"].closed for s in net.sent)


# --- malformed and oversized responses ------------------------------------------------------------------

@pytest.mark.parametrize("reply, status, code", [
    (FakeResponse(200, b"<html>not json</html>", {"Content-Type": "text/html"}), "invalid_response", "body-not-json"),
    (FakeResponse(200, b"\xff\xfe"), "invalid_response", "body-not-json"),
    (FakeResponse(200, json.dumps({"results": "x"}).encode()), "invalid_response", "unexpected-shape"),
    (FakeResponse(200, b"[]"), "invalid_response", "unexpected-shape"),
    (FakeResponse(200, b"\x1f\x8b", {"Content-Type": "application/json", "Content-Encoding": "gzip"}),
     "failed", "unexpected-content-encoding"),
    (FakeResponse(200, b" " * (activation.POLICIES["openalex"].max_bytes + 1)), "failed", "response-too-large"),
])
def test_malformed_responses_yield_no_candidates_from_that_provider(wire, reply, status, code):
    wire({"api.openalex.org": [reply], "api.crossref.org": [ok(CROSSREF)]})
    data = search().json()
    assert (outcome(data, "openalex")["status"], outcome(data, "openalex")["code"]) == (status, code)
    assert outcome(data, "crossref")["status"] == "ok"
    assert not any(s["provider"] == "openalex" for c in data["run"]["candidates"] for s in c["sources"])


def test_malformed_records_are_counted_not_rendered(wire):
    page = {"meta": {"count": 3}, "results": [None, {"id": "https://openalex.org/W1"}, {"id": "nope", "title": "x"}]}
    wire({"api.openalex.org": [ok(json.dumps(page).encode())], "api.crossref.org": [ok(CROSSREF)]})
    o = outcome(search(provider="openalex").json(), "openalex")
    assert (o["status"], o["records_received"], o["records_rejected"]) == ("ok", 3, 3)
    assert o["rejection_reasons"] == {"not-an-object": 1, "missing-title": 1, "missing-provider-id": 1}


def test_more_records_than_requested_are_cut_and_counted(wire):
    record = json.loads(OPENALEX)["results"][0]
    page = {"meta": {}, "results": [{**record, "id": f"https://openalex.org/W8{i:06d}", "doi": None,
                                     "title": f"SYNTHETIC record {i}"} for i in range(60)]}
    wire({"api.openalex.org": [ok(json.dumps(page).encode())], "api.crossref.org": [ok(CROSSREF)]})
    data = search(provider="openalex").json()
    o = outcome(data, "openalex")
    assert o["rejection_reasons"]["beyond-requested-rows"] == 60 - activation.ROWS
    assert len(data["run"]["candidates"]) == activation.ROWS


# --- network failures ------------------------------------------------------------------------------------

@pytest.mark.parametrize("reply, status, code", [
    (TimeoutError("synthetic"), "unavailable", "timeout"),
    (ConnectionResetError("synthetic"), "failed", "connection-failure"),
    (ssl.SSLCertVerificationError("synthetic"), "failed", "tls-failure"),
])
def test_network_failures_are_reported_and_the_other_provider_still_answers(wire, reply, status, code):
    wire({"api.openalex.org": [reply], "api.crossref.org": [ok(CROSSREF)]})
    data = search().json()
    o = outcome(data, "openalex")
    assert (o["status"], o["code"]) == (status, code)
    assert outcome(data, "crossref")["status"] == "ok" and data["run"]["candidates"]
    assert "synthetic" not in json.dumps(data)  # exception text never reaches the response


@pytest.mark.parametrize("addresses, code", [
    (OSError("synthetic dns failure"), "dns-failure"),
    (["10.0.0.7"], "non-public-address"),
    (["169.254.169.254"], "non-public-address"),
    ([PUBLIC_V4, "127.0.0.1"], "non-public-address"),
])
def test_dns_failures_and_private_answers_never_connect(wire, addresses, code):
    net, _, _ = wire({"api.openalex.org": [ok(OPENALEX)], "api.crossref.org": [ok(CROSSREF)]}, addresses=addresses)
    data = search().json()
    assert {o["code"] for o in data["run"]["outcomes"]} == {code} and net.sent == []


def test_a_slowly_trickled_body_times_out(wire):
    net, clock, _ = wire({"api.openalex.org": [ok(b"")], "api.crossref.org": [ok(CROSSREF)]})
    body = b" " * (https_transport.CHUNK * 40)  # 40 chunks at 1 s each, beyond the 15 s request limit
    net.routes["api.openalex.org"] = [FakeResponse(200, body, seconds_per_chunk=1.0, clock=clock)]
    o = outcome(search(provider="openalex").json(), "openalex")
    assert (o["status"], o["code"]) == ("unavailable", "timeout")


# --- circuit breaker, budget, deadline and concurrency ---------------------------------------------------

def test_repeated_failures_open_the_circuit_then_it_recovers(wire):
    net, clock, guard = wire({"api.openalex.org": [TimeoutError("x")], "api.crossref.org": [ok(CROSSREF)]})
    policy = activation.POLICIES["openalex"]
    search(provider="openalex")
    search(provider="openalex")  # 2 searches x 2 attempts = 4 failures >= 3
    sent = len(net.sent)
    data = search(provider="openalex").json()
    o = outcome(data, "openalex")
    assert (o["status"], o["code"], o["attempts"]) == ("unavailable", "circuit-open", 0) and len(net.sent) == sent
    assert client.get("/api/research/v1/discovery/readiness").json()["providers"]["openalex"]["circuit_open"] is True
    clock.now += policy.circuit_cooldown_seconds + 1
    net.routes["api.openalex.org"] = [ok(OPENALEX)]
    assert outcome(search(provider="openalex").json(), "openalex")["status"] == "ok"


def test_the_daily_budget_holds_requests_until_the_next_day(wire):
    small = {p: activation.ProviderPolicy(**{**vars(v), "daily_request_budget": 2}) for p, v in activation.POLICIES.items()}
    net, clock, _ = wire({"api.openalex.org": [ok(OPENALEX)], "api.crossref.org": [ok(CROSSREF)]}, policies=small)
    search(provider="openalex")
    search(provider="openalex")
    data = search(provider="openalex").json()
    o = outcome(data, "openalex")
    assert (o["status"], o["code"], o["attempts"]) == ("rate_limited", "daily-budget-exhausted", 0)
    assert net.hosts().count("api.openalex.org") == 2
    clock.day = "2026-11-13"
    assert outcome(search(provider="openalex").json(), "openalex")["status"] == "ok"


def test_the_run_deadline_stops_further_requests(wire):
    net, clock, _ = wire({"api.openalex.org": [ok(OPENALEX)], "api.crossref.org": [ok(CROSSREF)]}, deadline=10.0)
    net.routes["api.openalex.org"] = [FakeResponse(200, OPENALEX, seconds_per_chunk=9.5, clock=clock)]  # one chunk
    data = search().json()
    assert outcome(data, "openalex")["status"] == "ok"
    c = outcome(data, "crossref")
    assert (c["status"], c["code"], c["attempts"]) == ("unavailable", "run-deadline", 0)
    assert net.hosts() == ["api.openalex.org"]


def test_request_timeouts_never_exceed_the_remaining_run_time(wire):
    net, clock, _ = wire({"api.openalex.org": [ok(OPENALEX)], "api.crossref.org": [ok(CROSSREF)]}, deadline=5.0)
    search(provider="openalex")
    assert net.sent[0]["timeout"] <= 5.0


def test_concurrent_searches_are_bounded(wire):
    net, _, _ = wire({"api.openalex.org": [ok(OPENALEX)], "api.crossref.org": [ok(CROSSREF)]})
    held = [discovery._SLOTS.acquire(blocking=False) for _ in range(activation.MAX_CONCURRENT_SEARCHES)]
    try:
        response = search()
    finally:
        for acquired in held:
            if acquired:
                discovery._SLOTS.release()
    assert response.status_code == 429 and response.headers["retry-after"] == "5"
    assert response.json()["detail"]["code"] == "DISCOVERY_BUSY" and net.sent == []
    assert search().status_code == 200


def test_the_search_text_is_never_logged(wire, caplog):
    wire({"api.openalex.org": [ok(OPENALEX)], "api.crossref.org": [ok(CROSSREF)]})
    with caplog.at_level(logging.DEBUG):
        search()
    assert QUERY not in caplog.text and CONTACT not in caplog.text and KEY not in caplog.text


# --- separation: discovered metadata, Library-reviewed sources, Research evidence -------------------------

DISCOVERY_FILES = [ROOT / "research-api/app/discovery.py",
                   *sorted((ROOT / "research-api/app/discovery_adapters").glob("*.py"))]
REVIEWED_MODULES = {"editorial", "source_adapters", "source_rights_sync", "workspace_ingest", "workspaces",
                    "convergence", "object_registry", "figure_objects", "figure_releases", "public_figures",
                    "graph_analysis", "metadata_diagnostics", "main"}


def imported_modules(path):
    names = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom):
            names.add((node.module or "").split(".")[-1] if node.level else (node.module or ""))
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            names.update(alias.name.split(".")[-1] for alias in node.names)
    return names


def test_discovery_code_never_reaches_library_review_or_research_evidence():
    for path in DISCOVERY_FILES:
        assert not imported_modules(path) & REVIEWED_MODULES, path.name
    text = "\n".join(p.read_text() for p in DISCOVERY_FILES)
    for forbidden in ("sqlalchemy", "INSERT ", "UPDATE ", "open(", "write_text", "write_bytes"):
        assert forbidden not in text, forbidden


def test_reviewed_and_evidence_modules_never_read_discovery_results():
    for name in REVIEWED_MODULES - {"main"}:
        path = ROOT / f"research-api/app/{name}.py"
        if path.exists():
            assert not imported_modules(path) & {"discovery", "discovery_adapters", "research_discovery"}, name


def test_a_candidate_cannot_be_marked_reviewed_evidence_or_publishable():
    data = json.loads((ROOT / "tests/fixtures/rh08d02/discovery-search-synthetic.json").read_text())
    candidate = data["run"]["candidates"][0]
    for change in ({"classification": "library-reviewed"}, {"review_status": "reviewed"}, {"publishable": True},
                   {"evidence_status": "approved"}):
        with pytest.raises(Exception):
            da.Candidate(**{**candidate, **change})
    assert data["separation"]["library_reviewed"] is False and data["separation"]["research_evidence"] is False


# --- staging acceptance runner ---------------------------------------------------------------------------

sys.path.insert(0, str(ROOT / "scripts"))
import discovery_staging_acceptance as acceptance  # noqa: E402


def through_app(method, url, body=None):
    path = url.replace("http://127.0.0.1:8000", "")
    response = client.get(path) if method == "GET" else client.post(path, json=body)
    return response.status_code, {k.lower(): v for k, v in response.headers.items()}, response.json()


@pytest.mark.parametrize("url, allowed", [
    ("http://127.0.0.1:8000/api/research/v1", True),
    ("http://localhost:8000/api/research/v1", True),
    ("https://research.staging.example.org/api/research/v1", True),
    ("https://staging-research.example.org/api/research/v1", True),
    ("https://research.example.org/api/research/v1", False),
    ("http://research.staging.example.org/api/research/v1", False),
    ("https://example.staging/api", False),
    ("https://u:p@research.staging.example.org/api", False),
    ("https://research.staging.example.org/api?x=1", False),
])
def test_acceptance_runs_only_against_loopback_or_staging(url, allowed):
    assert acceptance.allowed_base(url) is allowed


def test_acceptance_passes_against_a_correct_staging_deployment(wire):
    wire({"api.openalex.org": [ok(OPENALEX)], "api.crossref.org": [ok(CROSSREF)]})
    waits = []
    report = acceptance.run_acceptance("http://127.0.0.1:8000/api/research/v1", fetch=through_app, sleep=waits.append)
    assert report["passed"] is True, report["checks"]
    assert report["stage"] == "RH-08D-03" and waits == [acceptance.SPACING_SECONDS]
    assert [s["candidates"] for s in report["searches"]] == [9, 9]
    assert report["searches"][0]["retracted"] == 2 and report["searches"][0]["multi_source"] == 1
    text = json.dumps(report)
    assert CONTACT not in text and "SYNTHETIC" not in text and acceptance.QUERIES[0] not in text


def test_acceptance_fails_when_discovery_is_off(monkeypatch):
    monkeypatch.delenv("RESEARCH_DISCOVERY_MODE", raising=False)
    report = acceptance.run_acceptance("http://127.0.0.1:8000/api/research/v1", fetch=through_app, sleep=lambda s: None)
    assert report["passed"] is False
    assert report["checks"][0]["problems"] == ["discovery is not active in staging", "no contact address configured"]


def test_acceptance_catches_a_response_that_breaks_the_separation():
    data = json.loads((ROOT / "tests/fixtures/rh08d02/discovery-search-synthetic.json").read_text())
    data["run"]["transport"] = "https"
    data["run"]["external_request_performed"] = True
    assert acceptance.check_search(200, {"cache-control": "no-store"}, data, QUERY) == []
    assert acceptance.check_search(200, {"cache-control": "no-store"}, data, "other words") == [
        "query hash does not match the search"]
    bad = json.loads(json.dumps(data))
    bad["query"] = QUERY.upper()
    bad["run"]["candidates"][0]["review_status"] = "reviewed"
    bad["separation"]["research_evidence"] = True
    bad["run"]["candidates"][1]["sources"][0]["record_sha256"] = "x"
    problems = acceptance.check_search(200, {}, bad, QUERY)
    for expected in ("missing Cache-Control: no-store", "search text echoed in the response",
                     "separation block missing or wrong", "not discovered-unreviewed", "provenance hash missing"):
        assert any(expected in p for p in problems), expected


# --- OpenAlex API key and provider identification -------------------------------------------------------

@pytest.mark.parametrize("content", ["", "short", "has spaces in it 0123456789", "x" * 129, "key;with\nnewline-0123456789"])
def test_malformed_keys_disable_discovery(tmp_path, content):
    path = tmp_path / "key"
    path.write_text(content)
    state = activation.resolve({**BASE, "RESEARCH_OPENALEX_API_KEY_FILE": str(path)})
    assert (state.active, state.reason, state.openalex_key) == (False, "openalex-key-invalid", None)


def test_the_key_is_read_only_from_a_file_not_an_environment_value():
    env = {k: v for k, v in BASE.items() if k != "RESEARCH_OPENALEX_API_KEY_FILE"}
    for name in ("RESEARCH_OPENALEX_API_KEY", "OPENALEX_API_KEY"):
        assert activation.resolve({**env, name: KEY}).reason == "openalex-key-missing"
    source = (ROOT / "research-api/app/discovery_adapters/activation.py").read_text()
    assert source.count("env.get(OPENALEX_KEY_FILE_ENV") == 1 and "OPENALEX_API_KEY\"" not in source


def test_crossref_alone_needs_no_openalex_key():
    env = {k: v for k, v in BASE.items() if k != "RESEARCH_OPENALEX_API_KEY_FILE"}
    state = activation.resolve({**env, "RESEARCH_DISCOVERY_PROVIDERS": "crossref"})
    assert state.active and state.providers == ("crossref",) and state.openalex_key is None


def test_the_key_never_reaches_responses_provenance_or_urls(wire):
    net, _, _ = wire({"api.openalex.org": [ok(OPENALEX)], "api.crossref.org": [ok(CROSSREF)]})
    response = search()
    ready = client.get("/api/research/v1/discovery/readiness")
    assert response.status_code == 200 and ready.json()["openalex_api_key_configured"] is True
    assert ready.json()["contact_configured"] is True
    for text in (response.text, ready.text, client.get("/api/research/v1/discovery/providers").text):
        assert KEY not in text and CONTACT not in text
    request = da.openalex.build_request(QUERY, rows=25, contact=CONTACT)
    assert KEY not in request.key()  # the provenance request hash is over this, so it never covers the key
    assert all(KEY not in s["connection"].path for s in net.sent)


def test_a_bearer_token_is_bound_to_one_provider_host():
    secret = activation.Secret(KEY)
    for kwargs in ({"bearer": secret}, {"bearer_provider": "openalex"}, {"bearer": secret, "bearer_provider": "evil"}):
        with pytest.raises(ValueError):
            HttpsTransport(user_agent="x", **kwargs)
    net = Network(Clock(), {"api.crossref.org": [ok(CROSSREF)]})
    t = HttpsTransport(user_agent="x", bearer=secret, bearer_provider="openalex", resolver=net.resolve,
                       connector=net.connect)
    t.send(da.crossref.build_request(QUERY))
    assert "Authorization" not in net.sent[0]["connection"].headers


def test_transport_errors_never_carry_the_key(wire):
    wire({"api.openalex.org": [ssl.SSLError("bad record " + KEY)], "api.crossref.org": [ok(CROSSREF)]})
    data = search().json()
    assert outcome(data, "openalex")["code"] == "tls-failure" and KEY not in json.dumps(data)


def test_a_provider_signalling_its_budget_is_spent_is_held_until_reset(wire):
    spent = FakeResponse(200, OPENALEX, {"Content-Type": "application/json", "X-RateLimit-Remaining": "0",
                                         "X-RateLimit-Reset": "3600", "X-RateLimit-Credits-Used": "0.001"})
    net, clock, _ = wire({"api.openalex.org": [spent], "api.crossref.org": [ok(CROSSREF)]})
    assert outcome(search(provider="openalex").json(), "openalex")["status"] == "ok"
    o = outcome(search(provider="openalex").json(), "openalex")
    assert (o["status"], o["code"], o["attempts"]) == ("rate_limited", "provider-budget-exhausted", 0)
    assert 3590 <= o["retry_after_seconds"] <= 3600  # less the rate-limiter spacing already waited
    assert net.hosts().count("api.openalex.org") == 1
    clock.now += 3601
    net.routes["api.openalex.org"] = [ok(OPENALEX)]
    assert outcome(search(provider="openalex").json(), "openalex")["status"] == "ok"


def test_conservative_limits_sit_below_the_reviewed_provider_policies():
    # Reviewed 2026-11-12: OpenAlex free key 30 req/s and $1/day at $0.001 per search (about 1,000 searches);
    # Crossref polite pool 3 list req/s with concurrency 3. See docs/RH-08D-03-LIVE-DISCOVERY.md.
    assert activation.RATE_PER_SECOND <= 1.0 and activation.MAX_CONCURRENT_SEARCHES <= 2
    assert activation.POLICIES["openalex"].daily_request_budget * 0.001 < 1.0
    assert activation.MAX_ATTEMPTS <= 2


# --- provider preflight (read-only connectivity) ---------------------------------------------------------

import discovery_provider_preflight as preflight  # noqa: E402


class FakeTLSSocket:
    def __init__(self, host):
        self.host = host

    def getpeercert(self):
        return {"subjectAltName": (("DNS", self.host),), "issuer": ((("organizationName", "Synthetic CA"),),),
                "notAfter": "Jan  1 00:00:00 2100 GMT"}

    def version(self):
        return "TLSv1.3"

    def cipher(self):
        return ("TLS_AES_256_GCM_SHA384", "TLSv1.3", 256)


class PreflightConnection(FakeConnection):
    def __init__(self, host, reply):
        super().__init__(reply)
        self.sock = FakeTLSSocket(host)


def test_preflight_sends_one_bare_root_request_per_provider():
    seen = []

    def connector(host, address):
        connection = PreflightConnection(host, FakeResponse(200, b'{"meta":{}}', {"Content-Type": "application/json",
                                                                                    "X-RateLimit-Limit": "1"}))
        seen.append((host, address, connection))
        return connection

    results = [preflight.check(p, h, resolver=lambda h: [PUBLIC_V4], connector=connector)
               for p, h in preflight.HOSTS.items()]
    assert all(r["ok"] for r in results)
    assert results[0]["tls"]["version"] == "TLSv1.3" and results[0]["tls"]["san_matches_host"] is True
    assert results[0]["http"]["headers"] == {"content-type": "application/json", "x-ratelimit-limit": "1"}
    for host, address, c in seen:
        assert (c.method, c.path) == ("GET", "/") and address == PUBLIC_V4
        assert "Authorization" not in c.headers and "mailto" not in c.headers["User-Agent"] and c.closed


@pytest.mark.parametrize("addresses, error", [(["10.1.2.3"], "non-public-address"), ([], "non-public-address")])
def test_preflight_never_connects_to_non_public_answers(addresses, error):
    result = preflight.check("openalex", "api.openalex.org", resolver=lambda h: addresses,
                             connector=lambda h, a: pytest.fail("connected"))
    assert (result["ok"], result["error"]) == (False, error)


def test_preflight_reports_failures_without_raising():
    def broken(host):
        raise OSError("synthetic")
    assert preflight.check("crossref", "api.crossref.org", resolver=broken)["error"] == "dns-failure: OSError"
    result = preflight.check("crossref", "api.crossref.org", resolver=lambda h: [PUBLIC_V4],
                             connector=lambda h, a: PreflightConnection(h, ConnectionResetError("x")))
    assert result["ok"] is False and result["error"] == "connection-failure: ConnectionResetError"
