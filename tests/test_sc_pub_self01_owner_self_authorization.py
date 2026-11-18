"""SC-PUB-SELF-01: Research accepts a Workspace owner's self-authorization only where configured, only when it is
labelled as one throughout and backed by a fresh step-up, and always discloses it publicly as not independently
reviewed (synthetic fixtures only; public CI)."""
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from tests.test_sc_ed01_editorial import (  # noqa: F401 (session and world are fixtures)
    AREA, OWNER, OWNER_ACTOR, SLUG, approve_article, make_publishable, session, world, write,
)
from tests.test_sc_model01_convergence import governance
from tests.test_sc_model01_workspace_ingest import KEY, PUB, sign
from app import convergence as cv  # noqa: E402
from app import editorial as ed  # noqa: E402
from app import object_registry as md  # noqa: E402

DECLARATION = "Checked the data, methods, figures and rights myself."


def self_record(subject, dimension, owner=OWNER, *, actor=None, review=None, decision=None, auth=None, age=10):
    """Shaped like Workspace's owner self-authorization record (signed by the test key)."""
    a, d = governance(subject, cv.APPROVAL_ACTIONS.get(dimension, "research_area_publication"),
                      approver=actor or owner, requester=owner)
    unique = uuid4().hex[:12]
    a["approvalId"] = d["approvalId"] = f"approval_{unique}"
    a["decisionRef"] = d["decisionId"] = f"decision_{unique}"
    d["actor"]["role"] = cv.OWNER_SELF_ROLE
    d.update({"reviewKind": cv.OWNER_SELF_KIND, "independentReview": False, **(decision or {})})
    now = datetime.now(timezone.utc)
    return sign(cv.WS_APPROVAL_RECORD_SCHEMA, {
        "schema": cv.WS_APPROVAL_RECORD_SCHEMA, "issuer": "sustainable-catalyst-workspace", "audience": "sc-research",
        "ownerScope": owner, "projectId": "proj-1", "dimension": dimension, "action": a["action"],
        "subjectRef": subject, "grantId": cv.OWNER_SELF_ROLE,
        "authentication": {"requester": "sc-identity-oidc-session", "decider": cv.STEP_UP_DECIDER_METHOD,
                           "stepUp": True, "deciderAuthTime": int(now.timestamp()) - age, "deciderAmr": ["pwd", "otp"],
                           "revisionConfirmed": True, **(auth or {})},
        "review": {"kind": cv.OWNER_SELF_KIND, "independent": False, "statement": "x", "attestation": DECLARATION,
                   **(review or {})},
        "approval": a, "decision": d, "issuedAt": now.isoformat()}, KEY)


@pytest.fixture
def accepted(monkeypatch):
    monkeypatch.setenv(cv.SELF_AUTHORIZATION_POLICY_ENV, "accepted")


def subject_of(session, oid):
    obj = session.get(md.ResearchObject, oid)
    return cv.subject_ref(obj, cv._provenance(session, obj))


def self_approve(session, oid, dims=tuple(cv.APPROVAL_ACTIONS)):
    for dim in dims:
        cv.record_signed_approval(session, owner_scope=OWNER, object_id=oid, dimension=dim,
                                  record=self_record(subject_of(session, oid), dim), key=PUB)


def test_refused_unless_this_service_accepts_owner_self_authorization(session, world):
    with pytest.raises(PermissionError, match="does not accept owner self-authorization"):
        self_approve(session, world["run"], dims=("scientific",))


@pytest.mark.parametrize("kw,error,match", [
    ({"review": {"independent": True}}, ValueError, "labelled"),
    ({"review": {"kind": "separate-reviewer"}}, ValueError, "labelled"),
    ({"decision": {"reviewKind": None}}, ValueError, "labelled"),
    ({"decision": {"independentReview": True}}, ValueError, "labelled"),
    ({"review": {"attestation": "  "}}, ValueError, "declaration"),
    ({"auth": {"revisionConfirmed": False}}, ValueError, "exact revision"),
    ({"auth": {"stepUp": False}}, PermissionError, "step-up"),
    ({"age": 3600}, PermissionError, "not fresh"),
    ({"actor": "workspace-user:someone-else"}, PermissionError, "Only the Workspace owner"),
])
def test_every_element_of_a_self_authorization_is_required(session, world, accepted, kw, error, match):
    with pytest.raises(error, match=match):
        cv.record_signed_approval(session, owner_scope=OWNER, object_id=world["run"], dimension="scientific",
                                  record=self_record(subject_of(session, world["run"]), "scientific", **kw), key=PUB)


