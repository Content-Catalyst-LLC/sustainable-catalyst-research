"""RH-08D-04 governed discovery intake: offline tests (synthetic candidates, SQLite, no network).

Saved candidates stay discovered, unreviewed metadata; Library proposals are recorded but never sent.
"""
import base64
import json
from hashlib import sha256

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from tests.test_sc_ed01_editorial import attestation
from tests.test_sc_model01_workspace_ingest import PUB, pub_file, session  # noqa: F401 (session is a fixture)
from app import discovery
from app import discovery_intake as di
from app import editorial as ed
from app import workspaces as ws
from app.discovery_adapters import library_intake
from app.discovery_adapters.models import Candidate, CandidateAuthor, CandidateSource

OWNER, OTHER = "workspace-user:a", "workspace-user:b"
OWNER_ACTOR = ed.Actor(OWNER, "owner", OWNER, "*")
OTHER_ACTOR = ed.Actor(OTHER, "owner", OTHER, "*")
EDITOR = ed.Actor("workspace-user:ed", "editor", OWNER, "proj-1")
BASE = "/api/research/v1/discovery"
STAGING = {"RESEARCH_DISCOVERY_INTAKE": "staging", "RESEARCH_ENV": "staging",
           "RESEARCH_CONVERGENCE_WRITES": "staging-service-token"}


def h(text):
    return sha256(text.encode()).hexdigest()


def source(record="r1", provider="crossref", response="resp-1"):
    return CandidateSource(provider=provider, provider_record_id=f"{provider}:{record}",
                           retrieved_at="2026-11-13T01:15:44+00:00", query_sha256=h("query"),
                           request_sha256=h("request-" + record), response_sha256=h(response),
                           record_sha256=h(provider + record))


def cand(doi="10.1234/erw.2026.1", title="Enhanced rock weathering in agricultural soils", year=2026,
         sources=None, retracted=False, authors=("Ada Lovelace",), cid=None):
    return Candidate(candidate_id=cid or "cand-" + h(f"{doi}{title}{year}")[:24], doi=doi, title=title,
                     publication_year=year, authors=tuple(CandidateAuthor(name=a) for a in authors),
                     retracted=retracted, sources=tuple(sources or (source(),)))


@pytest.fixture
def articles(session):
    for owner, slug, project in ((OWNER, "erw-study", "proj-1"), (OWNER, "sdg-study", "proj-2"),
                                 (OTHER, "other-study", "proj-9")):
        ed.create_article(session, ed.Actor(owner, "owner", owner, "*"), area_slug="synthetic-area-" + owner[-1],
                          project_key="k-" + slug, workspace_project_id=project, slug=slug, title="Synthetic " + slug)
    session.flush()
    return session


# --- Gate ----------------------------------------------------------------------------------------


def test_intake_is_disabled_by_default_and_production_is_refused(monkeypatch, tmp_path):
    assert di.intake_state({}) == di.IntakeState(False, "mode-disabled")
    assert di.intake_state({"RESEARCH_DISCOVERY_INTAKE": "on"}).reason == "unknown-mode"
    assert di.intake_state({"RESEARCH_DISCOVERY_INTAKE": "staging", "RESEARCH_ENV": "production"}).reason \
        == "production-not-authorized"
    assert di.intake_state({"RESEARCH_DISCOVERY_INTAKE": "staging", "RESEARCH_ENV": "preview"}).reason \
        == "environment-not-staging"
    monkeypatch.delenv("RESEARCH_CONVERGENCE_WRITES", raising=False)
    assert di.intake_state({"RESEARCH_DISCOVERY_INTAKE": "staging", "RESEARCH_ENV": "staging"}).reason \
        == "identity-gate-closed"
    token = tmp_path / "t"
    token.write_text("staging-secret")
    for k, v in {**STAGING, "RESEARCH_CONVERGENCE_TOKEN_FILE": str(token)}.items():
        monkeypatch.setenv(k, v)
    assert di.intake_state() == di.IntakeState(True, "active")
    assert di.PRODUCTION_INTAKE_AUTHORIZED is False and di.LIBRARY_SUBMISSION_AUTHORIZED is False
    assert library_intake.LIBRARY_INTAKE_ENABLED is False


