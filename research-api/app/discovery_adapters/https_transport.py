"""RH-08D-02/03: the HTTPS transport for OpenAlex and Crossref.

Nothing in this package constructs it. ``live.GuardedTransport`` builds one per provider, and only when
``activation`` reports an active staging configuration; by default discovery is disabled.

Each request is checked before any network activity:
* the provider's fixed host only (``api.openalex.org`` / ``api.crossref.org``), ``https``, port 443, the
  ``/works`` path, no user information and no query string in the URL (parameters are encoded here);
* the host name must resolve only to public addresses (no loopback, private, link-local, reserved or
  multicast), and the connection goes to the vetted address with TLS verified against the host name, so a
  DNS answer cannot be swapped between the check and the connect;
* TLS 1.2 or later with certificate and host-name verification; no proxy or cookies. The only credential is
  an optional bearer token (the OpenAlex API key) bound to one provider's host, sent as a header so it never
  appears in a URL, the request provenance or an error;
* optionally, through the staging egress proxy (``app.discovery_egress_proxy``): the transport then sends
  ``CONNECT <provider host>:443`` to that fixed internal proxy, which does the public-address vetting, and TLS
  is still verified end to end against the provider host name;
* redirects are not followed, the body is capped and must arrive within ``total_timeout`` seconds, and only
  rate-limit and content-type headers are kept.
"""
import http.client
import ipaddress
import socket
import ssl
import time
from urllib.parse import urlencode, urlsplit

from .models import ProviderRequest, ProviderResponse
from .transport import TransportError, TransportTimeout

ALLOWED_HOSTS = {"openalex": "api.openalex.org", "crossref": "api.crossref.org"}
ALLOWED_PATH = "/works"
KEPT_HEADERS = {"content-type", "retry-after", "x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset",
                "x-ratelimit-credits-used", "x-ratelimit-interval", "x-rate-limit-limit", "x-rate-limit-interval",
                "x-rate-limit-type", "x-concurrency-limit", "x-api-pool"}
MAX_BYTES = 2_000_000
CHUNK = 65_536


def tls_context() -> ssl.SSLContext:
    context = ssl.create_default_context()  # CERT_REQUIRED and host-name checking
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def _system_resolver(host: str) -> list[str]:
    return sorted({info[4][0] for info in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connects to an already vetted address while verifying TLS against the original host name."""

    def __init__(self, host: str, address: str, timeout: float, context: ssl.SSLContext):
        super().__init__(host, 443, timeout=timeout, context=context)
        self._sc_address, self._sc_context = address, context

    def connect(self):
        sock = socket.create_connection((self._sc_address, 443), self.timeout)
        self.sock = self._sc_context.wrap_socket(sock, server_hostname=self.host)


def _connect(host: str, address: str, timeout: float):
    return _PinnedHTTPSConnection(host, address, timeout, tls_context())


def _connect_via_proxy(host: str, proxy: tuple[str, int], timeout: float):
    # http.client sends CONNECT host:443 to the proxy, then wraps TLS with server_hostname=host.
    connection = http.client.HTTPSConnection(proxy[0], proxy[1], timeout=timeout, context=tls_context())
    connection.set_tunnel(host, 443)
    return connection


class HttpsTransport:
    kind = "https"
    external = True

    def __init__(self, *, user_agent: str, timeout: float = 10.0, total_timeout: float | None = None,
                 max_bytes: int = MAX_BYTES, resolver=_system_resolver, connector=_connect, clock=time.monotonic,
                 bearer=None, bearer_provider: str | None = None, proxy: tuple[str, int] | None = None,
                 proxy_connector=_connect_via_proxy):
        total_timeout = 2 * timeout if total_timeout is None else total_timeout
        if not 0 < timeout <= 30 or not timeout <= total_timeout <= 60 or not 0 < max_bytes <= 10_000_000:
            raise ValueError("timeout must be in (0, 30] s, total_timeout in [timeout, 60] s and max_bytes in (0, 10 MB]")
        self.user_agent, self.timeout, self.total_timeout, self.max_bytes = user_agent, timeout, total_timeout, max_bytes
        self._resolve, self._connect, self._clock = resolver, connector, clock
        if (bearer is None) != (bearer_provider is None) or (bearer_provider and bearer_provider not in ALLOWED_HOSTS):
            raise ValueError("a bearer token must be bound to exactly one allowed provider")
        self._bearer, self._bearer_provider = bearer, bearer_provider  # bearer: an activation.Secret
        self._proxy, self._proxy_connect = proxy, proxy_connector

    def target(self, request: ProviderRequest) -> tuple[str, str]:
        """(host, path with query) for an allowed request; raises TransportError otherwise. No network."""
        url = urlsplit(request.url)
        host = ALLOWED_HOSTS.get(request.provider)
        if (url.scheme != "https" or url.hostname != host or url.port not in (None, 443) or url.username
                or url.password or url.query or url.fragment or url.path != ALLOWED_PATH):
            raise TransportError("request-not-allowed")
        return host, ALLOWED_PATH + ("?" + urlencode(request.params) if request.params else "")

    def vetted_address(self, host: str) -> str:
        try:
            addresses = self._resolve(host)
        except OSError as error:
            raise TransportError("dns-failure") from error
        if not addresses:
            raise TransportError("dns-failure")
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if not ip.is_global or ip.is_multicast:
                raise TransportError("non-public-address")
        return addresses[0]

    def _read(self, response, deadline: float) -> bytes:
        """The body in chunks, refused once it passes the cap or the deadline (a socket timeout alone
        bounds each read, not a slowly trickled body)."""
        body = bytearray()
        while True:
            chunk = response.read(CHUNK)
            if not chunk:
                return bytes(body)
            body += chunk
            if len(body) > self.max_bytes:
                raise TransportError("response-too-large")
            if self._clock() > deadline:
                raise TransportTimeout("timeout")

    def send(self, request: ProviderRequest) -> ProviderResponse:
        host, path = self.target(request)
        deadline = self._clock() + self.total_timeout
        if self._proxy:
            connection = self._proxy_connect(host, self._proxy, self.timeout)
        else:
            connection = self._connect(host, self.vetted_address(host), self.timeout)
        try:
            headers = {"Host": host, "User-Agent": self.user_agent, "Accept": "application/json",
                       "Accept-Encoding": "identity", "Connection": "close"}
            if self._bearer is not None and request.provider == self._bearer_provider:
                headers["Authorization"] = "Bearer " + self._bearer.reveal()
            connection.request("GET", path, headers=headers)
            response = connection.getresponse()
            encoding = (response.getheader("Content-Encoding") or "identity").strip().lower()
            if encoding != "identity":
                raise TransportError("unexpected-content-encoding")
            body = self._read(response, deadline)
            headers = {name: value for name, value in response.getheaders() if name.lower() in KEPT_HEADERS}
            return ProviderResponse(status=response.status, headers=headers, body=body)
        except (TimeoutError, socket.timeout) as error:
            raise TransportTimeout("timeout") from error
        except (TransportError, TransportTimeout):
            raise
        except ssl.SSLError as error:
            raise TransportError("tls-failure") from error
        except (http.client.HTTPException, ValueError) as error:
            raise TransportError("protocol-error") from error
        except OSError as error:
            if str(error).startswith("Tunnel connection failed"):
                raise TransportError("egress-refused") from error
            raise TransportError("connection-failure") from error
        finally:
            connection.close()
