"""Offline transports and the transport exceptions. The live HTTPS transport is in ``https_transport``."""
from .models import ProviderRequest, ProviderResponse


class ProviderDisabled(RuntimeError):
    """Raised before any request when a provider is not authorised to be called."""


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
