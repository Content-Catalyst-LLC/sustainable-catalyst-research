"""UCD-RESEARCH-01 publication types, origins and Studio bibliographies on editorial revisions (synthetic only)."""
import pytest

from tests.test_sc_ed01_editorial import (  # noqa: F401 (session and world are fixtures)
    OWNER, OWNER_ACTOR, SLUG, approve_article, make_publishable, publish_area, refs, session, world,
)
from app import editorial as ed  # noqa: E402

SHA_A, SHA_B = "a" * 64, "b" * 64
BIBLIOGRAPHY = {"schema": "sc-publication-bibliography/1.0", "style": "harvard", "unresolved": [], "items": [
    {"key": "beerling2020potential", "inText": "(Beerling et al., 2020)", "doi": "10.1038/s41586-020-2448-9",
     "reference": "Beerling, D.J. et al. (2020) 'Potential for large-scale CO2 removal', *Nature*, 583, pp. 242-248."}]}
SUBMISSION = {"status": "unverified", "sha256": SHA_A, "institution": "Synthetic University", "course": "SYN101",
              "assignment": "Assignment 1", "submittedOn": "2026-01-01"}


def derived(**kw):
    return {"type": "report", "bibliography": BIBLIOGRAPHY, "derivedFrom": [
        {"relation": "derived-from", "kind": "workspace-studio-revision", "workspaceProjectId": "proj-1",
         "documentId": "publication-synthetic", "revision": 2, "documentSha256": SHA_B, "label": "Studio draft"},
        {"relation": "derived-from", "kind": "academic-submission", "label": "Coursework seed",
         "submission": SUBMISSION},
        {"relation": "companion-to", "kind": "research-article", "ref": "ws-study-b", "label": "Policy brief"}], **kw}


def revise(session, world, publication, base=0, roles=("result", "study-package")):
    return ed.add_revision(session, OWNER_ACTOR, slug=SLUG, base_revision=base, title="Synthetic ERW report",
                           summary="Synthetic data only.", sections=[{"heading": "Overview", "body": "Modelled."}],
                           references=refs(session, world, *roles), publication=publication)


def test_revisions_without_a_publication_block_keep_their_document_shape(session, world):
    out = revise(session, world, None)
    assert "publication" not in ed.article_view(session, OWNER, SLUG)["head"]
    assert out["documentSha256"]


def test_publication_type_origins_and_bibliography_are_part_of_the_hashed_revision(session, world):
    first = revise(session, world, derived())
    head = ed.article_view(session, OWNER, SLUG)["head"]["publication"]
    assert head["type"] == "report" and [o["kind"] for o in head["derivedFrom"]] == [
        "workspace-studio-revision", "academic-submission", "research-article"]
    assert head["derivedFrom"][0]["documentSha256"] == SHA_B and head["bibliography"]["items"][0]["key"] == "beerling2020potential"
    second = revise(session, world, derived(type="working-paper"), base=1)
    assert second["documentSha256"] != first["documentSha256"]


@pytest.mark.parametrize("publication,problem", [
    ({"type": "essay"}, "publication.type"),
    ({"type": "report", "submission": SUBMISSION}, "Only an original-submission"),
    ({"type": "original-submission"}, "describe the submission"),
    ({"type": "original-submission", "submission": SUBMISSION,
      "derivedFrom": [{"relation": "derived-from", "kind": "research-article", "ref": "x"}]}, "itself the origin"),
    ({"type": "original-submission", "submission": {**SUBMISSION, "status": "verified"}}, "receipt"),
    ({"type": "original-submission", "submission": {**SUBMISSION, "sha256": "x"}}, "SHA-256"),
    ({"type": "original-submission", "submission": {"status": "verified", "receiptRef": "r"}}, "SHA-256"),
    ({"type": "report", "derivedFrom": [{"relation": "inspired-by", "kind": "research-article", "ref": "x"}]},
     "relation"),
    ({"type": "report", "derivedFrom": [{"relation": "derived-from", "kind": "workspace-studio-revision",
                                          "workspaceProjectId": "proj-other", "documentId": "d", "revision": 1,
                                          "documentSha256": SHA_B}]}, "different Workspace project"),
    ({"type": "report", "bibliography": {"schema": "other", "items": []}}, "bibliography"),
    ({"type": "report", "bibliography": {**BIBLIOGRAPHY, "items": BIBLIOGRAPHY["items"] * 2}}, "repeats key"),
])
def test_invalid_publication_blocks_are_refused(session, world, publication, problem):
    with pytest.raises(ValueError, match=problem):
        revise(session, world, publication)


def _publish(session, world, publication):
    revise(session, world, publication)
    make_publishable(session, world)
    sub = ed.submit_revision(session, OWNER_ACTOR, slug=SLUG, revision=1)
    publish_area(session)
    approve_article(session, sub["subjectRef"])
    return ed.publish_revision(session, OWNER_ACTOR, slug=SLUG, revision=1)


