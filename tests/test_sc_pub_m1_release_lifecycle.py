"""SC-PUB-M1 release lifecycle: unlist, relist, update, withdraw (tombstone), receipts, step-up evidence.

Synthetic fixtures only (public CI). Real Workspace-issued, step-up-bound records are exercised in
the isolated staging acceptance.
"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

from tests.test_sc_ed01_editorial import (  # noqa: F401 (session/world are fixtures)
    AREA, OWNER, OWNER_ACTOR, SLUG, approve_article, make_publishable, publish_area, publish_revision_n, session,
    world, write,
)
from tests.test_sc_model01_convergence import governance
from tests.test_sc_model01_workspace_ingest import KEY, PUB, sign
from app import convergence as cv  # noqa: E402
from app import editorial as ed  # noqa: E402
from app import object_registry as md  # noqa: E402
from app import workspaces as ws  # noqa: E402

EDITOR = ed.Actor("workspace-user:ed", "editor", OWNER, "proj-1")


def live(session, world):
    make_publishable(session, world)
    publish_area(session)
    return publish_revision_n(session, world, 1, "First")


def catalog_slugs(session):
    return {a["slug"] for a in ws.approved_catalog_from_database(session)[1]}


def listed_objects(session, **kw):
    return {o.id for o, *_ in session.execute(md.public_query(**kw))}


def test_publish_returns_a_receipt_bound_to_the_audit_chain(session, world):
    out = live(session, world)
    receipt = out["receipt"]
    assert receipt["action"] == "article-published" and receipt["revision"] == 1 and out["publicationStatus"] == "published"
    assert set(receipt["approvalRecords"]) == set(cv.APPROVAL_ACTIONS)
    head = ed.audit_log(session, OWNER, SLUG)[-1]
    assert head["eventSha256"] == receipt["eventSha256"] and head["prev"] == receipt["prevSha256"]
    assert ed.verify_chain(session)["valid"]


def test_unlisted_article_is_served_by_link_but_not_discoverable(session, world):
    live(session, world)
    assert SLUG in catalog_slugs(session) and world["polyglot-result"] in listed_objects(session)
    out = ed.unlist_article(session, OWNER_ACTOR, slug=SLUG, reason="Owner review of wording")
    assert out["publicationStatus"] == "unlisted" and out["receipt"]["listed"] is False
    page = ed.public_article(session, SLUG)
    assert page["listed"] is False and page["title"] == "First"
    # Its own outputs still resolve by link (the figure is unreleased here: no figure channel; SC-FIG-03A).
    assert {r["status"] for r in page["references"] if r["role"] != "figure"} == {"available"}
    assert SLUG not in catalog_slugs(session)
    assert world["polyglot-result"] not in listed_objects(session)
    assert world["polyglot-result"] in listed_objects(session, article=SLUG)
    key = session.get(md.ResearchObject, world["polyglot-result"]).public_key
    assert md.public_object(key, session)["id"] == key  # the article's object links keep working
    assert ed.editorial_integrity(session)["consistent"]
    with pytest.raises(ed.Conflict):
        ed.unlist_article(session, OWNER_ACTOR, slug=SLUG, reason="again")
    ed.relist_article(session, OWNER_ACTOR, slug=SLUG, reason="Wording confirmed")
    assert SLUG in catalog_slugs(session) and ed.public_article(session, SLUG)["listed"] is True
    assert ed.editorial_integrity(session)["consistent"]


def test_update_while_unlisted_stays_unlisted(session, world):
    live(session, world)
    ed.unlist_article(session, OWNER_ACTOR, slug=SLUG, reason="hold")
    out = publish_revision_n(session, world, 2, "Second")
    assert out["publicationStatus"] == "unlisted" and out["receipt"]["listed"] is False
    page = ed.public_article(session, SLUG)
    assert page["title"] == "Second" and page["listed"] is False and SLUG not in catalog_slugs(session)
    ed.rollback_article(session, OWNER_ACTOR, slug=SLUG, revision=1, reason="Second had an error")
    assert ed.public_article(session, SLUG)["title"] == "First"
    assert ed.editorial_integrity(session)["consistent"]


def test_withdrawal_is_final_and_leaves_only_a_tombstone(session, world):
    live(session, world)
    with pytest.raises(PermissionError):
        ed.withdraw_article(session, EDITOR, slug=SLUG, reason="x", public_notice="x")
    out = ed.withdraw_article(session, OWNER_ACTOR, slug=SLUG, reason="Synthetic withdrawal drill (private reason)",
                              public_notice="Withdrawn by the author.")
    assert out["publicationStatus"] == "withdrawn" and out["receipt"]["action"] == "article-withdrawn"
    assert ed.public_article(session, SLUG) is None and ws.approved_workspace_view(session, SLUG) is None
    assert SLUG not in catalog_slugs(session) and world["polyglot-result"] not in listed_objects(session, article=SLUG)
    tomb = ed.public_tombstone(session, SLUG)
    assert tomb == {"schema": "sc-research-public-tombstone/1.0", "slug": SLUG, "status": "withdrawn", "title": "First",
                    "withdrawnAt": tomb["withdrawnAt"], "notice": "Withdrawn by the author."}
    assert "private reason" not in str(tomb) and OWNER not in str(tomb)
    for action in (lambda: ed.relist_article(session, OWNER_ACTOR, slug=SLUG, reason="x"),
                   lambda: ed.rollback_article(session, OWNER_ACTOR, slug=SLUG, revision=1, reason="x"),
                   lambda: ed.unlist_article(session, OWNER_ACTOR, slug=SLUG, reason="x")):
        with pytest.raises(ed.Conflict):
            action()
    write(session, world, base=1, title="After withdrawal")
    sub = ed.submit_revision(session, OWNER_ACTOR, slug=SLUG, revision=2)
    approve_article(session, sub["subjectRef"], revision=2)
    with pytest.raises(ed.Conflict, match="final"):
        ed.publish_revision(session, OWNER_ACTOR, slug=SLUG, revision=2)
    assert ed.editorial_integrity(session)["consistent"]
    receipts = ed.release_receipts(session, OWNER, SLUG)
    assert [r["action"] for r in receipts] == ["article-published", "article-withdrawn"]


def test_retraction_is_not_a_tombstone(session, world):
    live(session, world)
    ed.retract_article(session, OWNER_ACTOR, slug=SLUG, reason="silent removal")
    assert ed.public_tombstone(session, SLUG) is None and ed.public_article(session, SLUG) is None


def test_direct_database_unlisting_or_relisting_is_detected(session, world):
    live(session, world)
    art = session.get(ed.EditorialArticle, session.scalar(select(ws.ResearchEntry.id).where(ws.ResearchEntry.slug == SLUG)))
    art.publication_status = "unlisted"
    session.flush()
    assert not ed.editorial_integrity(session)["consistent"]
    art.publication_status = "published"
    ed.unlist_article(session, OWNER_ACTOR, slug=SLUG, reason="hold")
    art.publication_status = "published"
    session.flush()
    assert not ed.editorial_integrity(session)["consistent"]


# --- step-up evidence --------------------------------------------------------------------------

def stepped_record(subject, dimension, *, decider="sc-identity-oidc-session", step_up=True, age=30, auth_time=None):
    a, d = governance(subject, cv.APPROVAL_ACTIONS[dimension], approver="workspace-user:reviewer", requester=OWNER)
    unique = uuid4().hex[:12]
    a["approvalId"] = d["approvalId"] = f"approval_{unique}"
    a["decisionRef"] = d["decisionId"] = f"decision_{unique}"
    issued = datetime.now(timezone.utc)
    auth = {"requester": "sc-identity-oidc-session", "decider": decider, "stepUp": step_up,
            "deciderAuthTime": int((issued - timedelta(seconds=age)).timestamp()) if auth_time is None else auth_time}
    return sign(cv.WS_APPROVAL_RECORD_SCHEMA, {
        "schema": cv.WS_APPROVAL_RECORD_SCHEMA, "issuer": "sustainable-catalyst-workspace", "audience": "sc-research",
        "ownerScope": OWNER, "projectId": "proj-1", "dimension": dimension, "action": a["action"], "subjectRef": subject,
        "grantId": "rag_test", "authentication": auth, "approval": a, "decision": d, "issuedAt": issued.isoformat()}, KEY)


@pytest.mark.parametrize("kw", [
    {"step_up": False}, {"decider": "signed-http-only-session-cookie"}, {"age": 900}, {"age": -600}, {"auth_time": "x"},
])
def test_step_up_policy_refuses_records_without_fresh_sign_in(session, world, monkeypatch, kw):
    monkeypatch.setenv(cv.STEP_UP_POLICY_ENV, "required")
    write(session, world)
    obj = session.get(md.ResearchObject, world["polyglot-result"])
    subject = cv.subject_ref(obj, cv._provenance(session, obj))
    with pytest.raises(PermissionError, match="step-up|fresh"):
        cv.record_signed_approval(session, owner_scope=OWNER, object_id=obj.id, dimension="scientific",
                                  record=stepped_record(subject, "scientific", **kw), key=PUB)


def test_step_up_policy_accepts_fresh_records_and_is_off_by_default(session, world, monkeypatch):
    obj = session.get(md.ResearchObject, world["polyglot-result"])
    subject = cv.subject_ref(obj, cv._provenance(session, obj))
    cv.record_signed_approval(session, owner_scope=OWNER, object_id=obj.id, dimension="rights",
                              record=stepped_record(subject, "rights", step_up=False), key=PUB)
    monkeypatch.setenv(cv.STEP_UP_POLICY_ENV, "required")
    cv.record_signed_approval(session, owner_scope=OWNER, object_id=obj.id, dimension="scientific",
                              record=stepped_record(subject, "scientific"), key=PUB)
    assert cv.approval_state(session, obj, signed_only=True)["scientific"]


@pytest.mark.parametrize("env, policy, expected", [
    ("staging", None, "off"), ("staging", "required", "required"), ("production", None, "required"),
    ("production", "off", "off"), ("production", "requird", "required"), ("staging", "yes", "required"),
])
def test_step_up_policy_fails_closed_in_production(monkeypatch, env, policy, expected):
    monkeypatch.setenv("RESEARCH_ENV", env)
    if policy is None:
        monkeypatch.delenv(cv.STEP_UP_POLICY_ENV, raising=False)
    else:
        monkeypatch.setenv(cv.STEP_UP_POLICY_ENV, policy)
    assert cv.step_up_policy() == expected


def test_step_up_policy_also_governs_area_publication(session, world, monkeypatch):
    monkeypatch.setenv(cv.STEP_UP_POLICY_ENV, "required")
    with pytest.raises(PermissionError, match="step-up"):
        publish_area(session)  # an area approval decided without a fresh sign-in
    assert ed._area(session, AREA).visibility != md.APPROVED


# --- HTTP ----------------------------------------------------------------------------------------

def test_http_lifecycle_routes_and_public_responses(monkeypatch, tmp_path, session, world):
    from fastapi.testclient import TestClient
    from app.main import app
    live(session, world)
    session.commit()
    monkeypatch.setattr(ws, "_session_factory", lambda: session)
    token = tmp_path / "t"
    token.write_text("staging-secret")
    for k, v in {"RESEARCH_CONVERGENCE_TOKEN_FILE": str(token), "RESEARCH_CONVERGENCE_WRITES": "staging-service-token",
                 "RESEARCH_ENV": "staging"}.items():
        monkeypatch.setenv(k, v)
    client = TestClient(app)
    owner = {"Authorization": "Bearer staging-secret", "X-SC-Owner-Scope": OWNER}
    base = "/api/research/v1"
    r = client.get(f"{base}/articles/{SLUG}/content")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store" and "x-robots-tag" not in r.headers
    assert client.post(f"{base}/editorial/articles/{SLUG}/unlist", headers={**owner, "X-SC-Owner-Scope": "workspace-user:b"},
                       json={"reason": "x"}).status_code == 404
    assert client.post(f"{base}/editorial/articles/{SLUG}/unlist", headers=owner, json={"reason": "hold"}).status_code == 200
    r = client.get(f"{base}/articles/{SLUG}/content")
    assert r.status_code == 200 and r.headers["x-robots-tag"] == "noindex" and r.json()["listed"] is False
    assert SLUG not in {a["slug"] for a in client.get(f"{base}/articles").json()["items"]}
    assert client.post(f"{base}/editorial/articles/{SLUG}/relist", headers=owner, json={"reason": "ok"}).status_code == 200
    r = client.post(f"{base}/editorial/articles/{SLUG}/withdraw", headers=owner,
                    json={"reason": "drill", "publicNotice": "Withdrawn by the author."})
    assert r.status_code == 200 and r.json()["receipt"]["action"] == "article-withdrawn"
    r = client.get(f"{base}/articles/{SLUG}/content")
    assert r.status_code == 410 and r.json()["notice"] == "Withdrawn by the author." and r.headers["cache-control"] == "no-store"
    assert "sections" not in r.json()
    receipts = client.get(f"{base}/editorial/articles/{SLUG}/receipts", headers=owner).json()["items"]
    assert [x["action"] for x in receipts] == ["article-published", "article-unlisted", "article-relisted", "article-withdrawn"]
    assert client.get(f"{base}/articles/no-such-article/content").status_code == 404
