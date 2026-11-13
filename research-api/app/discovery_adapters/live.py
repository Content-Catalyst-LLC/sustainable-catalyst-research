"""RH-08D-03: the guarded live transport. Policy checks around the vetted HTTPS transport.

``GuardedTransport`` is built for one search and enforces, before each request:
* only providers enabled by ``activation`` (anything else raises ``ProviderDisabled``);
* the run deadline: the remaining time also bounds each request's timeouts;
* the provider's daily request budget and circuit breaker, held process-wide by ``ProviderGuard``;
* a provider's own signal that its budget is spent (``X-RateLimit-Remaining: 0`` with ``X-RateLimit-Reset``,
  as OpenAlex sends): further requests are held until the reset, at most a day.
Every response or transport failure is reported back to the guard. Budgets and circuits live in memory
only; nothing is persisted, and a restart resets them.
"""
import threading
import time
from datetime import datetime, timezone

from .activation import POLICIES, RUN_DEADLINE_SECONDS, ProviderPolicy
from .models import ProviderRequest, ProviderResponse
from .transport import ProviderDisabled, ProviderHeld, TransportError, TransportTimeout

MIN_REQUEST_SECONDS = 1.0
MAX_PROVIDER_HOLD_SECONDS = 86_400.0


def _utc_day() -> str:
    return datetime.now(timezone.utc).date().isoformat()


class ProviderGuard:
    """Daily request budgets and consecutive-failure circuit breakers, shared by all searches."""

    def __init__(self, policies: dict[str, ProviderPolicy] = POLICIES, *, clock=time.monotonic, day=_utc_day):
        self.policies, self._clock, self._day = policies, clock, day
        self._lock = threading.Lock()
        self._used: dict[tuple[str, str], int] = {}
        self._failures: dict[str, int] = {}
        self._open_until: dict[str, float] = {}
        self._spent_until: dict[str, float] = {}

    def admit(self, provider: str) -> None:
        """Count one request against the budget, or raise ``ProviderHeld`` without sending it."""
        policy = self.policies[provider]
        with self._lock:
            now = self._clock()
            if self._open_until.get(provider, 0.0) > now:
                raise ProviderHeld("unavailable", "circuit-open", round(self._open_until[provider] - now, 1))
            if self._spent_until.get(provider, 0.0) > now:
                raise ProviderHeld("rate_limited", "provider-budget-exhausted",
                                   round(self._spent_until[provider] - now, 1))
            key = (self._day(), provider)
            self._used = {k: v for k, v in self._used.items() if k[0] == key[0]}
            if self._used.get(key, 0) >= policy.daily_request_budget:
                raise ProviderHeld("rate_limited", "daily-budget-exhausted")
            self._used[key] = self._used.get(key, 0) + 1

    def record(self, provider: str, healthy: bool) -> None:
        policy = self.policies[provider]
        with self._lock:
            if healthy:
                self._failures[provider] = 0
                return
            self._failures[provider] = self._failures.get(provider, 0) + 1
            if self._failures[provider] >= policy.circuit_failures:
                self._open_until[provider] = self._clock() + policy.circuit_cooldown_seconds
                self._failures[provider] = 0

    def provider_spent(self, provider: str, reset_seconds: float) -> None:
        with self._lock:
            hold = min(max(reset_seconds, 60.0), MAX_PROVIDER_HOLD_SECONDS)
            self._spent_until[provider] = self._clock() + hold

    def status(self) -> dict:
        """Public, aggregate state: whether each circuit is open. No counts of individual searches."""
        with self._lock:
            now = self._clock()
            return {p: {"circuit_open": self._open_until.get(p, 0.0) > now} for p in self.policies}


def https_factory(user_agent: str, *, openalex_key=None, proxy=None, **transport_options):
    """Builds one HttpsTransport per request. The OpenAlex key, if any, is bound to the OpenAlex host only.
    ``transport_options`` (resolver, connector, clock) exist for offline tests."""
    def build(policy: ProviderPolicy, timeout: float, total_timeout: float):
        from .https_transport import HttpsTransport
        bearer = openalex_key if policy.provider == "openalex" else None
        return HttpsTransport(user_agent=user_agent, timeout=timeout, total_timeout=total_timeout,
                              max_bytes=policy.max_bytes, bearer=bearer,
                              bearer_provider="openalex" if bearer is not None else None, proxy=proxy,
                              **transport_options)
    return build


def _number(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class GuardedTransport:
    kind = "https"
    external = True

    def __init__(self, guard: ProviderGuard, providers, factory, *, deadline_seconds: float = RUN_DEADLINE_SECONDS,
                 clock=time.monotonic):
        self._guard, self._providers, self._factory, self._clock = guard, frozenset(providers), factory, clock
        self._deadline = clock() + deadline_seconds

    def send(self, request: ProviderRequest) -> ProviderResponse:
        provider = request.provider
        if provider not in self._providers:
            raise ProviderDisabled(f"{provider} is not enabled")
        remaining = self._deadline - self._clock()
        if remaining < MIN_REQUEST_SECONDS:
            raise ProviderHeld("unavailable", "run-deadline")
        policy = self._guard.policies[provider]
        total = min(policy.total_timeout_seconds, remaining)
        transport = self._factory(policy, min(policy.timeout_seconds, total), total)
        self._guard.admit(provider)
        try:
            response = transport.send(request)
        except (TransportTimeout, TransportError):
            self._guard.record(provider, healthy=False)
            raise
        self._guard.record(provider, healthy=response.status < 500 and response.status != 429)
        headers = {k.lower(): v for k, v in response.headers.items()}
        remaining, reset = _number(headers.get("x-ratelimit-remaining")), _number(headers.get("x-ratelimit-reset"))
        if remaining is not None and remaining <= 0 and reset is not None:
            self._guard.provider_spent(provider, reset)
        return response