def test_readiness_and_status_report_intake_closed_by_default(monkeypatch):
    for k in ("RESEARCH_DISCOVERY_INTAKE", "RESEARCH_DISCOVERY_MODE"):
        monkeypatch.delenv(k, raising=False)
    client = TestClient(_app())
    ready = client.get(BASE + "/readiness").json()
    assert ready["candidate_intake"] == {"enabled": False, "reason": "mode-disabled"}
    assert ready["saved_searches_enabled"] is False and ready["library_intake_enabled"] is False
    status = client.get(BASE + "/intake/status").json()
    assert status["enabled"] is False and status["library_submission_authorized"] is False
    assert status["separation"]["research_evidence"] is False and status["publication_enabled"] is False
    r = client.get(BASE + "/intake/candidates")
    assert r.status_code == 503 and r.json()["detail"]["code"] == "DISCOVERY_INTAKE_DISABLED"


# --- Run handles ---------------------------------------------------------------------------------


def test_run_handles_expire_are_bounded_and_unguessable():
    now = {"t": 0.0}
    handles = di.RunHandles(ttl=10, capacity=2, clock=lambda: now["t"])
    c = cand()
    first = handles.put([c])
    assert first.startswith("run-") and len(first) > 20
    assert handles.candidate(first, c.candidate_id) == c
    with pytest.raises(LookupError):
        handles.candidate(first, "cand-" + "0" * 24)
    with pytest.raises(LookupError):
        handles.candidate("run-guess", c.candidate_id)
    second, third = handles.put([c]), handles.put([c])
    with pytest.raises(LookupError):  # evicted: capacity 2
        handles.candidate(first, c.candidate_id)
    now["t"] = 11
    for handle in (second, third):
        with pytest.raises(LookupError):
            handles.candidate(handle, c.candidate_id)


def synthetic_search(**kw):
    """The committed RH-08D-01 synthetic provider fixtures, as make_synthetic_search runs them."""
    from app.discovery_adapters import FixtureTransport, ProviderResponse, RateLimiter, crossref, openalex
    from tests.test_rh08d02_discover import QUERY, make_synthetic_search
    fix = make_synthetic_search.FIX
    transport = FixtureTransport({
        openalex.build_request(QUERY, rows=25).key(): ProviderResponse(status=200, body=(fix / "openalex-works.json").read_bytes()),
        crossref.build_request(QUERY, rows=25).key(): ProviderResponse(status=200, body=(fix / "crossref-works.json").read_bytes())})
    return discovery.search_response(discovery.DiscoverySearchRequest(query=QUERY), transport,
                                     limiter=RateLimiter(rate_per_second=10, clock=lambda: 0.0, sleep=lambda s: None),
                                     **kw)


def test_search_response_offers_a_handle_only_when_intake_is_enabled():
    closed = synthetic_search()
    assert closed["intake"] == {"enabled": False, "run_handle": None} and closed["saved"] is False
    handles = di.RunHandles()
    open_ = synthetic_search(handles=handles)
    handle = open_["intake"]["run_handle"]
    first = open_["run"]["candidates"][0]
    held = handles.candidate(handle, first["candidate_id"])
    assert held.model_dump(mode="json") == first  # the server's own copy, unchanged
    assert open_["saved"] is False and open_["run"]["persisted"] is False


# --- Saving, provenance and deduplication -----------------------------------------------------------


def test_saving_stores_metadata_and_provenance_and_keeps_separation(session):
    c = cand()
    out = di.save_candidate(session, OWNER_ACTOR, c)
    assert out["created"] is True and out["status"] == "saved-unreviewed"
    assert (out["classification"], out["review_status"], out["library_reviewed"], out["research_evidence"],
            out["publishable"]) == ("discovered-unreviewed", "unreviewed", False, False, False)
    assert out["metadata"] == di.candidate_metadata(c) and "sources" not in out["metadata"]
    (src,) = out["sources"]
    assert src["recordSha256"] == c.sources[0].record_sha256 and src["responseSha256"] == c.sources[0].response_sha256
    assert src["querySha256"] == h("query") and src["metadataSha256"] == out["metadataSha256"]
    stored = di.stored_candidate(session, session.get(di.DiscoverySavedCandidate, out["id"]))
    assert stored == c


