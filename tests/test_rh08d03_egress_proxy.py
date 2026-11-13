"""RH-08D-03: the discovery egress proxy and the transport's proxy mode.

The proxy tests drive the CONNECT handler with in-memory streams and a fake resolver/upstream, except one
test that runs the real asyncio server on 127.0.0.1 against a local echo upstream (loopback only). No test
reaches a provider.
"""
import asyncio
import json
import logging
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research-api"))
sys.path.insert(0, str(ROOT / "tests"))

from app import discovery  # noqa: E402
from app import discovery_egress_proxy as egress  # noqa: E402
from app.discovery_adapters import activation, https_transport, live  # noqa: E402
from app.discovery_adapters.https_transport import HttpsTransport  # noqa: E402
import test_rh08d03_live_discovery as base  # noqa: E402  (fakes and fixtures for the live path)

staging, wire = base.staging, base.wire  # re-export the live-path fixtures
PUBLIC = "1.1.1.1"
LOOPBACK = {"127.0.0.1", "localhost"}


@pytest.fixture(autouse=True)
def loopback_only(monkeypatch):
    """Every connection or lookup must stay on 127.0.0.1; anything else fails the test."""
    real_connect, real_create, real_getaddrinfo = socket.socket.connect, socket.create_connection, socket.getaddrinfo

    def connect(sock, address):
        assert address[0] in LOOPBACK, address
        return real_connect(sock, address)

    def create_connection(address, *args, **kwargs):
        assert address[0] in LOOPBACK, address
        return real_create(address, *args, **kwargs)

    def getaddrinfo(host, *args, **kwargs):
        assert host in LOOPBACK or host is None, host
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "create_connection", create_connection)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


class Writer:
    def __init__(self):
        self.data, self.closed = bytearray(), False

    def write(self, chunk):
        self.data += chunk

    async def drain(self):
        pass

    def close(self):
        self.closed = True


def reader(data=b"", eof=True):
    r = asyncio.StreamReader(limit=egress.MAX_HEADER_BYTES)
    r.feed_data(data)
    if eof:
        r.feed_eof()
    return r


def run(make):
    """Runs ``make()`` inside a fresh event loop, so stream readers are created on that loop."""
    async def main():
        return await (make() if callable(make) else make)
    return asyncio.run(main())


def proxy(addresses=(PUBLIC,), upstream=b"server-hello", calls=None, **kwargs):
    calls = calls if calls is not None else {"resolve": [], "open": []}

    async def resolve(host):
        calls["resolve"].append(host)
        if isinstance(addresses, Exception):
            raise addresses
        return list(addresses)

    async def open_upstream(address):
        calls["open"].append(address)
        if isinstance(upstream, Exception):
            raise upstream
        up_writer = Writer()
        calls["upstream_writer"] = up_writer
        return reader(upstream), up_writer

    return egress.EgressProxy(resolve=resolve, open_upstream=open_upstream, **kwargs), calls


def connect(host="api.openalex.org", port=443, rest=b"client-hello"):
    return f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode() + rest


# --- the proxy ---------------------------------------------------------------------------------------------

def test_the_proxy_allows_exactly_the_transport_hosts():
    assert egress.ALLOWED == frozenset(https_transport.ALLOWED_HOSTS.values()) and egress.PORT == 443


@pytest.mark.parametrize("host", ["api.openalex.org", "API.CROSSREF.ORG"])
def test_an_allowed_connect_is_tunnelled_to_a_vetted_address(host):
    p, calls = proxy()
    w = Writer()
    outcome = run(lambda: p.handle(reader(connect(host)), w))
    assert bytes(w.data).startswith(b"HTTP/1.1 200 Connection Established\r\n\r\n")
    assert bytes(w.data).endswith(b"server-hello") and bytes(calls["upstream_writer"].data) == b"client-hello"
    assert outcome == "closed" and calls["open"] == [PUBLIC] and w.closed and p.active == 0


@pytest.mark.parametrize("request_bytes, status, outcome", [
    (connect("example.org"), b"403", "destination-not-allowed"),
    (connect("api.openalex.org.evil.test"), b"403", "destination-not-allowed"),
    (connect("api.openalex.org", 80), b"403", "destination-not-allowed"),
    (connect("api.openalex.org", 8443), b"403", "destination-not-allowed"),
    (connect("169.254.169.254"), b"403", "destination-not-allowed"),
    (b"GET http://api.openalex.org/works HTTP/1.1\r\nHost: api.openalex.org\r\n\r\n", b"405", "not-connect"),
    (b"CONNECT api.openalex.org:443\r\n\r\n", b"405", "not-connect"),
    (b"\x16\x03\x01garbage-without-header-end", b"400", "bad-request"),
])
def test_everything_else_is_refused_before_dns(request_bytes, status, outcome):
    p, calls = proxy()
    w = Writer()
    assert run(lambda: p.handle(reader(request_bytes), w)) == outcome
    assert bytes(w.data).startswith(b"HTTP/1.1 " + status) and calls["resolve"] == [] and calls["open"] == []


