"""RH-PUB-01: an original manuscript registered as a private, citable source object from a Workspace-signed record.
Research keeps the record as private provenance, never the bytes; anonymous readers see only the allow-listed
projection after the source's own approvals release it (synthetic fixtures only; public CI)."""
from datetime import datetime, timezone

import pytest

from tests.test_sc_ed01_editorial import (  # noqa: F401 (session and world are fixtures)
    AREA, OWNER, OWNER_ACTOR, SLUG, approve, approve_article, publish_area, session, world,
)
from tests.test_sc_model01_workspace_ingest import KEY, OTHER, PUB, sign
from app import convergence as cv  # noqa: E402
from app import editorial as ed  # noqa: E402
from app import object_registry as md  # noqa: E402

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
ORIGINAL_SHA, STUDIO_SHA = "1" * 64, "2" * 64
RIGHTS = {"basis": "author-owned", "holder": "Synthetic Author",
          "statement": "Written by the owner; no third-party text, data or images are reproduced."}
PRIVATE = ("original-synthetic.docx", "artifact-synthetic-1", "proj-1", RIGHTS["statement"], "Synthetic Author")


def source_record(owner=OWNER, project="proj-1", key=KEY, **over):
    body = {"schema": ed.SOURCE_SCHEMA, "issuer": "sustainable-catalyst-workspace", "audience": "sc-research",
            "ownerScope": owner, "projectId": project, "title": "Original manuscript: Synthetic policy paper",
            "original": {"artifactId": "artifact-synthetic-1", "sha256": ORIGINAL_SHA, "byteCount": 48213,
                         "mediaType": DOCX, "filename": "original-synthetic.docx"},
            "studioRevision": {"documentId": "publication-synthetic", "revision": 3, "markdownSHA256": STUDIO_SHA},
            "rights": RIGHTS, "issuedAt": datetime.now(timezone.utc).isoformat()}
    for k, v in over.items():
        body[k] = {**body[k], **v} if isinstance(v, dict) and isinstance(body.get(k), dict) else v
    return sign(ed.SOURCE_SCHEMA, body, key)


def register(session, **kw):
    return ed.register_source(session, OWNER_ACTOR, slug=SLUG, document=source_record(**kw), key=PUB)


def test_registers_a_private_source_bound_to_the_exact_original_and_studio_revision(session, world):
    out = register(session)
    assert (out["kind"], out["role"], out["visibility"], out["created"]) == ("source", "citation", "private", True)
    obj = session.get(md.ResearchObject, out["objectId"])
    assert obj.source_sha256 == ORIGINAL_SHA and obj.workspace_id == ed._entry(session, OWNER, SLUG)[3].id
    prov = cv._provenance(session, obj).record
    assert prov["workspace"]["studioRevision"] == {"documentId": "publication-synthetic", "revision": 3,
                                                   "markdownSHA256": STUDIO_SHA}
    assert prov["rights"] == RIGHTS and prov["workspaceSourceRecord"]["signature"]["alg"] == "Ed25519"
    assert out["pin"] == ed.current_pin(session, obj)
    # Replaying a fresh signature for the same original returns the same object; nothing is rewritten.
    again = register(session)
    assert (again["objectId"], again["created"], again["pin"]) == (out["objectId"], False, out["pin"])
    events = [e for e in ed.audit_log(session, OWNER, SLUG) if e["action"] == "source-registered"]
    assert len(events) == 1 and events[0]["detail"]["sourceSha256"] == ORIGINAL_SHA


@pytest.mark.parametrize("kw,error,match", [
    ({"key": OTHER}, ValueError, "trust|signature"),
    ({"owner": "workspace-user:b"}, PermissionError, "another Workspace owner"),
    ({"project": "proj-other"}, PermissionError, "different Workspace project"),
    ({"original": {"mediaType": "text/html"}}, ValueError, "Word"),
    ({"original": {"sha256": "x"}}, ValueError, "SHA-256"),
    ({"original": {"byteCount": 0}}, ValueError, "byteCount"),
    ({"studioRevision": {"revision": 0}}, ValueError, "revision"),
    ({"studioRevision": {"markdownSHA256": "nope"}}, ValueError, "SHA-256"),
    ({"rights": {"basis": "assumed"}}, ValueError, "rights.basis"),
    ({"rights": {"statement": "mine"}}, ValueError, "20 characters"),
])
def test_only_a_complete_record_signed_by_workspace_for_this_owner_and_project_registers(session, world, kw, error, match):
    with pytest.raises(error, match=match):
        register(session, **kw)
    assert not session.query(md.ResearchObject).filter_by(kind="source").count()


def test_only_the_owner_of_this_article_can_register(session, world):
    editor = ed.Actor("workspace-user:ed", "editor", OWNER, "*")
    with pytest.raises(PermissionError, match="owner"):
        ed.register_source(session, editor, slug=SLUG, document=source_record(), key=PUB)
    stranger = ed.Actor("workspace-user:b", "owner", "workspace-user:b", "*")
    with pytest.raises(LookupError):
        ed.register_source(session, stranger, slug=SLUG, document=source_record(owner="workspace-user:b"), key=PUB)


def test_the_same_original_cannot_be_claimed_by_another_studio_publication(session, world):
    register(session)
    with pytest.raises(ed.Conflict, match="another Studio publication"):
        register(session, studioRevision={"documentId": "publication-other"})


def test_a_report_citing_only_its_original_can_be_submitted_and_published_without_exposing_private_metadata(session, world):
    src = register(session)
    publication = {"type": "policy-brief", "derivedFrom": [
        {"relation": "derived-from", "kind": "workspace-studio-revision", "workspaceProjectId": "proj-1",
         "documentId": "publication-synthetic", "revision": 3, "documentSha256": STUDIO_SHA, "label": "Studio revision 3"}]}
    ed.add_revision(session, OWNER_ACTOR, slug=SLUG, base_revision=0, title="Synthetic policy paper",
                    summary="Synthetic text only.", sections=[{"heading": "Overview", "body": "Prose."}],
                    references=[{"objectId": src["objectId"], "role": "citation", "pin": src["pin"]}],
                    publication=publication)
    sub = ed.submit_revision(session, OWNER_ACTOR, slug=SLUG, revision=1)  # the pin rule is met by the original
    assert sub["references"][0]["status"] == "verified"
    publish_area(session)
    approve_article(session, sub["subjectRef"])
    with pytest.raises(PermissionError, match="not-publicly-approved"):
        ed.publish_revision(session, OWNER_ACTOR, slug=SLUG, revision=1)  # the original is still private
    approve(session, src["objectId"])
    cv.promote_public_projection(session, owner_scope=OWNER, object_id=src["objectId"], signed_only=True)
    assert ed.publish_revision(session, OWNER_ACTOR, slug=SLUG, revision=1)["state"] == "published"
    page = ed.public_article(session, SLUG)
    ref = page["references"][0]
    assert (ref["status"], ref["kind"], ref["role"]) == ("available", "source", "citation")
    obj = session.get(md.ResearchObject, src["objectId"])
    shown = md.object_public_dict(obj, SLUG, AREA)
    prov = cv.public_provenance(obj, cv._provenance(session, obj), cv.review_disclosure(session, obj))
    assert prov["artifactSha256"] == ORIGINAL_SHA and prov["verification"] == "workspace-bytes-verified"
    assert prov["limitations"] == ed.SOURCE_LIMITATION
    public_text = str(page) + str(shown) + str(prov)
    for private in PRIVATE + (OWNER, obj.source_id):
        assert private not in public_text