def test_doi_deduplication_merges_new_provider_records_only(session):
    first = di.save_candidate(session, OWNER_ACTOR, cand(sources=[source("r1")]))
    again = di.save_candidate(session, OWNER_ACTOR, cand(doi="10.1234/ERW.2026.1", sources=[source("r1")]))
    assert again["id"] == first["id"] and again["deduplicated"] is True and again["sourcesAdded"] == 0
    other = di.save_candidate(session, OWNER_ACTOR, cand(sources=[source("o1", "openalex"), source("r1")]))
    assert other["id"] == first["id"] and other["sourcesAdded"] == 1
    assert {s["provider"] for s in other["sources"]} == {"crossref", "openalex"}
    changed = di.save_candidate(session, OWNER_ACTOR, cand(title="ERW in soils (corrected)", sources=[source("r2")]))
    assert changed["id"] == first["id"] and changed["metadataDiffers"] is True
    assert changed["title"] == first["title"]  # the first saved metadata is kept; each source records what it saw
    assert len({s["metadataSha256"] for s in changed["sources"]}) == 2
    assert session.scalar(select(di.DiscoverySavedCandidate).where(
        di.DiscoverySavedCandidate.owner_scope == OWNER)) is not None
    assert len(di.list_saved(session, OWNER_ACTOR)) == 1


def test_candidates_without_doi_deduplicate_on_normalised_metadata(session):
    a = di.save_candidate(session, OWNER_ACTOR, cand(doi=None, title="Sustainable Development  Indicators!",
                                                     authors=("J. Smith",)))
    b = di.save_candidate(session, OWNER_ACTOR, cand(doi=None, title="sustainable development indicators",
                                                     authors=("Jane Smith",), sources=[source("r9")]))
    assert b["id"] == a["id"] and b["sourcesAdded"] == 1
    c = di.save_candidate(session, OWNER_ACTOR, cand(doi=None, title="Sustainable development indicators",
                                                     authors=("Ann Jones",), sources=[source("r10")]))
    assert c["id"] != a["id"]  # another first author: a separate candidate...
    assert c["possibleDuplicateOf"] == [a["id"]]  # ...flagged, never merged


def test_same_title_with_a_different_doi_is_flagged_both_ways_not_merged(session):
    a = di.save_candidate(session, OWNER_ACTOR, cand(doi="10.1/a"))
    b = di.save_candidate(session, OWNER_ACTOR, cand(doi="10.1/b", sources=[source("r2")]))
    assert b["id"] != a["id"] and b["possibleDuplicateOf"] == [a["id"]]
    first = di.saved_view(session, session.get(di.DiscoverySavedCandidate, a["id"]))
    assert first["possibleDuplicateOf"] == [b["id"]]  # the earlier candidate learns of the later one too
    assert "possible-duplicate" in first["review"]["blockingIssues"]
    with pytest.raises(ValueError, match="possible-duplicate"):
        di.propose_to_library(session, OWNER_ACTOR, a["id"], [])
    view = di.propose_to_library(session, OWNER_ACTOR, a["id"], ["possible-duplicate"])
    row = session.scalar(select(di.DiscoveryLibraryProposal))
    assert row.proposal["possible_duplicate_of"] == [b["candidateId"]] and view["libraryProposal"]["sent"] is False


def test_every_source_keeps_the_metadata_it_arrived_with(session):
    first = cand(sources=[source("r1")])
    later = cand(title="ERW in soils (corrected)", sources=[source("o1", "openalex")])
    di.save_candidate(session, OWNER_ACTOR, first)
    out = di.save_candidate(session, OWNER_ACTOR, later)
    rows = {s.provider_record_id: s for s in session.scalars(select(di.DiscoveryCandidateSource))}
    assert rows["crossref:r1"].candidate_metadata == di.candidate_metadata(first)
    assert rows["openalex:o1"].candidate_metadata == di.candidate_metadata(later)
    from app import convergence as cv
    assert all(cv.canonical_sha256(r.candidate_metadata) == r.metadata_sha256 for r in rows.values())
    assert out["review"]["conflictingFields"] == ["title"]


