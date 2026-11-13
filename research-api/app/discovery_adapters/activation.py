"""RH-08D-03: whether live discovery may run in this process, and the provider policies it runs under.

Live discovery is **disabled by default**. It runs only when every condition below holds; otherwise
``resolve()`` returns an inactive state with a reason code, never a partly enabled one:

* ``RESEARCH_DISCOVERY_MODE=staging``. Unset, ``disabled`` or any other value disables discovery, so
  setting it back to ``disabled`` (or removing it) and restarting is the rollback;
* ``RESEARCH_ENV=staging``. Production is refused while ``PRODUCTION_DISCOVERY_AUTHORIZED`` is False. That is
  a code constant, so enabling production discovery is a reviewed change, not a configuration edit;
* ``RESEARCH_DISCOVERY_PROVIDERS`` lists the providers to call (``openalex``, ``crossref``), comma separated;
* ``RESEARCH_DISCOVERY_CONTACT`` is a monitored operator mailbox, sent to both providers as ``mailto`` and in
  the User-Agent (Crossref's "polite" identification; Crossref rate-limits per address). Reserved example and
  test domains are refused. It is never echoed in a response;
* when OpenAlex is enabled, ``RESEARCH_OPENALEX_API_KEY_FILE`` names a file (a container secret) holding the
  free OpenAlex API key. The key is read only from that file, never from an environment value, is sent only
  to ``api.openalex.org`` as an ``Authorization: Bearer`` header (never in a URL, so never in provenance), and
  is never logged, echoed or included in ``repr``.

The limits are deliberately conservative and sit below the providers' published limits as reviewed on
2026-11-12 (docs/RH-08D-03-LIVE-DISCOVERY.md); activation re-checks them against the providers' current terms.
"""
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

PRODUCTION_DISCOVERY_AUTHORIZED = False
LIVE_PROVIDERS = ("openalex", "crossref")
MODES = ("disabled", "staging")
CONTACT = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63})+$")
# Shared placeholder domains (RFC 2606/6761) would pool our requests with strangers' and reach no one.
RESERVED_DOMAINS = re.compile(r"(^|\.)(example\.(com|net|org)|example|test|invalid|localhost|local)$")
PROJECT_URL = "https://sustainablecatalyst.com"
OPENALEX_KEY_FILE_ENV = "RESEARCH_OPENALEX_API_KEY_FILE"
OPENALEX_KEY = re.compile(r"^[A-Za-z0-9_-]{16,128}$")

# Shared by every provider: one request per second per provider, at most two attempts per search and
# at most 3 s waiting between them; a whole search must finish its requests within RUN_DEADLINE_SECONDS.
RATE_PER_SECOND = 1.0
BURST = 1
MAX_ATTEMPTS = 2
MAX_RETRY_WAIT_SECONDS = 3.0
RUN_DEADLINE_SECONDS = 25.0
MAX_CONCURRENT_SEARCHES = 2
ROWS = 25


@dataclass(frozen=True)
class ProviderPolicy:
    provider: str
    timeout_seconds: float          # each connect and read
    total_timeout_seconds: float    # one request, including a slowly trickled body
    max_bytes: int                  # response body cap
    daily_request_budget: int       # requests per UTC day in this process, retries included
    circuit_failures: int           # consecutive failures that open the circuit
    circuit_cooldown_seconds: float  # how long an open circuit holds requests back


POLICIES = {
    # OpenAlex bills a search at $0.001 against a free key's $1/day, i.e. about 1,000 searches; stay below.
    "openalex": ProviderPolicy("openalex", 8.0, 15.0, 2_000_000, 800, 3, 300.0),
    "crossref": ProviderPolicy("crossref", 8.0, 15.0, 2_000_000, 2_000, 3, 300.0),
}


class Secret:
    """A credential that never appears in ``repr``, ``str`` or JSON."""
    __slots__ = ("_value",)

    def __init__(self, value: str):
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "Secret(***)"

    __str__ = __repr__


def load_openalex_key(env) -> tuple[Secret | None, str | None]:
    """(key, None) or (None, reason). Reads only the file named by RESEARCH_OPENALEX_API_KEY_FILE."""
    path = env.get(OPENALEX_KEY_FILE_ENV, "").strip()
    if not path:
        return None, "openalex-key-missing"
    try:
        value = Path(path).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None, "openalex-key-unreadable"
    if not OPENALEX_KEY.fullmatch(value):
        return None, "openalex-key-invalid"
    return Secret(value), None


def user_agent(contact: str) -> str:
    return f"SustainableCatalystResearch/0.3 (+{PROJECT_URL}; mailto:{contact}) research-discovery"


@dataclass(frozen=True)
class Activation:
    mode: str
    environment: str
    active: bool
    reason: str
    providers: tuple[str, ...] = ()
    contact: str | None = field(default=None, repr=False)
    openalex_key: Secret | None = field(default=None, repr=False)

    @property
    def user_agent(self) -> str | None:
        return user_agent(self.contact) if self.contact else None


def resolve(env=None) -> Activation:
    env = os.environ if env is None else env
    raw_mode = env.get("RESEARCH_DISCOVERY_MODE", "disabled").strip().lower() or "disabled"
    mode = raw_mode if raw_mode in MODES else "disabled"
    environment = env.get("RESEARCH_ENV", "").strip().lower()

    def off(reason: str) -> Activation:
        return Activation(mode=mode, environment=environment, active=False, reason=reason)

    if raw_mode not in MODES:
        return off("unknown-mode")
    if mode == "disabled":
        return off("mode-disabled")
    if environment == "production" and not PRODUCTION_DISCOVERY_AUTHORIZED:
        return off("production-not-authorized")
    if environment != "staging":
        return off("environment-not-staging")
    names = [p.strip().lower() for p in env.get("RESEARCH_DISCOVERY_PROVIDERS", "").split(",") if p.strip()]
    if not names:
        return off("no-providers")
    if any(p not in LIVE_PROVIDERS for p in names):
        return off("unknown-provider")
    contact = env.get("RESEARCH_DISCOVERY_CONTACT", "").strip()
    if not contact:
        return off("contact-missing")
    if len(contact) > 254 or not CONTACT.fullmatch(contact):
        return off("contact-invalid")
    if RESERVED_DOMAINS.search(contact.rsplit("@", 1)[1].lower()):
        return off("contact-reserved-domain")
    providers = tuple(p for p in LIVE_PROVIDERS if p in names)
    key = None
    if "openalex" in providers:
        key, problem = load_openalex_key(env)
        if problem:
            return off(problem)
    return Activation(mode=mode, environment=environment, active=True, reason="active", providers=providers,
                      contact=contact, openalex_key=key)