def test_oversized_headers_are_refused():
    p, calls = proxy()
    w = Writer()
    big = b"CONNECT api.openalex.org:443 HTTP/1.1\r\nX: " + b"a" * egress.MAX_HEADER_BYTES + b"\r\n\r\n"
    assert run(lambda: p.handle(reader(big), w)) == "bad-request" and calls["resolve"] == []


@pytest.mark.parametrize("addresses", [["10.0.0.5"], ["127.0.0.1"], ["169.254.169.254"], ["172.17.0.1"],
                                       ["100.64.0.1"], ["::1"], ["fd00::1"], [PUBLIC, "192.168.1.1"], []])
def test_non_public_answers_are_never_connected(addresses):
    p, calls = proxy(addresses=addresses)
    w = Writer()
    assert run(lambda: p.handle(reader(connect()), w)) == "non-public-address"
    assert bytes(w.data).startswith(b"HTTP/1.1 403") and calls["open"] == []


def test_dns_and_connect_failures_are_502():
    p, _ = proxy(addresses=OSError("synthetic"))
    w = Writer()
    assert run(lambda: p.handle(reader(connect()), w)) == "dns-failure" and bytes(w.data).startswith(b"HTTP/1.1 502")
    p, _ = proxy(upstream=ConnectionRefusedError("synthetic"))
    w = Writer()
    assert run(lambda: p.handle(reader(connect()), w)) == "connect-failure" and bytes(w.data).startswith(b"HTTP/1.1 502")


def test_concurrent_tunnels_are_capped():
    p, calls = proxy(max_tunnels=1)

    async def scenario():
        quiet = asyncio.StreamReader()  # the upstream stays open, so the first tunnel keeps its slot

        async def open_upstream(address):
            return quiet, Writer()
        p._open = open_upstream
        hold = reader(connect(rest=b""), eof=False)
        first = asyncio.ensure_future(p.handle(hold, Writer()))
        await asyncio.sleep(0.05)
        w = Writer()
        second = await p.handle(reader(connect()), w)
        hold.feed_eof()
        quiet.feed_eof()
        await first
        return second, w

    outcome, w = run(scenario())
    assert outcome == "busy" and bytes(w.data).startswith(b"HTTP/1.1 503")


def test_idle_tunnels_time_out(monkeypatch):
    monkeypatch.setattr(egress, "IDLE_TIMEOUT", 0.05)
    p, _ = proxy(upstream=b"")

    async def scenario():
        up = asyncio.StreamReader()  # the upstream never answers or closes

        async def open_upstream(address):
            return up, Writer()
        p._open = open_upstream
        return await p.handle(reader(connect(rest=b""), eof=False), Writer())

    assert run(scenario()) == "timeout"


def test_the_byte_cap_ends_a_tunnel(monkeypatch):
    monkeypatch.setattr(egress, "TUNNEL_BYTES", 10)
    p, _ = proxy(upstream=b"x" * 64)
    assert run(lambda: p.handle(reader(connect(rest=b"")), Writer())) == "byte-cap"


def test_logs_name_only_the_destination(caplog):
    p, _ = proxy()
    with caplog.at_level(logging.INFO, logger="discovery-egress"):
        run(lambda: p.handle(reader(connect(rest=b"GET /works?search=secret+topic&api_key=k")), Writer()))
    assert "host=api.openalex.org outcome=closed" in caplog.text
    assert "secret" not in caplog.text and "api_key" not in caplog.text and PUBLIC not in caplog.text


def test_the_real_server_relays_on_loopback():
    async def scenario():
        async def echo(r, w):
            w.write(b"echo:" + await r.read(5))
            await w.drain()
            w.close()
        upstream = await asyncio.start_server(echo, "127.0.0.1", 0)
        up_port = upstream.sockets[0].getsockname()[1]

        async def resolve(host):
            return [PUBLIC]

        async def open_upstream(address):  # the vetted public address is redirected to the local echo
            assert address == PUBLIC
            return await asyncio.open_connection("127.0.0.1", up_port)

        p = egress.EgressProxy(resolve=resolve, open_upstream=open_upstream)
        server = await asyncio.start_server(p.handle, "127.0.0.1", 0, limit=egress.MAX_HEADER_BYTES)
        port = server.sockets[0].getsockname()[1]
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(connect("api.crossref.org", rest=b"hello"))
        await w.drain()
        data = await asyncio.wait_for(r.read(), 5)
        w.close()
        for s in (server, upstream):
            s.close()
            await s.wait_closed()
        return data

    assert run(scenario()) == b"HTTP/1.1 200 Connection Established\r\n\r\necho:hello"


