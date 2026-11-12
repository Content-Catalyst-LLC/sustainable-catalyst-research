"""Client-side rate limiting and retry policy, with an injectable clock so tests never sleep.

The defaults are deliberately conservative (one request per second, burst of one) and are not a
statement of any provider's published limits; activation must set them from the provider's policy.
"""
import time
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime


class RateLimiter:
    """Token bucket per provider."""

    def __init__(self, *, rate_per_second: float = 1.0, burst: int = 1, clock=time.monotonic, sleep=time.sleep):
        if rate_per_second <= 0 or burst < 1:
            raise ValueError("rate and burst must be positive")
        self.rate, self.burst, self.clock, self.sleep = rate_per_second, burst, clock, sleep
        self._tokens: dict[str, float] = {}
        self._stamp: dict[str, float] = {}

    def acquire(self, provider: str) -> float:
        """Wait until a request may be sent; returns the seconds waited."""
        now = self.clock()
        tokens = min(self.burst, self._tokens.get(provider, self.burst) + (now - self._stamp.get(provider, now)) * self.rate)
        waited = 0.0
        if tokens < 1:
            waited = (1 - tokens) / self.rate
            self.sleep(waited)
            now, tokens = now + waited, 1.0
        self._tokens[provider], self._stamp[provider] = tokens - 1, now
        return waited


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    backoff_seconds: float = 1.0
    max_wait_seconds: float = 30.0
    retry_on: frozenset = field(default_factory=lambda: frozenset({"rate_limited", "unavailable"}))

    def wait_before(self, attempt: int, retry_after: float | None) -> float | None:
        """Seconds to wait before attempt ``attempt + 1``, or None to stop retrying."""
        if attempt >= self.max_attempts:
            return None
        wait = retry_after if retry_after is not None else self.backoff_seconds * 2 ** (attempt - 1)
        return None if wait > self.max_wait_seconds else max(wait, 0.0)


def retry_after_seconds(headers: dict[str, str], now_epoch: float | None = None) -> float | None:
    """``Retry-After`` as seconds (delta or HTTP date); None if absent or unreadable."""
    value = {k.lower(): v for k, v in headers.items()}.get("retry-after")
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value).timestamp()
    except (TypeError, ValueError):
        return None
    return max(0.0, when - (time.time() if now_epoch is None else now_epoch))
