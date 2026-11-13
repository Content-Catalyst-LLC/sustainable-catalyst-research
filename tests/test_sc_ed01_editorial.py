"""SC-ED-01 Research editorial publishing (synthetic fixtures only; public CI).

Workspace-signed exports, editor attestations and approval records are produced with the local
test key that plays Workspace's signing key (see test_sc_model01_workspace_ingest). Real
Workspace-issued records are exercised in the isolated staging acceptance.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from tests.test_sc_model01_convergence import signed_record as _signed_record
from tests.test_sc_model01_workspace_ingest import (  # noqa: F401 (session is a fixture)
    BINDING, OTHER, PUB, export, grant, ingest, session, sign,
)
from app import convergence as cv  # noqa: E402
from app import editorial as ed  # noqa: E402
from app import object_registry as md  # noqa: E402
from app import workspace_ingest as wi  # noqa: E402
from app import workspaces as ws  # noqa: E402

OWNER = "workspace-user:a"
AREA = "metrics-of-sustainable-development"
SLUG = "ws-study-a"
OWNER_ACTOR = ed.Actor(OWNER, "owner", OWNER, "*")


def signed_record(subject, dimension, owner=OWNER, **kw):
    return _signed_record(subject, dimension, owner=owner, **kw)


def attestation(grantee="workspace-user:ed", owner=OWNER, project="proj-1", role="editor", minutes=30, key=None,
                issued_ago=0.0):
    """``minutes`` is the lifetime from issue; ``issued_ago`` backdates the issue (minutes), as for an attestation
    obtained before a grant was revoked."""
    now = datetime.now(timezone.utc) - timedelta(minutes=issued_ago)
    body = {"schema": ed.ATTESTATION_SCHEMA, "issuer": wi.ISSUER, "audience": wi.AUDIENCE, "grantId": "rag_e",
            "ownerScope": owner, "grantee": grantee, "role": role, "projectId": project, "authentication": "test",
            "issuedAt": now.isoformat(), "expiresAt": (now + timedelta(minutes=minutes)).isoformat()}
    return sign(ed.ATTESTATION_SCHEMA, body, *([key] if key else []))


def pin(session, oid):
    return ed.current_pin(session, session.get(md.ResearchObject, oid))


def approve(session, oid, dims=tuple(cv.APPROVAL_ACTIONS), approver="workspace-user:reviewer"):
    obj = session.get(md.ResearchObject, oid)
    subject = cv.subject_ref(obj, cv._provenance(session, obj))
    for dim in dims:
        cv.record_signed_approval(session, owner_scope=OWNER, object_id=oid, dimension=dim,
                                  record=signed_record(subject, dim, owner=OWNER, approver=approver), key=PUB)


def reproduce(session, rid, run="run-julia", gid="gj"):
    return wi.ingest_reproduction(session, key=PUB, original_id=rid, grant=grant(run=run, grant_id=gid), rel_tol=1e-9,
                                  abs_tol=1e-12, fetch=lambda _: export(run=run, grant_id=gid))


@pytest.fixture
def world(session):
    out = ingest(session, grant(), export())
    o = {k.split(":")[-1] if k.startswith("output:") else k: v["id"] for k, v in out["objects"].items()}
    ed.create_article(session, OWNER_ACTOR, area_slug=AREA, project_key="unused", workspace_project_id="proj-1",
                      slug=SLUG, title="Synthetic enhanced-weathering study")
    return o


def refs(session, o, *roles):
    table = {"result": ("polyglot-result", {}), "figure": ("fig", {}), "study-package": ("run", {"packageId": "study_1"}),
             "dataset": ("dataset:ds-1:r1", {}), "model": ("model", {})}
    return [{"objectId": o[table[r][0]], "role": r, "pin": pin(session, o[table[r][0]]), **table[r][1]} for r in roles]


def write(session, o, roles=("result", "figure", "study-package"), base=0, actor=OWNER_ACTOR, **kw):
    return ed.add_revision(session, actor, slug=SLUG, base_revision=base, title=kw.get("title", "Synthetic ERW study"),
                           summary="Synthetic data only.", sections=[{"heading": "Overview", "body": "Modelled values."}],
                           references=kw.get("references", refs(session, o, *roles)))


def publish_area(session):
    area = ed._area(session, AREA)
    subject = ed.area_subject_ref(area)
    ed.publish_area(session, OWNER_ACTOR, slug=AREA, record=signed_record(subject, "area-publication"), key=PUB)


def make_publishable(session, o):
    reproduce(session, o["polyglot-result"])
    for oid in (o["polyglot-result"], o["fig"], o["run"]):
        approve(session, oid)
        cv.promote_public_projection(session, owner_scope=OWNER, object_id=oid, signed_only=True)


# --- actors -----------------------------------------------------------------


def test_editor_needs_a_valid_workspace_attestation():
    assert ed.resolve_actor(OWNER).role == "owner"
    editor = ed.resolve_actor(OWNER, "workspace-user:ed", attestation(), key=PUB)
    assert (editor.role, editor.project_ref) == ("editor", "proj-1")
    for bad, match in ((None, "attestation"), (attestation(owner="workspace-user:b"), "another"),
                       (attestation(grantee="workspace-user:x"), "another"), (attestation(role="approver:rights"), "another"),
                       (attestation(minutes=-1), "expired")):
        with pytest.raises(PermissionError, match=match):
            ed.resolve_actor(OWNER, "workspace-user:ed", bad, key=PUB)
    with pytest.raises(ValueError, match="signature"):
        ed.resolve_actor(OWNER, "workspace-user:ed", attestation(key=OTHER), key=PUB)


def test_attestation_times_are_checked_and_recorded():
    editor = ed.resolve_actor(OWNER, "workspace-user:ed", attestation(issued_ago=2), key=PUB)
    assert 110 <= (datetime.now(timezone.utc) - editor.attested_at).total_seconds() <= 130
    assert editor == ed.Actor("workspace-user:ed", "editor", OWNER, "proj-1")  # the issue time is not identity
    for bad in (attestation(issued_ago=-5), attestation(minutes=24 * 60)):  # future-dated; longer than Workspace's TTL
        with pytest.raises(PermissionError, match="future or valid for too long"):
            ed.resolve_actor(OWNER, "workspace-user:ed", bad, key=PUB)
    body = {k: v for k, v in attestation().items() if k != "signature"}
    for issued in (None, "2026-11-13T00:00:00"):  # missing; no timezone
        undated = sign(ed.ATTESTATION_SCHEMA, {**body, "issuedAt": issued})
        with pytest.raises(PermissionError, match="no valid issue time"):
            ed.resolve_actor(OWNER, "workspace-user:ed", undated, key=PUB)


def test_editor_writes_need_an_attestation_inside_the_write_window():
    """F2: Workspace refuses a revoked grant a new attestation, so a revoked editor stops writing within the window."""
    window = ed.EDITOR_WRITE_WINDOW_SECONDS
    assert 0 < window <= 300
    fresh = ed.resolve_actor(OWNER, "workspace-user:ed", attestation(), key=PUB)
    ed.require_fresh_for_write(fresh)
    late = fresh.attested_at + timedelta(seconds=window + 1)
    with pytest.raises(PermissionError, match="request a new one from Workspace"):
        ed.require_fresh_for_write(fresh, now=late)
    ed.require_fresh_for_write(OWNER_ACTOR, now=late)  # the owner's own credential is not an attestation
    with pytest.raises(PermissionError):  # an editor built without an issue time never writes
        ed.require_fresh_for_write(ed.Actor("workspace-user:ed", "editor", OWNER, "proj-1"))


def test_editor_scope_is_the_granted_workspace_project(session, world):
    editor = ed.Actor("workspace-user:ed", "editor", OWNER, "proj-1")
    out = write(session, world, actor=editor)
    assert out["revision"] == 1
    stranger = ed.Actor("workspace-user:ed", "editor", OWNER, "proj-other")
    with pytest.raises(PermissionError, match="does not cover"):
        write(session, world, base=1, actor=stranger)
    with pytest.raises(PermissionError, match="owner"):
        ed.retract_article(session, editor, slug=SLUG, reason="x")
    with pytest.raises(PermissionError, match="owner"):
        ed.create_article(session, editor, area_slug=AREA, project_key="k", workspace_project_id="proj-1",
                          slug="other-article", title="T")


def test_other_owners_cannot_see_or_adopt_the_article(session, world):
    b = ed.Actor("workspace-user:b", "owner", "workspace-user:b", "*")
    with pytest.raises(LookupError):
        ed.article_view(session, "workspace-user:b", SLUG)
    with pytest.raises(ed.Conflict):
        ed.create_article(session, b, area_slug=AREA, project_key="k", workspace_project_id="proj-1", slug=SLUG, title="T")


# --- revisions and references -----------------------------------------------------


def test_revisions_are_immutable_hash_identified_registry_objects(session, world):
    one = write(session, world)
    obj = session.get(md.ResearchObject, one["objectId"])
    assert obj.kind == "research-article" and obj.source_version == "r1" and obj.source_sha256 == one["documentSha256"]
    assert one["subjectRef"] == f"research-object:{obj.id}#{cv._provenance(session, obj).record_sha256}"
    assert {r["status"] for r in one["references"]} == {"verified"}
    with pytest.raises(ed.Conflict, match="head is revision 1"):
        write(session, world, base=0)
    two = write(session, world, base=1, title="Revised")
    assert two["revision"] == 2 and two["documentSha256"] != one["documentSha256"]
    view = ed.article_view(session, OWNER, SLUG)
    assert [r["revision"] for r in view["revisions"]] == [1, 2] and view["head"]["title"] == "Revised"


@pytest.mark.parametrize("mutate,problem", [
    (lambda r, o, s: r.update(pin="0" * 64), "unverifiable:pin-does-not-match-provenance"),
    (lambda r, o, s: r.update(role="figure"), "unverifiable:kind-does-not-match-role"),
    (lambda r, o, s: r.update(role="study-package", packageId="study_missing", objectId=o["run"], pin=pin(s, o["run"])),
     "unverifiable:study-package-not-verified"),
])
def test_unverifiable_references_are_refused(session, world, mutate, problem):
    ref = refs(session, world, "result")[0]
    mutate(ref, world, session)
    with pytest.raises(PermissionError, match=problem.split(":")[-1]):
        write(session, world, references=[ref])


def test_foreign_and_unknown_objects_are_unverifiable(session, world):
    other = ingest(session, grant(owner="b", run="run-b", grant_id="gb"), export(owner="b", run="run-b", grant_id="gb"),
                   entry="ws-study-b")
    oid = other["objects"]["output:polyglot-result"]["id"]
    ref = {"objectId": oid, "role": "result", "pin": "a" * 64}
    with pytest.raises(PermissionError, match="not-found-or-foreign"):
        write(session, world, references=[ref])


def test_withdrawn_outputs_are_refused(session, world):
    notice = sign(wi.WITHDRAWAL_SCHEMA, {"schema": wi.WITHDRAWAL_SCHEMA, "issuer": wi.ISSUER, "audience": wi.AUDIENCE,
                                         "ownerScope": OWNER, "projectId": "proj-1", "runId": "run-1",
                                         "withdrawnAt": "2026-11-10T00:00:00Z", "reason": "test"})
    wi.apply_withdrawal(session, key=PUB, notice=notice)
    with pytest.raises(PermissionError, match="withdrawn"):
        write(session, world, roles=("result",))


def test_superseded_source_revisions_are_refused(session, world):
    old = refs(session, world, "result")
    # The same Workspace run re-exported in a new state is a newer revision of the same source.
    ingest(session, grant(grant_id="g2"), export(grant_id="g2", repro="d" * 64))
    with pytest.raises(PermissionError, match="superseded"):
        write(session, world, references=old)


def test_stale_notebook_claims_are_refused(session, world):
    from tests.test_sc_nb01_upstream_lineage import scenario
    stale = scenario(session, current=False, head=2, changed=["fit"])
    oid = stale["objects"]["output:polyglot-result"]["id"]
    ref = {"objectId": oid, "role": "result", "pin": pin(session, oid)}
    with pytest.raises(PermissionError, match="stale"):
        write(session, world, references=[ref])


def test_submission_requires_independent_reproduction(session, world):
    write(session, world)
    with pytest.raises(PermissionError, match="insufficiently-reproduced"):
        ed.submit_revision(session, OWNER_ACTOR, slug=SLUG, revision=1)
    reproduce(session, world["polyglot-result"])
    out = ed.submit_revision(session, OWNER_ACTOR, slug=SLUG, revision=1)
    assert out["subjectRef"].startswith("research-object:") and {r["status"] for r in out["references"]} == {"verified"}
    # The study-package run is reproduced through the result it produced.
    assert cv.reproduction_status(session, session.get(md.ResearchObject, world["run"])) == "reproduced"


def test_unverified_input_lineage_is_insufficient(session, world):
    inline = ingest(session, grant(run="run-inline", grant_id="g-inl"),
                    export(run="run-inline", grant_id="g-inl", lineage="inline-unverified", bindings=[]))
    oid = inline["objects"]["output:polyglot-result"]["id"]
    write(session, world, references=[{"objectId": oid, "role": "result", "pin": pin(session, oid)}])
    with pytest.raises(PermissionError, match="input-lineage-unverified"):
        ed.submit_revision(session, OWNER_ACTOR, slug=SLUG, revision=1)


# --- approvals and publication --------------------------------------------------------


def submitted(session, world):
    write(session, world)
    make_publishable(session, world)
    return ed.submit_revision(session, OWNER_ACTOR, slug=SLUG, revision=1)


def approve_article(session, subject, dims=tuple(cv.APPROVAL_ACTIONS), revision=1):
    for dim in dims:
        ed.record_article_approval(session, OWNER_ACTOR, slug=SLUG, revision=revision, dimension=dim,
                                   record=signed_record(subject, dim), key=PUB)


def test_full_editorial_publication(session, world):
    sub = submitted(session, world)
    with pytest.raises(PermissionError, match="area"):
        ed.publish_revision(session, OWNER_ACTOR, slug=SLUG, revision=1)
    publish_area(session)
    approve_article(session, sub["subjectRef"], dims=("scientific", "rights", "ownership"))
    with pytest.raises(PermissionError, match="publication"):
        ed.publish_revision(session, OWNER_ACTOR, slug=SLUG, revision=1)
    approve_article(session, sub["subjectRef"], dims=("publication",))
    out = ed.publish_revision(session, OWNER_ACTOR, slug=SLUG, revision=1)
    assert out["publishedRevision"] == 1 and out["state"] == "published"
    page = ed.public_article(session, SLUG)
    assert page["title"] == "Synthetic ERW study" and page["sections"][0]["heading"] == "Overview"
    assert {r["role"] for r in page["references"] if r["status"] == "available"} == {"result", "study-package"}
    # With no figure release channel the figure is never released, so it is not public (SC-FIG-03A).
    assert [r["status"] for r in page["references"] if r["role"] == "figure"] == ["unavailable"]
    assert page["classificationNotice"] and "not measurements" in page["classificationNotice"]
    text = str(page)
    for private in (OWNER, "proj-1", "rag_", "approval_", world["polyglot-result"], "pin"):
        assert private not in text
    integrity = ed.editorial_integrity(session)
    assert integrity["consistent"], integrity
    actions = [e["action"] for e in ed.audit_log(session, OWNER, SLUG)]
    assert actions[-1] == "article-published" and actions.count("approval-recorded") == 4


def test_unsigned_or_mismatched_approvals_do_not_count(session, world):
    sub = submitted(session, world)
    with pytest.raises(ValueError, match="signature"):
        ed.record_article_approval(session, OWNER_ACTOR, slug=SLUG, revision=1, dimension="scientific",
                                   record=signed_record(sub["subjectRef"], "scientific", key=OTHER), key=PUB)
    with pytest.raises(ValueError, match="Self-approval"):
        ed.record_article_approval(session, OWNER_ACTOR, slug=SLUG, revision=1, dimension="scientific",
                                   record=signed_record(sub["subjectRef"], "scientific", approver=OWNER), key=PUB)
    with pytest.raises(ValueError, match="approves 'rights'"):
        ed.record_article_approval(session, OWNER_ACTOR, slug=SLUG, revision=1, dimension="scientific",
                                   record=signed_record(sub["subjectRef"], "rights"), key=PUB)
    # An unsigned record recorded in-process (legacy path) is not counted by editorial publication.
    obj_id = ed._revision(session, ed._entry(session, OWNER, SLUG)[0], 1).object_id
    from tests.test_sc_model01_convergence import governance
    for dim, action in cv.APPROVAL_ACTIONS.items():
        a, d = governance(sub["subjectRef"], action)
        cv.record_workspace_approval(session, owner_scope=OWNER, object_id=obj_id, dimension=dim, approval=a, decision=d)
    publish_area(session)
    with pytest.raises(PermissionError, match="Missing signed"):
        ed.publish_revision(session, OWNER_ACTOR, slug=SLUG, revision=1)


def test_new_revision_withdraws_submission_and_old_approvals_do_not_transfer(session, world):
    sub = submitted(session, world)
    approve_article(session, sub["subjectRef"])
    write(session, world, base=1, title="Changed after approval")
    publish_area(session)
    with pytest.raises(ed.Conflict, match="in review"):
        ed.publish_revision(session, OWNER_ACTOR, slug=SLUG, revision=1)
    sub2 = ed.submit_revision(session, OWNER_ACTOR, slug=SLUG, revision=2)
    assert sub2["subjectRef"] != sub["subjectRef"]
    with pytest.raises(PermissionError, match="Missing signed"):
        ed.publish_revision(session, OWNER_ACTOR, slug=SLUG, revision=2)


def test_publication_rechecks_references(session, world):
    sub = submitted(session, world)
    approve_article(session, sub["subjectRef"])
    publish_area(session)
    # Between review and publication the run is withdrawn by its Workspace owner.
    notice = sign(wi.WITHDRAWAL_SCHEMA, {"schema": wi.WITHDRAWAL_SCHEMA, "issuer": wi.ISSUER, "audience": wi.AUDIENCE,
                                         "ownerScope": OWNER, "projectId": "proj-1", "runId": "run-1",
                                         "withdrawnAt": "2026-11-10T00:00:00Z", "reason": "test"})
    wi.apply_withdrawal(session, key=PUB, notice=notice)
    with pytest.raises(PermissionError, match="withdrawn"):
        ed.publish_revision(session, OWNER_ACTOR, slug=SLUG, revision=1)
    assert ed.public_article(session, SLUG) is None


def test_unapproved_reference_blocks_publication(session, world):
    write(session, world, roles=("result", "figure", "dataset"))
    make_publishable(session, world)
    sub = ed.submit_revision(session, OWNER_ACTOR, slug=SLUG, revision=1)
    approve_article(session, sub["subjectRef"])
    publish_area(session)
    with pytest.raises(PermissionError, match="dataset .*not-publicly-approved"):
        ed.publish_revision(session, OWNER_ACTOR, slug=SLUG, revision=1)


def publish_revision_n(session, world, n, title):
    write(session, world, base=n - 1, title=title)
    sub = ed.submit_revision(session, OWNER_ACTOR, slug=SLUG, revision=n)
    approve_article(session, sub["subjectRef"], revision=n)
    return ed.publish_revision(session, OWNER_ACTOR, slug=SLUG, revision=n)


def test_update_rollback_and_retraction(session, world):
    make_publishable(session, world)
    publish_area(session)
    publish_revision_n(session, world, 1, "First")
    out = publish_revision_n(session, world, 2, "Second")
    assert out["state"] == "updated" and ed.public_article(session, SLUG)["title"] == "Second"
    editor = ed.Actor("workspace-user:ed", "editor", OWNER, "proj-1")
    with pytest.raises(PermissionError):
        ed.rollback_article(session, editor, slug=SLUG, revision=1, reason="x")
    ed.rollback_article(session, OWNER_ACTOR, slug=SLUG, revision=1, reason="Second revision had an error")
    page = ed.public_article(session, SLUG)
    assert page["title"] == "First" and page["revision"] == 1
    write(session, world, base=2, title="Never released")
    with pytest.raises(ed.Conflict, match="previously released"):
        ed.rollback_article(session, OWNER_ACTOR, slug=SLUG, revision=3, reason="x")
    ed.retract_article(session, OWNER_ACTOR, slug=SLUG, reason="Retracted for correction")
    assert ed.public_article(session, SLUG) is None and ws.approved_workspace_view(session, SLUG) is None
    assert ed.editorial_integrity(session)["consistent"]
    with pytest.raises(ed.Conflict):
        ed.rollback_article(session, OWNER_ACTOR, slug=SLUG, revision=2, reason="x")
    actions = [e["action"] for e in ed.audit_log(session, OWNER, SLUG)]
    assert actions.count("article-published") == 2 and "article-rolled-back" in actions and actions[-1] == "article-retracted"


# --- areas -----------------------------------------------------------------------------


def test_area_publication_is_signed_bound_to_content_and_single_use(session, world):
    area = ed._area(session, AREA)
    subject = ed.area_subject_ref(area)
    b = ed.Actor("workspace-user:b", "owner", "workspace-user:b", "*")
    with pytest.raises(PermissionError, match="steward"):
        ed.publish_area(session, b, slug=AREA, record=signed_record(subject, "area-publication", owner=b.owner), key=PUB)
    with pytest.raises(ValueError, match="area-publication"):
        ed.publish_area(session, OWNER_ACTOR, slug=AREA, record=signed_record(subject, "publication"), key=PUB)
    record = signed_record(subject, "area-publication")
    ed.publish_area(session, OWNER_ACTOR, slug=AREA, record=record, key=PUB)
    with pytest.raises(ed.Conflict, match="Retract"):
        ed.update_area(session, OWNER_ACTOR, slug=AREA, title="Renamed", summary="")
    ed.retract_area(session, OWNER_ACTOR, slug=AREA, reason="rename")
    with pytest.raises(ed.Conflict, match="already used"):
        ed.publish_area(session, OWNER_ACTOR, slug=AREA, record=record, key=PUB)
    ed.update_area(session, OWNER_ACTOR, slug=AREA, title="Renamed", summary="")
    with pytest.raises(ValueError, match="current title"):
        ed.publish_area(session, OWNER_ACTOR, slug=AREA, record=signed_record(subject, "area-publication"), key=PUB)
    ed.publish_area(session, OWNER_ACTOR, slug=AREA,
                    record=signed_record(ed.area_subject_ref(area), "area-publication"), key=PUB)


def test_area_retraction_hides_published_articles(session, world):
    make_publishable(session, world)
    publish_area(session)
    publish_revision_n(session, world, 1, "First")
    assert ed.public_article(session, SLUG) is not None
    ed.retract_area(session, OWNER_ACTOR, slug=AREA, reason="review")
    assert ed.public_article(session, SLUG) is None


# --- integrity ---------------------------------------------------------------------------


def test_direct_database_publication_is_detected(session, world):
    entry = ed._entry(session, OWNER, SLUG)[0]
    entry.visibility, entry.rights_cleared = md.APPROVED, True
    ed._area(session, AREA).visibility = md.APPROVED
    session.flush()
    report = ed.editorial_integrity(session)
    assert not report["consistent"]
    assert {f["finding"] for f in report["findings"]} == {"public-state-not-explained-by-audit-chain",
                                                          "public-area-without-signed-publication"}
    # And no article body is served without an editorial release.
    assert ed.public_article(session, SLUG) is None


def test_audit_chain_detects_tampering(session, world):
    write(session, world)
    assert ed.verify_chain(session)["valid"]
    row = session.scalars(ed.select(ed.EditorialEvent).order_by(ed.EditorialEvent.seq)).all()[1]
    body = deepcopy(row.body)
    body["actor"] = "workspace-user:someone-else"
    row.body = body
    session.flush()
    assert ed.verify_chain(session) == {"valid": False, "events": 2, "brokenAt": 2}


# --- HTTP -------------------------------------------------------------------------------


def test_http_editorial_routes(monkeypatch, tmp_path):
    import base64
    import json
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.main import app
    from tests.test_sc_model01_workspace_ingest import pub_file
    engine = create_engine("sqlite+pysqlite:///" + str(tmp_path / "ed.sqlite"))
    ws.Base.metadata.create_all(engine)
    monkeypatch.setattr(ws, "_session_factory", lambda: Session(engine, expire_on_commit=False))
    client = TestClient(app)
    assert client.get("/api/research/v1/editorial/articles").status_code == 503  # writes gate closed by default
    token = tmp_path / "t"
    token.write_text("staging-secret")
    for k, v in {"RESEARCH_CONVERGENCE_TOKEN_FILE": str(token), "RESEARCH_CONVERGENCE_WRITES": "staging-service-token",
                 "RESEARCH_ENV": "staging", "RESEARCH_WORKSPACE_PUBLIC_KEY_FILE": pub_file(tmp_path, PUB)}.items():
        monkeypatch.setenv(k, v)
    owner = {"Authorization": "Bearer staging-secret", "X-SC-Owner-Scope": OWNER}
    assert client.get("/api/research/v1/editorial/articles", headers={"X-SC-Owner-Scope": OWNER}).status_code == 401
    r = client.post("/api/research/v1/editorial/articles", headers=owner, json={
        "areaSlug": "synthetic-area", "projectKey": "syn-proj", "workspaceProjectId": "proj-1",
        "slug": "syn-article", "title": "Synthetic article"})
    assert r.status_code == 200 and r.json()["created"] is True, r.text
    body = {"baseRevision": 0, "title": "Synthetic article", "summary": "", "references": [],
            "sections": [{"heading": "Overview", "body": "Synthetic."}]}
    assert client.post("/api/research/v1/editorial/articles/syn-article/revisions", headers=owner, json=body).status_code == 200
    assert client.post("/api/research/v1/editorial/articles/syn-article/revisions", headers=owner, json=body).status_code == 409
    att = base64.urlsafe_b64encode(json.dumps(attestation()).encode()).decode().rstrip("=")
    editor = {**owner, "X-SC-Actor-Scope": "workspace-user:ed", "X-SC-Editor-Attestation": att}
    r = client.post("/api/research/v1/editorial/articles/syn-article/revisions", headers=editor, json={**body, "baseRevision": 1})
    assert r.status_code == 200 and r.json()["revision"] == 2, r.text
    assert client.post("/api/research/v1/editorial/articles/syn-article/revisions",
                       headers={**owner, "X-SC-Actor-Scope": "workspace-user:ed"}, json={**body, "baseRevision": 2}).status_code == 403
    assert client.post("/api/research/v1/editorial/articles/syn-article/retract", headers=editor, json={"reason": "x"}).status_code == 403
    other = {**owner, "X-SC-Owner-Scope": "workspace-user:b"}
    assert client.get("/api/research/v1/editorial/articles/syn-article", headers=other).status_code == 404
    view = client.get("/api/research/v1/editorial/articles/syn-article", headers=owner).json()
    assert view["headRevision"] == 2 and view["revisions"][1]["authorRole"] == "editor"
    assert client.post("/api/research/v1/editorial/articles/syn-article/revisions/2/approvals/scientific",
                       headers=owner, json={"approval": {}, "decision": {}}).status_code == 422
    assert client.get("/api/research/v1/articles/syn-article/content").status_code == 404
    audit = client.get("/api/research/v1/editorial/audit?article=syn-article", headers=owner).json()
    assert audit["chain"]["valid"] and [e["action"] for e in audit["events"]][-1] == "revision-created"
    assert client.get("/api/research/v1/editorial/integrity", headers=owner).json()["consistent"] is True
    # F2: an attestation obtained before revocation still reads, but no longer writes, once past the window
    stale = base64.urlsafe_b64encode(json.dumps(attestation(issued_ago=6, minutes=60)).encode()).decode().rstrip("=")
    late = {**editor, "X-SC-Editor-Attestation": stale}
    assert client.get("/api/research/v1/editorial/articles/syn-article", headers=late).status_code == 200
    r = client.post("/api/research/v1/editorial/articles/syn-article/revisions", headers=late, json={**body, "baseRevision": 2})
    assert r.status_code == 403 and "request a new one" in r.text


def test_private_console_is_not_served_by_the_public_reader(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    assert client.get("/api/research/v1/editorial/ui/index.html").status_code == 404
    token = tmp_path / "t"
    token.write_text("staging-secret")
    for k, v in {"RESEARCH_CONVERGENCE_TOKEN_FILE": str(token), "RESEARCH_CONVERGENCE_WRITES": "staging-service-token",
                 "RESEARCH_ENV": "staging"}.items():
        monkeypatch.setenv(k, v)
    page = client.get("/api/research/v1/editorial/ui/index.html")
    assert page.status_code == 200 and "script-src 'self'" in page.headers["content-security-policy"]
    assert client.get("/api/research/v1/editorial/ui/app.js").status_code == 200
    assert client.get("/api/research/v1/editorial/ui/../main.py").status_code == 404