def test_a_retraction_reported_by_a_later_record_reaches_the_library_reviewer(session):
    saved = di.save_candidate(session, OWNER_ACTOR, cand(retracted=False))
    assert saved["review"]["blockingIssues"] == []
    later = di.save_candidate(session, OWNER_ACTOR, cand(retracted=True, sources=[source("o1", "openalex")]))
    assert later["id"] == saved["id"] and later["metadata"]["retracted"] is False  # first metadata kept...
    assert later["review"]["retractionReported"] is True  # ...but the retraction is not hidden
    assert later["review"]["blockingIssues"] == ["retracted", "metadata-conflict"]
    with pytest.raises(ValueError, match="retracted"):
        di.propose_to_library(session, OWNER_ACTOR, saved["id"], ["metadata-conflict"])
    di.propose_to_library(session, OWNER_ACTOR, saved["id"], ["retracted", "metadata-conflict"])
    proposal = session.scalar(select(di.DiscoveryLibraryProposal)).proposal
    assert proposal["retracted"] is True
    (conflict,) = proposal["conflicts"]
    assert conflict["field"] == "retracted" and sorted(conflict["values"].values()) == ["false", "true"]
    assert len(proposal["sources"]) == 2


def test_a_concurrent_duplicate_save_is_a_409_not_a_server_error(http, monkeypatch):
    from sqlalchemy.exc import IntegrityError
    client, _ = http
    c = cand()
    handle = di.HANDLES.put([c])

    def racing(*args, **kwargs):
        raise IntegrityError("INSERT", {}, Exception("duplicate key"))
    monkeypatch.setattr(di, "save_candidate", racing)
    r = client.post(BASE + "/intake/candidates", json={"run_handle": handle, "candidate_id": c.candidate_id},
                    headers={"Authorization": "Bearer staging-secret", "X-SC-Owner-Scope": OWNER})
    assert r.status_code == 409


def test_long_dois_are_hashed_into_the_identity_key(session):
    long_a, long_b = "10.1234/" + "x" * 200 + "a", "10.1234/" + "x" * 200 + "b"
    a = di.save_candidate(session, OWNER_ACTOR, cand(doi=long_a))
    b = di.save_candidate(session, OWNER_ACTOR, cand(doi=long_b, sources=[source("r2")]))
    assert a["id"] != b["id"]
    assert all(len(r.dedup_key) <= 80 for r in session.scalars(select(di.DiscoverySavedCandidate)))


# --- Owner isolation and authorisation ----------------------------------------------------------------


def test_owners_never_see_or_change_each_others_candidates(articles):
    s = articles
    mine = di.save_candidate(s, OWNER_ACTOR, cand())
    theirs = di.save_candidate(s, OTHER_ACTOR, cand())
    assert theirs["id"] != mine["id"] and theirs["created"] is True  # deduplication is per owner
    assert [c["id"] for c in di.list_saved(s, OTHER_ACTOR)] == [theirs["id"]]
    for fn, args in ((di.dismiss, ()), (di.associate, ("other-study",)), (di.propose_to_library, ([],))):
        with pytest.raises(LookupError, match="Saved candidate not found"):
            fn(s, OTHER_ACTOR, mine["id"], *args)
    with pytest.raises(LookupError, match="Research article not found"):  # another owner's article
        di.associate(s, OWNER_ACTOR, mine["id"], "other-study")


def test_editors_save_and_associate_only_within_their_granted_project(articles):
    s = articles
    saved = di.save_candidate(s, EDITOR, cand())
    assert saved["savedBy"] == "workspace-user:ed"
    assert di.associate(s, EDITOR, saved["id"], "erw-study")["associations"] == [
        {"entry": "erw-study", "relation": "related-candidate"}]
    with pytest.raises(PermissionError, match="does not cover"):
        di.associate(s, EDITOR, saved["id"], "sdg-study")  # proj-2 is outside the grant
    with pytest.raises(PermissionError, match="Only the Workspace project owner"):
        di.propose_to_library(s, EDITOR, saved["id"], ["retraction-status-unknown"])
    with pytest.raises(PermissionError, match="Only the Workspace project owner"):
        di.dismiss(s, EDITOR, saved["id"])


# --- Associations, dismissal and Library proposals -------------------------------------------------------


