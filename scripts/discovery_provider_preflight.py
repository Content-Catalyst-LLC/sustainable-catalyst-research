"""RH-08D-03 provider preflight: read-only DNS, TLS and HTTPS reachability of OpenAlex and Crossref.

Run on the staging host (and inside the staging API container) before any activation. For each provider it
resolves the fixed API host, checks every address is public, opens one TLS connection to a vetted address
with certificate and host-name verification, and sends one ``GET /`` (the API root: no search, no query, no
API key, no contact address). It prints a JSON report: addresses, TLS version and certificate expiry, HTTP
status, content type, rate-limit headers and timings. It never reads application configuration or secrets
and never enables anything. Standard library only, Python 3.9+.

    python3 scripts/discovery_provider_preflight.py > preflight.json
"""
import http.client
import ipaddress
import json
import socket
import ssl
import sys
import time
from datetime import datetime, timezone

HOSTS = {"openalex": "api.openalex.org", "crossref": "api.crossref.org"}
USER_AGENT = "SustainableCatalystResearch-preflight/0.3 (connectivity check; no search)"
REPORTED_HEADERS = ("content-type", "x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset",
                    "x-rate-limit-limit", "x-rate-limit-interval", "x-rate-limit-type", "x-concurrency-limit",
                    "x-api-pool", "retry-after", "server")
TIMEOUT = 10.0
MAX_BYTES = 65_536


def resolve(host):
    return sorted({info[4][0] for info in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})


def tls_context():
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


class _Pinned(http.client.HTTPSConnection):
    def __init__(self, host, address):
        super().__init__(host, 443, timeout=TIMEOUT, context=tls_context())
        self._address = address

    def connect(self):
        sock = socket.create_connection((self._address, 443), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def connect(host, address):
    return _Pinned(host, address)


def check(provider, host, resolver=resolve, connector=connect, clock=time.monotonic):
    result = {"provider": provider, "host": host, "ok": False}
    started = clock()
    try:
        addresses = resolver(host)
    except OSError as error:
        result["error"] = "dns-failure: " + type(error).__name__
        return result
    result["addresses"] = addresses
    result["dns_ms"] = round((clock() - started) * 1000)
    if not addresses or any(not ipaddress.ip_address(a).is_global or ipaddress.ip_address(a).is_multicast
                            for a in addresses):
        result["error"] = "non-public-address"
        return result
    address = next((a for a in addresses if ":" not in a), addresses[0])
    result["connected_address"] = address
    connection = connector(host, address)
    try:
        started = clock()
        connection.request("GET", "/", headers={"Host": host, "User-Agent": USER_AGENT, "Accept": "application/json",
                                                "Accept-Encoding": "identity", "Connection": "close"})
        # Read the TLS details now: http.client closes the socket once a "Connection: close" response arrives.
        sock = connection.sock
        cert = sock.getpeercert() or {}
        names = [v for k, v in cert.get("subjectAltName", ()) if k == "DNS"]
        issuer = dict(x[0] for x in cert.get("issuer", ()))
        result["tls"] = {"version": sock.version(), "cipher": (sock.cipher() or [None])[0],
                         "verified_for_host": True, "san_matches_host": any(_matches(n, host) for n in names),
                         "issuer_organization": issuer.get("organizationName"),
                         "not_after": cert.get("notAfter"), "days_until_expiry": _days_left(cert.get("notAfter"))}
        response = connection.getresponse()
        body = response.read(MAX_BYTES)
        result["https_ms"] = round((clock() - started) * 1000)
        headers = {k.lower(): v for k, v in response.getheaders()}
        result["http"] = {"status": response.status, "bytes_read": len(body),
                          "headers": {k: headers[k] for k in REPORTED_HEADERS if k in headers}}
        result["ok"] = response.status < 500 and result["tls"]["san_matches_host"]
    except ssl.SSLCertVerificationError as error:
        result["error"] = "tls-verification-failed: " + str(error.verify_message)
    except ssl.SSLError as error:
        result["error"] = "tls-failure: " + type(error).__name__
    except (TimeoutError, socket.timeout):
        result["error"] = "timeout"
    except (OSError, http.client.HTTPException) as error:
        result["error"] = "connection-failure: " + type(error).__name__
    finally:
        connection.close()
    return result


def _matches(pattern, host):
    if pattern.startswith("*."):
        return host.count(".") == pattern.count(".") and host.endswith(pattern[1:])
    return pattern.lower() == host.lower()


def _days_left(not_after):
    if not not_after:
        return None
    expires = datetime.fromtimestamp(ssl.cert_time_to_seconds(not_after), timezone.utc)
    return (expires - datetime.now(timezone.utc)).days


def main():
    report = {"schema": "sc-research-discovery-provider-preflight/1.0",
              "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "python": sys.version.split()[0], "openssl": ssl.OPENSSL_VERSION,
              "results": [check(p, h) for p, h in HOSTS.items()]}
    report["ok"] = all(r["ok"] for r in report["results"])
    print(json.dumps(report, indent=1))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
