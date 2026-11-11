"""RH-05 metadata link analysis is deterministic and private by default."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research-graphs"))
from metadata_engine import (
    MetadataObject, RelationshipProposal, approved_public_projection,
    normalize_concepts, normalize_doi, propose_relationships, MAX_OBJECTS,
)


def item(authority="library", source_id="doi-source-1", source_version="v1", **kw):
    return MetadataObject(authority=authority, source_id=source_id,
                          source_version=source_version, kind="publication",
                          title="Source record", **kw)


def test_canonical_doi_forms():
    assert normalize_doi("https://doi.org/10.1000/ABC") == "10.1000/abc"
    assert normalize_doi("DOI:10.1000/ABC") == "10.1000/abc"
    assert normalize_doi("https://dx.doi.org/10.1000/ABC") == "10.1000/abc"
    assert normalize_doi("not a DOI") is None
    assert normalize_doi(None) is None


def test_normalized_concepts():
    assert normalize_concepts([" Climate   Change ", "SOIL", "soil", "X", ""]) == (
        "climate change", "soil",
    )


def test_matching_doi_is_a_candidate_not_scientific_support():
    left = item(doi="10.1000/ABC")
    right = item(authority="research-librarian", source_id="b2", doi="https://doi.org/10.1000/abc")
    result = propose_relationships([left, right])
    assert len(result) == 1
    assert result[0].predicate == "possible_same_publication"
    assert result[0].review_state == "proposed"
    assert result[0].evidence_assertion is False
    assert "verify edition" in result[0].basis.lower()


def test_two_shared_concepts_suggest_topic_connection_only():
    left = item(concepts=("ERW", "Agriculture", "Soils"))
    right = item(source_id="s2", concepts=("erw", "soils", "governance"))
    relations = propose_relationships([left, right])
    assert [r.predicate for r in relations] == ["shared_declared_topics"]
    assert "erw" in relations[0].basis.lower()


def test_one_shared_concept_does_not_link():
    left = item(concepts=("soil", "climate"))
    right = item(source_id="b", concepts=("soil", "economics"))
    assert propose_relationships([left, right]) == []


def test_explicit_dependency_has_direction():
    right = item(source_id="dataset", source_version="v2")
    left = item(source_id="figure", depends_on=(right.stable_key,))
    result = propose_relationships([left, right])
    assert len(result) == 1
    assert result[0].predicate == "declared_dependency"
    assert result[0].subject == left.fingerprint
    assert result[0].object == right.fingerprint


def test_same_title_does_not_suggest_evidence_or_identity():
    first = item()  # Both records have identical titles, but distinct source IDs.
    second = item(source_id="other")
    assert propose_relationships([first, second]) == []


def test_public_projection_excludes_private_and_unreviewed_metadata():
    approved_a = item(source_id="a", public_approved=True, rights_cleared=True)
    approved_b = item(source_id="b", public_approved=True, rights_cleared=True)
    private = item(source_id="internal-secret-id", public_approved=False, rights_cleared=True)
    no_rights = item(source_id="licensed-source", public_approved=True, rights_cleared=False)
    reviewed = RelationshipProposal(approved_a.fingerprint, approved_b.fingerprint,
                                    "related_subject", "Reviewed field links",
                                    review_state="approved-public")
    proposed = RelationshipProposal(approved_a.fingerprint, private.fingerprint,
                                    "related_subject", "Candidate only")
    embargoed = RelationshipProposal(approved_a.fingerprint, no_rights.fingerprint,
                                     "related_subject", "Source not rights cleared",
                                     review_state="approved-public")
    output = approved_public_projection(
        [approved_a, approved_b, private, no_rights], [reviewed, proposed, embargoed]
    )
    assert len(output["nodes"]) == 2
    assert len(output["edges"]) == 1
    assert output["edges"][0]["relation"] == "related_subject"
    assert "internal-secret-id" not in str(output)
    assert "licensed-source" not in str(output)
    assert "doi-source-1" not in str(output)


def test_causal_or_evidentiary_assertion_not_auto_published():
    a = item(public_approved=True, rights_cleared=True)
    b = item(source_id="b", public_approved=True, rights_cleared=True)
    claim = RelationshipProposal(a.fingerprint, b.fingerprint, "supports",
                                 "Unreviewed interpretation",
                                 review_state="approved-public", evidence_assertion=True)
    assert approved_public_projection([a, b], [claim])["edges"] == []


def test_rejects_duplicate_identity_or_overlarge_metadata_batch():
    a = item()
    with pytest.raises(ValueError, match="Duplicate"):
        propose_relationships([a, a])
    with pytest.raises(ValueError, match="exceeds limit"):
        propose_relationships([item(source_id=str(n)) for n in range(MAX_OBJECTS + 1)])


def test_stable_reproducible_output():
    a = item(doi="10.1111/first", concepts=("climate", "agriculture"))
    b = item(source_id="b", doi="10.1111/first", concepts=("climate", "agriculture"))
    first = [r.public_dict() for r in propose_relationships([a, b])]
    second = [r.public_dict() for r in propose_relationships([a, b])]
    assert first == second
    assert len(first) == 2