# --- activation and transport in proxy mode ---------------------------------------------------------------

@pytest.mark.parametrize("value, proxy, reason", [
    ("http://discovery-egress:3128", ("discovery-egress", 3128), "active"),
    ("", None, "active"),
    ("https://discovery-egress:3128", None, "egress-proxy-invalid"),
    ("http://proxy.example.net:3128", None, "egress-proxy-invalid"),
    ("http://u:p@discovery-egress:3128", None, "egress-proxy-invalid"),
    ("http://discovery-egress:99999", None, "egress-proxy-invalid"),
    ("http://10.0.0.1:3128", None, "egress-proxy-invalid"),
    ("http://discovery-egress", None, "egress-proxy-invalid"),
])
def test_the_egress_proxy_setting_is_an_internal_service_name(value, proxy, reason):
    state = activation.resolve({**base.BASE, "RESEARCH_DISCOVERY_EGRESS_PROXY": value})
    assert (state.reason, state.egress_proxy) == (reason, proxy)


class TunnelConnection(base.FakeConnection):
    pass


def test_proxy_mode_tunnels_without_local_dns_and_keeps_the_key_bound():
    seen = []

    def proxy_connector(host, proxy, timeout):
        connection = TunnelConnection(base.ok(base.OPENALEX).fresh())
        seen.append((host, proxy, timeout, connection))
        return connection

    t = HttpsTransport(user_agent="ua", bearer=activation.Secret(base.KEY), bearer_provider="openalex",
                       proxy=("discovery-egress", 3128), proxy_connector=proxy_connector,
                       resolver=lambda h: pytest.fail("resolved locally"), connector=lambda *a: pytest.fail("direct"))
    from app.discovery_adapters import crossref, openalex
    assert t.send(openalex.build_request(base.QUERY)).status == 200
    t.send(crossref.build_request(base.QUERY))
    (h1, p1, _, c1), (h2, _, _, c2) = seen
    assert (h1, p1, h2) == ("api.openalex.org", ("discovery-egress", 3128), "api.crossref.org")
    assert c1.headers["Authorization"] == "Bearer " + base.KEY and "Authorization" not in c2.headers
    assert c1.path.startswith("/works?") and c1.closed and c2.closed


def test_a_refused_tunnel_is_reported_as_egress_refused(monkeypatch, wire):
    net, clock, guard = wire({"api.openalex.org": [base.ok(base.OPENALEX)], "api.crossref.org": [base.ok(base.CROSSREF)]})
    monkeypatch.setenv("RESEARCH_DISCOVERY_EGRESS_PROXY", "http://discovery-egress:3128")

    def refused(host, proxy, timeout):
        return base.FakeConnection(OSError("Tunnel connection failed: 403 Forbidden"))

    monkeypatch.setattr(discovery, "live_transport", lambda state: live.GuardedTransport(
        guard, state.providers, live.https_factory(state.user_agent, openalex_key=state.openalex_key,
                                                   proxy=state.egress_proxy, proxy_connector=refused,
                                                   resolver=lambda h: pytest.fail("resolved locally"), clock=clock),
        clock=clock))
    data = base.search().json()
    assert {(o["status"], o["code"]) for o in data["run"]["outcomes"]} == {("failed", "egress-refused")}
    ready = base.client.get("/api/research/v1/discovery/readiness").json()
    assert ready["egress"] == "proxy" and "discovery-egress" not in json.dumps(ready)


def test_the_real_tunnel_connection_targets_the_provider_host():
    connection = https_transport._connect_via_proxy("api.crossref.org", ("discovery-egress", 3128), 5.0)
    assert (connection.host, connection.port) == ("discovery-egress", 3128)
    assert (connection._tunnel_host, connection._tunnel_port) == ("api.crossref.org", 443)
    assert connection._context.verify_mode.name == "CERT_REQUIRED" and connection._context.check_hostname


def test_the_preflight_can_check_through_the_proxy():
    import discovery_provider_preflight as preflight
    seen = []

    def proxy_connector(host, proxy):
        seen.append((host, proxy))
        return base.PreflightConnection(host, base.FakeResponse(200, b"{}"))

    result = preflight.check("openalex", "api.openalex.org", resolver=lambda h: pytest.fail("resolved locally"),
                             proxy=("discovery-egress", 3128), proxy_connector=proxy_connector)
    assert result["ok"] and result["via_proxy"] == "discovery-egress:3128" and "addresses" not in result
    assert seen == [("api.openalex.org", ("discovery-egress", 3128))]
    refused = preflight.check("crossref", "api.crossref.org", proxy=("discovery-egress", 3128),
                              proxy_connector=lambda h, p: base.PreflightConnection(
                                  h, OSError("Tunnel connection failed: 403 Forbidden")))
    assert refused["error"].startswith("egress-refused")
    assert preflight.main(["--bogus"]) == 2