def test_associations_are_reversible_and_never_evidence(articles):
    s = articles
    saved = di.save_candidate(s, OWNER_ACTOR, cand())
    for slug in ("erw-study", "sdg-study", "erw-study"):
        view = di.associate(s, OWNER_ACTOR, saved["id"], slug)
    assert [a["entry"] for a in view["associations"]] == ["erw-study", "sdg-study"]
    assert view["research_evidence"] is False and view["publishable"] is False
    view = di.dissociate(s, OWNER_ACTOR, saved["id"], "sdg-study")
    assert [a["entry"] for a in view["associations"]] == ["erw-study"]
    assert [a["entry"] for a in di.associate(s, OWNER_ACTOR, saved["id"], "sdg-study")["associations"]] == [
        "erw-study", "sdg-study"]


def test_dismissal_keeps_provenance_and_a_new_save_restores(articles):
    s = articles
    saved = di.save_candidate(s, OWNER_ACTOR, cand())
    view = di.dismiss(s, OWNER_ACTOR, saved["id"])
    assert view["status"] == "dismissed" and len(view["sources"]) == 1
    with pytest.raises(ValueError, match="dismissed"):
        di.associate(s, OWNER_ACTOR, saved["id"], "erw-study")
    with pytest.raises(ValueError, match="dismissed"):
        di.propose_to_library(s, OWNER_ACTOR, saved["id"], ["retraction-status-unknown"])
    again = di.save_candidate(s, OWNER_ACTOR, cand())
    assert again["id"] == saved["id"] and again["status"] == "saved-unreviewed"


def test_library_proposal_needs_the_owner_to_acknowledge_exactly_the_blocking_issues(articles):
    s = articles
    saved = di.save_candidate(s, OWNER_ACTOR, cand(retracted=None))
    with pytest.raises(ValueError, match=r"\['retraction-status-unknown'\]"):
        di.propose_to_library(s, OWNER_ACTOR, saved["id"], [])
    with pytest.raises(ValueError, match="Acknowledge exactly"):
        di.propose_to_library(s, OWNER_ACTOR, saved["id"], ["retraction-status-unknown", "no-doi"])
    view = di.propose_to_library(s, OWNER_ACTOR, saved["id"], ["retraction-status-unknown"])
    proposal = view["libraryProposal"]
    assert proposal["status"] == "awaiting-library-endpoint" and proposal["sent"] is False
    assert proposal["blockingIssues"] == ["retraction-status-unknown"]
    row = s.scalar(select(di.DiscoveryLibraryProposal))
    expected = library_intake.draft_proposal(di.stored_candidate(s, s.get(di.DiscoverySavedCandidate, saved["id"])))
    assert row.proposal == expected.model_dump(mode="json", by_alias=True)
    assert row.proposal["status"] == "draft-not-submitted" and row.proposal["evidence_status"] == "not-evidence"
    with pytest.raises(ValueError, match="already been proposed"):
        di.propose_to_library(s, OWNER_ACTOR, saved["id"], ["retraction-status-unknown"])
    with pytest.raises(library_intake.LibraryIntakeDisabled):
        library_intake.submit(expected)  # nothing can send it


def test_a_clean_candidate_is_proposed_with_no_issues_to_acknowledge(session):
    saved = di.save_candidate(session, OWNER_ACTOR, cand())
    assert di.propose_to_library(session, OWNER_ACTOR, saved["id"], [])["libraryProposal"]["blockingIssues"] == []


# --- Audit chain ---------------------------------------------------------------------------------------


def test_every_change_is_audited_in_a_verifiable_chain_without_search_text(articles):
    s = articles
    saved = di.save_candidate(s, OWNER_ACTOR, cand())
    di.save_candidate(s, OWNER_ACTOR, cand(sources=[source("r2")]))
    di.associate(s, OWNER_ACTOR, saved["id"], "erw-study")
    di.dissociate(s, OWNER_ACTOR, saved["id"], "erw-study")
    di.propose_to_library(s, OWNER_ACTOR, saved["id"], [])
    di.dismiss(s, OWNER_ACTOR, saved["id"])
    actions = [e.action for e in s.scalars(select(di.DiscoveryIntakeEvent).order_by(di.DiscoveryIntakeEvent.seq))]
    assert actions == ["candidate-saved", "candidate-deduplicated", "candidate-associated", "candidate-dissociated",
                       "library-proposal-recorded", "candidate-dismissed"]
    assert di.verify_chain(s)["valid"] is True
    dump = json.dumps([e.body for e in s.scalars(select(di.DiscoveryIntakeEvent))])
    assert "rock weathering" not in dump.lower() and "query" not in dump
    tampered = s.scalar(select(di.DiscoveryIntakeEvent).where(di.DiscoveryIntakeEvent.seq == 3))
    tampered.body = {**tampered.body, "actor": "someone-else"}
    s.flush()
    assert di.verify_chain(s) == {"valid": False, "events": 3, "brokenAt": 3}


