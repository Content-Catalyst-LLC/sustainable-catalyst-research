"""RH-08D-03 staging acceptance: checks a staging Research API with live discovery switched on.

Run it only after the owner has authorised a staging activation (docs/RH-08D-03-LIVE-DISCOVERY.md). It
talks to the Research API, never to a provider directly, and refuses any base URL that is not loopback (an
SSH tunnel) or an https host with a ``staging`` label. It sends a few fixed public queries, spaced out, and
writes a summary report: counts, outcome codes and hashes, no candidate titles and no contact address.

    python scripts/discovery_staging_acceptance.py --base-url http://127.0.0.1:8000/api/research/v1 \\
        --out discovery-staging-acceptance.json
"""
import argparse
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

QUERIES = ("enhanced rock weathering agricultural soils", "sustainable development indicators")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
OUTCOMES = {"ok", "disabled", "rate_limited", "unavailable", "failed", "invalid_response"}
SPACING_SECONDS = 5.0


def allowed_base(url: str) -> bool:
    parts = urlsplit(url)
    if parts.username or parts.password or parts.query or parts.fragment:
        return False
    if parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost", "::1"):
        return True
    labels = (parts.hostname or "").split(".")
    return parts.scheme == "https" and any(label == "staging" or label.startswith("staging-") for label in labels[:-1])


def http_fetch(method: str, url: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 (base URL vetted above)
            return response.status, {k.lower(): v for k, v in response.headers.items()}, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, {k.lower(): v for k, v in error.headers.items()}, json.loads(error.read() or b"null")


def check_readiness(data: dict) -> list[str]:
    problems = []
    if data.get("status") != "active_staging" or data.get("external_retrieval_enabled") is not True:
        problems.append("discovery is not active in staging")
    if data.get("contact_configured") is not True:
        problems.append("no contact address configured")
    if "openalex" in (data.get("enabled_providers") or []) and data.get("openalex_api_key_configured") is not True:
        problems.append("OpenAlex enabled without an API key")
    for key in ("production_discovery_authorized", "saved_searches_enabled", "library_intake_enabled",
                "publication_enabled", "research_evidence_promotion_enabled"):
        if data.get(key) is not False:
            problems.append(f"readiness {key} is not false")
    return problems


def _echoes(value, query: str) -> bool:
    """A ``query`` key anywhere, or any string that is the search text itself. A candidate title that merely
    contains the search words is provider metadata, not an echo."""
    if isinstance(value, dict):
        return "query" in value or any(_echoes(v, query) for v in value.values())
    if isinstance(value, list):
        return any(_echoes(v, query) for v in value)
    return isinstance(value, str) and " ".join(value.split()).casefold() == query


def check_search(status: int, headers: dict, data: dict, query: str) -> list[str]:
    """Every invariant a live staging search response must hold. An empty list means it passed."""
    if status != 200:
        return [f"search returned HTTP {status}"]
    problems = []
    if headers.get("cache-control") != "no-store":
        problems.append("missing Cache-Control: no-store")
    if _echoes(data, " ".join(query.split()).casefold()):
        problems.append("search text echoed in the response")
    if (data.get("run") or {}).get("query_sha256") != hashlib.sha256(" ".join(query.split()).casefold().encode()).hexdigest():
        problems.append("query hash does not match the search")
    separation = data.get("separation") or {}
    if (separation.get("tier"), separation.get("library_reviewed"), separation.get("research_evidence")) != \
            ("discovered-metadata", False, False):
        problems.append("separation block missing or wrong")
    if data.get("saved") is not False or data.get("publishable") is not False:
        problems.append("response claims saved or publishable")
    if (data.get("library_intake") or {}).get("enabled") is not False:
        problems.append("Library intake enabled")
    run = data.get("run") or {}
    if run.get("transport") != "https" or run.get("persisted") is not False or run.get("publishable") is not False:
        problems.append("run is not an unsaved, unpublishable https run")
    for o in run.get("outcomes", []):
        if o.get("status") not in OUTCOMES:
            problems.append(f"unknown outcome status {o.get('status')!r}")
    if run.get("external_request_performed") is not any(o.get("attempts") for o in run.get("outcomes", [])):
        problems.append("external_request_performed does not match the attempts")
    for c in run.get("candidates", []):
        if (c.get("classification"), c.get("review_status"), c.get("publishable")) != \
                ("discovered-unreviewed", "unreviewed", False):
            problems.append(f"{c.get('candidate_id')}: not discovered-unreviewed")
        if not c.get("sources"):
            problems.append(f"{c.get('candidate_id')}: no provenance")
        for s in c.get("sources", []):
            if not all(HEX64.match(str(s.get(k, ""))) for k in ("query_sha256", "request_sha256", "response_sha256",
                                                                   "record_sha256")):
                problems.append(f"{c.get('candidate_id')}: provenance hash missing")
            if not s.get("retrieved_at"):
                problems.append(f"{c.get('candidate_id')}: no retrieval time")
    return problems


def summarise(data: dict) -> dict:
    run = data.get("run") or {}
    candidates = run.get("candidates", [])
    return {
        "query_sha256": run.get("query_sha256"),
        "started_at": run.get("started_at"),
        "outcomes": [{k: o.get(k) for k in ("provider", "status", "code", "attempts", "records_received",
                                            "records_rejected", "rejection_reasons")} for o in run.get("outcomes", [])],
        "candidates": len(candidates),
        "with_doi": sum(1 for c in candidates if c.get("doi")),
        "retracted": sum(1 for c in candidates if c.get("retracted") is True),
        "multi_source": sum(1 for c in candidates if len({s["provider"] for s in c.get("sources", [])}) > 1),
        "possible_duplicates": sum(1 for c in candidates if c.get("possible_duplicate_of")),
        "with_conflicts": sum(1 for c in candidates if c.get("conflicts")),
        "response_sha256": sorted({s["response_sha256"] for c in candidates for s in c.get("sources", [])}),
    }


def run_acceptance(base: str, fetch=http_fetch, sleep=time.sleep, queries=QUERIES) -> dict:
    if not allowed_base(base):
        raise SystemExit("refused: base URL must be loopback or an https staging host")
    base = base.rstrip("/")
    report = {"schema": "sc-research-discovery-staging-acceptance/1.0", "checks": [], "searches": []}

    def check(name, problems):
        report["checks"].append({"check": name, "passed": not problems, "problems": problems})

    _, _, ready = fetch("GET", base + "/discovery/readiness")
    check("readiness", check_readiness(ready or {}))
    report["stage"] = (ready or {}).get("stage")
    for index, query in enumerate(queries):
        if index:
            sleep(SPACING_SECONDS)
        status, headers, data = fetch("POST", base + "/discovery/search", {"query": query})
        check(f"search {index + 1}", check_search(status, headers, data or {}, query))
        if status == 200:
            report["searches"].append(summarise(data))
    status, _, data = fetch("POST", base + "/discovery/search", {"query": queries[0], "provider": "arxiv"})
    check("unsupported source refused", [] if status == 503 and (data or {}).get("detail", {}).get("code") ==
          "DISCOVERY_PROVIDER_NOT_AVAILABLE" else [f"arxiv search returned HTTP {status}"])
    report["passed"] = all(c["passed"] for c in report["checks"])
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    report = run_acceptance(args.base_url)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=1)
        handle.write("\n")
    print(("PASS" if report["passed"] else "FAIL") + f": {sum(c['passed'] for c in report['checks'])}/"
          f"{len(report['checks'])} checks; report in {args.out}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
