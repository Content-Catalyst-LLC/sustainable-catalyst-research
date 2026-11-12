"""Regenerates discovery-search-synthetic.json: the RH-08D-01 synthetic provider fixtures run through the
RH-08D-02 search response. Offline; the Discover interface tests render this file.

    python tests/fixtures/rh08d02/make_synthetic_search.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "research-api"))

from app import discovery  # noqa: E402
from app.discovery_adapters import FixtureTransport, ProviderResponse, RateLimiter, crossref, openalex  # noqa: E402

FIX = ROOT / "tests/fixtures/rh08d01"
QUERY = "synthetic basalt weathering"
NOW = "2026-11-12T00:00:00+00:00"
OUT = Path(__file__).with_name("discovery-search-synthetic.json")


def build() -> dict:
    def reply(name):
        return ProviderResponse(status=200, body=(FIX / name).read_bytes())
    transport = FixtureTransport({openalex.build_request(QUERY, rows=25).key(): reply("openalex-works.json"),
                                  crossref.build_request(QUERY, rows=25).key(): reply("crossref-works.json")})
    request = discovery.DiscoverySearchRequest(query=QUERY)
    return discovery.search_response(request, transport, now=lambda: NOW,
                                     limiter=RateLimiter(rate_per_second=10, clock=lambda: 0.0, sleep=lambda s: None))


def render(data: dict) -> str:
    return json.dumps(data, indent=1, ensure_ascii=False) + "\n"  # key order carries provider precedence


if __name__ == "__main__":
    OUT.write_text(render(build()), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
