"""RH-08D-02: the prepared HTTPS transport for OpenAlex and Crossref. Built and tested, never wired in.

Nothing in this package constructs it. ``app.discovery`` would do so only when ``LIVE_PROVIDERS_AUTHORIZED`` is
true, and that constant stays false until the owner separately authorises live provider calls.

Each request is checked before any network activity:
* the provider's fixed host only (``api.openalex.org`` / ``api.crossref.org``), ``https``, port 443, the
  ``/works`` path, no user information and no query string in the URL (parameters are encoded here);
* the host name must resolve only to public addresses (no loopback, private, link-local, reserved or
  multicast), and the connection goes to the vetted address with TLS verified against the host name, so a
  DNS answer cannot be swapped between the check and the connect;
* TLS 1.2 or later with certificate and host-name verification; no proxy, cookies or credentials;
* redirects are not followed, the body is capped, and only rate-limit and content-type headers are kept.
"""
import http.client
import ipaddress
import socket
import ssl
from urllib.parse import urlencode, urlsplit

from .models import ProviderRequest, ProviderResponse
from .transport import TransportError, TransportTimeout

ALLOWED_HOSTS = {"openalex": "api.openalex.org", "crossref": "api.crossref.org"}
ALLOWED_PATH = "/works"
KEPT_HEADERS = {"content-type", "retry-after", "x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-interval",
                "x-rate-limit-limit", "x-rate-limit-interval"}
MAX_BYTES = 2_000_000


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


class HttpsTransport:
    kind = "https"
    external = True

    def __init__(self, *, user_agent: str, timeout: float = 10.0, max_bytes: int = MAX_BYTES,
                 resolver=_system_resolver, connector=_connect):
        if not 0 < timeout <= 30 or not 0 < max_bytes <= 10_000_000:
            raise ValueError("timeout must be in (0, 30] seconds and max_bytes in (0, 10 MB]")
        self.user_agent, self.timeout, self.max_bytes = user_agent, timeout, max_bytes
        self._resolve, self._connect = resolver, connector

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

    def send(self, request: ProviderRequest) -> ProviderResponse:
        host, path = self.target(request)
        address = self.vetted_address(host)
        connection = self._connect(host, address, self.timeout)
        try:
            connection.request("GET", path, headers={
                "Host": host, "User-Agent": self.user_agent, "Accept": "application/json",
                "Accept-Encoding": "identity", "Connection": "close"})
            response = connection.getresponse()
            encoding = (response.getheader("Content-Encoding") or "identity").strip().lower()
            if encoding != "identity":
                raise TransportError("unexpected-content-encoding")
            body = response.read(self.max_bytes + 1)
            if len(body) > self.max_bytes:
                raise TransportError("response-too-large")
            headers = {name: value for name, value in response.getheaders() if name.lower() in KEPT_HEADERS}
            return ProviderResponse(status=response.status, headers=headers, body=body)
        except (TimeoutError, socket.timeout) as error:
            raise TransportTimeout("timeout") from error
        except TransportError:
            raise
        except (ssl.SSLError, http.client.HTTPException, OSError, ValueError) as error:
            raise TransportError(type(error).__name__) from error
        finally:
            connection.close()