# --- HTTP ------------------------------------------------------------------------------------------------


def _app():
    from app.main import app
    return app


@pytest.fixture
def http(monkeypatch, tmp_path):
    engine = create_engine("sqlite+pysqlite:///" + str(tmp_path / "intake.sqlite"))
    ws.Base.metadata.create_all(engine)
    monkeypatch.setattr(ws, "_session_factory", lambda: Session(engine, expire_on_commit=False))
    token = tmp_path / "t"
    token.write_text("staging-secret")
    for k, v in {**STAGING, "RESEARCH_CONVERGENCE_TOKEN_FILE": str(token),
                 "RESEARCH_WORKSPACE_PUBLIC_KEY_FILE": pub_file(tmp_path, PUB)}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(di, "HANDLES", di.RunHandles())
    with Session(engine) as s:
        ed.create_article(s, OWNER_ACTOR, area_slug="synthetic-area", project_key="k", workspace_project_id="proj-1",
                          slug="erw-study", title="Synthetic study")
        s.commit()
    return TestClient(_app()), engine


def test_http_intake_flow_uses_server_held_results(http):
    client, engine = http
    owner = {"Authorization": "Bearer staging-secret", "X-SC-Owner-Scope": OWNER}
    c = cand(retracted=None)
    handle = di.HANDLES.put([c])
    assert client.post(BASE + "/intake/candidates", json={"run_handle": handle, "candidate_id": c.candidate_id},
                       headers={"X-SC-Owner-Scope": OWNER}).status_code == 401
    r = client.post(BASE + "/intake/candidates", json={"run_handle": "run-nope", "candidate_id": c.candidate_id},
                    headers=owner)
    assert r.status_code == 404
    r = client.post(BASE + "/intake/candidates", json={"run_handle": handle, "candidate_id": c.candidate_id,
                                                       "title": "Forged title", "sources": []}, headers=owner)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store", r.text
    saved = r.json()
    assert saved["title"] == c.title and len(saved["sources"]) == 1  # client-supplied metadata is ignored
    sid = saved["id"]
    assert client.post(f"{BASE}/intake/candidates/{sid}/associations/erw-study", headers=owner).json()[
        "associations"] == [{"entry": "erw-study", "relation": "related-candidate"}]
    r = client.post(f"{BASE}/intake/candidates/{sid}/library-proposal", headers=owner,
                    json={"acknowledged_blocking_issues": []})
    assert r.status_code == 422 and "retraction-status-unknown" in r.text
    r = client.post(f"{BASE}/intake/candidates/{sid}/library-proposal", headers=owner,
                    json={"acknowledged_blocking_issues": ["retraction-status-unknown"]})
    assert r.status_code == 200 and r.json()["libraryProposal"]["sent"] is False
    att = base64.urlsafe_b64encode(json.dumps(attestation()).encode()).decode().rstrip("=")
    editor = {**owner, "X-SC-Actor-Scope": "workspace-user:ed", "X-SC-Editor-Attestation": att}
    assert client.post(f"{BASE}/intake/candidates/{sid}/dismiss", headers=editor).status_code == 403
    other = {"Authorization": "Bearer staging-secret", "X-SC-Owner-Scope": OTHER}
    assert client.get(f"{BASE}/intake/candidates/{sid}", headers=other).status_code == 404
    assert client.get(BASE + "/intake/candidates", headers=other).json() == {"items": []}
    assert [i["id"] for i in client.get(BASE + "/intake/candidates", headers=owner).json()["items"]] == [sid]
    with Session(engine) as s:
        assert di.verify_chain(s)["events"] == 3


def test_http_intake_refuses_production_even_with_the_flag(http, monkeypatch):
    client, _ = http
    monkeypatch.setenv("RESEARCH_ENV", "production")
    r = client.get(BASE + "/intake/candidates", headers={"Authorization": "Bearer staging-secret",
                                                          "X-SC-Owner-Scope": OWNER})
    assert r.status_code == 503 and r.json()["detail"]["reason"] == "production-not-authorized"