def test_unlabelled_self_approval_is_still_prohibited(session, world, accepted):
    from tests.test_sc_model01_convergence import signed_record
    with pytest.raises(ValueError, match="Self-approval is prohibited"):
        cv.record_signed_approval(session, owner_scope=OWNER, object_id=world["run"], dimension="scientific",
                                  record=signed_record(subject_of(session, world["run"]), "scientific", owner=OWNER,
                                                       approver=OWNER),
                                  key=PUB)
    # Nor can the self-authorization role be recorded outside a verified signed record.
    a, d = governance(subject_of(session, world["run"]), "research_scientific_review", approver=OWNER)
    d["actor"]["role"] = cv.OWNER_SELF_ROLE
    with pytest.raises(ValueError, match="verified, labelled signed record"):
        cv.record_workspace_approval(session, owner_scope=OWNER, object_id=world["run"], dimension="scientific",
                                     approval=a, decision=d)


def test_self_authorized_publication_is_disclosed_as_author_reviewed(session, world, accepted, monkeypatch):
    import tests.test_sc_ed01_editorial as e
    monkeypatch.setattr(e, "approve", lambda s, oid, **_: self_approve(s, oid))
    write(session, world)
    make_publishable(session, world)
    sub = ed.submit_revision(session, OWNER_ACTOR, slug=SLUG, revision=1)
    area = ed._area(session, AREA)
    ed.publish_area(session, OWNER_ACTOR, slug=AREA, record=self_record(ed.area_subject_ref(area), "area-publication"), key=PUB)
    for dim in cv.APPROVAL_ACTIONS:
        ed.record_article_approval(session, OWNER_ACTOR, slug=SLUG, revision=1, dimension=dim,
                                   record=self_record(sub["subjectRef"], dim), key=PUB)
    assert ed.publish_revision(session, OWNER_ACTOR, slug=SLUG, revision=1)["state"] == "published"
    page = ed.public_article(session, SLUG)
    assert page["reviewDisclosure"]["status"] == "author-reviewed" and page["reviewDisclosure"]["independentReview"] is False
    assert page["reviewDisclosure"]["selfAuthorizedDimensions"] == sorted(cv.APPROVAL_ACTIONS)
    assert cv.SELF_AUTHORIZED_STATEMENT in page["classificationNotice"] and "not measurements" in page["classificationNotice"]
    assert len(page["classificationNotice"]) <= 300  # the WordPress bridge keeps 300 characters
    obj = session.get(md.ResearchObject, world["run"])
    prov = cv.public_provenance(obj, cv._provenance(session, obj), cv.review_disclosure(session, obj))
    assert prov["reviewDisclosure"]["status"] == "author-reviewed"
    assert OWNER not in str(page) and DECLARATION not in str(page)  # the declaration stays in the signed record
    actions = [ev for ev in ed.audit_log(session, OWNER, SLUG) if ev["action"] == "approval-recorded"]
    assert len(actions) == 4


def test_other_approvers_are_not_described_as_author_review(session, world):
    from tests.test_sc_ed01_editorial import publish_area
    write(session, world)
    make_publishable(session, world)
    sub = ed.submit_revision(session, OWNER_ACTOR, slug=SLUG, revision=1)
    publish_area(session)
    approve_article(session, sub["subjectRef"])
    ed.publish_revision(session, OWNER_ACTOR, slug=SLUG, revision=1)
    page = ed.public_article(session, SLUG)
    assert page["reviewDisclosure"]["status"] == "reviewed-by-other-approvers"
    assert cv.SELF_AUTHORIZED_STATEMENT not in (page["classificationNotice"] or "")


def test_area_self_authorization_needs_the_policy(session, world):
    area = ed._area(session, AREA)
    with pytest.raises(PermissionError, match="does not accept"):
        ed.publish_area(session, OWNER_ACTOR, slug=AREA, record=self_record(ed.area_subject_ref(area), "area-publication"),
                        key=PUB)
