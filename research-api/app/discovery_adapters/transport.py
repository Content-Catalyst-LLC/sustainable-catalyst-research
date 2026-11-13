"""Offline transports and the transport exceptions. The live HTTPS transport is in ``https_transport``."""
from .models import ProviderRequest, ProviderResponse

# Short failure codes a transport may raise; a run reports them as the outcome code. Anything else is
# reported as the generic ``transport-error`` so no exception text reaches a response.
TRANSPORT_CODES = frozenset({"request-not-allowed", "dns-failure", "non-public-address", "tls-failure",
                             "connection-failure", "protocol-error", "unexpected-content-encoding",
                             "response-too-large", "egress-refused"})


class ProviderDisabled(RuntimeError):
    """Raised before any request when a provider is not authorised to be called."""


class ProviderHeld(RuntimeError):
    """Raised before any request when a provider policy holds it back (budget spent, circuit open, deadline).

    ``status`` is the outcome to report (``rate_limited`` or ``unavailable``) and ``code`` says why."""

    def __init__(self, status: str, code: str, retry_after: float | None = None):
        super().__init__(code)
        self.status, self.code, self.retry_after = status, code, retry_after


class TransportTimeout(RuntimeError):
    pass


class TransportError(RuntimeError):
    pass


class DisabledTransport:
    """The default: refuses every request, so nothing leaves the process."""
    kind = "disabled"
    external = False

    def send(self, request: ProviderRequest) -> ProviderResponse:
        raise ProviderDisabled(f"{request.provider} calls are not authorised")


class FixtureTransport:
    """Replays synthetic responses keyed by ``ProviderRequest.key()``; each key may hold a sequence."""
    kind = "fixture"
    external = False

    def __init__(self, responses: dict):
        self._responses = {key: list(value) if isinstance(value, list) else [value] for key, value in responses.items()}
        self.sent: list[ProviderRequest] = []

    def send(self, request: ProviderRequest) -> ProviderResponse:
        self.sent.append(request)
        queue = self._responses.get(request.key())
        if not queue:
            raise TransportError(f"no fixture for {request.key()}")
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return item