def test_an_unverified_submission_need_not_name_a_file(session, world):
    """Which candidate file was submitted is unknown until verified, so no hash is asserted for it."""
    unknown = {"status": "unverified", "institution": "Synthetic University", "assignment": "Assignment 1"}
    revise(session, world, {"type": "report", "derivedFrom": [
        {"relation": "derived-from", "kind": "academic-submission", "label": "Coursework seed", "submission": unknown}]})
    seed = ed.article_view(session, OWNER, SLUG)["head"]["publication"]["derivedFrom"][0]["submission"]
    assert seed["status"] == "unverified" and "sha256" not in seed


def test_a_typed_publication_needs_pinned_evidence_before_review(session, world):
    revise(session, world, derived(), roles=())
    with pytest.raises(PermissionError, match="Pin at least one"):
        ed.submit_revision(session, OWNER_ACTOR, slug=SLUG, revision=1)


def test_an_unverified_original_submission_is_never_released(session, world):
    with pytest.raises(PermissionError, match="verified against its receipt"):
        _publish(session, world, {"type": "original-submission", "submission": SUBMISSION})
    assert ed.public_article(session, SLUG) is None


def test_a_verified_original_submission_is_released_with_its_label(session, world):
    verified = {**SUBMISSION, "status": "verified", "receiptRef": "synthetic-receipt-1"}
    _publish(session, world, {"type": "original-submission", "submission": verified})
    pub = ed.public_article(session, SLUG)["publication"]
    assert pub["type"] == "original-submission" and pub["submission"]["status"] == "verified"
    assert "receiptRef" not in pub["submission"] and "sha256" not in pub["submission"]


def test_public_projection_shows_relations_and_bibliography_but_no_unverified_or_private_detail(session, world):
    _publish(session, world, derived())
    page = ed.public_article(session, SLUG)
    pub = page["publication"]
    assert pub["type"] == "report"
    assert pub["bibliography"][0]["reference"].startswith("Beerling") and pub["bibliography"][0]["doi"]
    seed = next(o for o in pub["derivedFrom"] if o["kind"] == "academic-submission")
    assert seed == {"relation": "derived-from", "kind": "academic-submission", "label": "Coursework seed",
                    "submissionStatus": "unverified"}
    text = str(page)
    for private in (OWNER, "proj-1", "publication-synthetic", SHA_A, SHA_B, "SYN101", "Synthetic University"):
        assert private not in text
    assert ed.editorial_integrity(session)["consistent"]


# --- SC-CITE-01: the Workspace snapshot is stored and applied, never re-formatted -----------------------------------

SNAPSHOT = {**BIBLIOGRAPHY, "markers": {"[@beerling2020potential, p. 244]": "(Beerling et al., 2020, p. 244)"},
            "items": [{**BIBLIOGRAPHY["items"][0], "library": {"recordId": "wordpress:1:post:15670", "revision": "1",
                                                               "contentSha256": "c" * 64}}]}


def test_markers_stay_editable_and_the_public_view_applies_workspace_rendering(session, world):
    body = "Potential is large [@beerling2020potential, p. 244]. Not covered [@other2021x]."
    ed.add_revision(session, OWNER_ACTOR, slug=SLUG, base_revision=0, title="Synthetic ERW report", summary="Synthetic.",
                    sections=[{"heading": "Overview", "body": body}], references=refs(session, world, "result", "study-package"),
                    publication={"type": "report", "bibliography": SNAPSHOT})
    assert ed.article_view(session, OWNER, SLUG)["head"]["sections"][0]["body"] == body  # stored as written
    make_publishable(session, world)
    sub = ed.submit_revision(session, OWNER_ACTOR, slug=SLUG, revision=1)
    publish_area(session)
    approve_article(session, sub["subjectRef"])
    ed.publish_revision(session, OWNER_ACTOR, slug=SLUG, revision=1)
    page = ed.public_article(session, SLUG)
    assert page["sections"][0]["body"] == "Potential is large (Beerling et al., 2020, p. 244). Not covered [@other2021x]."
    assert page["publication"]["bibliography"][0]["libraryRecordId"] == "wordpress:1:post:15670"


@pytest.mark.parametrize("bad,problem", [
    ({**SNAPSHOT, "markers": {"beerling": "(x)"}}, "not a citation marker"),
    ({**SNAPSHOT, "markers": {"[@a]": ""}}, "markers value"),
    ({**SNAPSHOT, "items": [{**BIBLIOGRAPHY["items"][0], "library": {"recordId": "r", "contentSha256": "x"}}]}, "SHA-256"),
])
def test_malformed_snapshots_are_refused(session, world, bad, problem):
    with pytest.raises(ValueError, match=problem):
        revise(session, world, {"type": "report", "bibliography": bad})


def test_apply_citations_formats_nothing_itself():
    assert ed.apply_citations("x [@a] y", None) == "x [@a] y"
    assert ed.apply_citations("x [@a] [@a]", {"markers": {"[@a]": "(A, 2020)"}}) == "x (A, 2020) (A, 2020)"
