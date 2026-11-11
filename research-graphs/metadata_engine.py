"""RH-05 — Deterministic metadata-only relationship proposals.

This is a *pure* analyzer over already-authorized research-object metadata.
It never downloads source documents, modifies another product, asserts evidence,
or publishes candidate links. Proposals always require review.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Iterable
from urllib.parse import unquote

MAX_OBJECTS = 500
DOI_PATTERN = re.compile(r"^10\.\d{4,9}/[^\s?#]{1,180}$", re.I)
CONCEPT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9 ._/-]{1,79}$")


def normalize_doi(value: str | None) -> str | None:
    """Normalize well-formed DOI metadata without title-based identity guesses."""
    if not isinstance(value, str) or not value.strip():
        return None
    doi = value.strip()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/",
                   "doi:", "DOI:"):
        if doi.lower().startswith(prefix.lower()):
            doi = doi[len(prefix):]
            break
    doi = unquote(doi).lower().strip()
    return doi if DOI_PATTERN.fullmatch(doi) else None


def normalize_concepts(values: Iterable[str] | None) -> tuple[str, ...]:
    """Index only declared topic metadata; never derive it from private text."""
    if values is None:
        return ()
    result = set()
    for value in values:
        if not isinstance(value, str):
            continue
        normalized = " ".join(value.casefold().split())
        if CONCEPT_PATTERN.fullmatch(normalized):
            result.add(normalized)
    return tuple(sorted(result))[:32]


@dataclass(frozen=True)
class MetadataObject:
    authority: str
    source_id: str
    source_version: str
    kind: str
    title: str
    doi: str | None = None
    concepts: tuple[str, ...] = ()
    # Explicit source authority references are never guessed by matching titles.
    depends_on: tuple[tuple[str, str, str], ...] = ()
    source_sha256: str | None = None
    metadata_retrieved_at: str | None = None
    license_ref: str | None = None
    rights_cleared: bool = False
    public_approved: bool = False

    @property
    def stable_key(self) -> tuple[str, str, str]:
        return (self.authority, self.source_id, self.source_version)

    @property
    def fingerprint(self) -> str:
        joined = "\x1f".join(self.stable_key)
        return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class RelationshipProposal:
    subject: str
    object: str
    predicate: str
    basis: str
    review_state: str = "proposed"
    evidence_assertion: bool = False

    def public_dict(self) -> dict:
        """Only a proposed structural/metadata relation; no original IDs/paths."""
        return {
            "subject": self.subject, "object": self.object,
            "predicate": self.predicate, "basis": self.basis,
            "review_state": self.review_state,
            "evidence_assertion": self.evidence_assertion,
        }


def propose_relationships(records: Iterable[MetadataObject]) -> list[RelationshipProposal]:
    """Suggest connections; do NOT infer support, contradiction or causation.

    Caller must already enforce owner scope on input records. Input must be
    a bounded, source-authorized metadata collection; this never queries
    Library or Workspace. All outputs remain **private review candidates**
    unless independent publishing approval occurs.
    """
    items = list(records)
    if len(items) > MAX_OBJECTS:
        raise ValueError("Research metadata batch exceeds limit")
    if len({item.stable_key for item in items}) != len(items):
        raise ValueError("Duplicate authority/source/version identity")
    proposals: list[RelationshipProposal] = []
    for i, left in enumerate(items):
        if not left.authority or not left.source_id or not left.source_version:
            raise ValueError("Complete source authority identity required")
        for right in items[i + 1:]:
            left_key = left.fingerprint
            right_key = right.fingerprint
            left_doi = normalize_doi(left.doi)
            right_doi = normalize_doi(right.doi)
            if left_doi and left_doi == right_doi:
                proposals.append(RelationshipProposal(
                    left_key, right_key, "possible_same_publication",
                    "Matching normalized DOI; verify edition, retraction and identity.",
                ))
            if right.stable_key in left.depends_on:
                proposals.append(RelationshipProposal(
                    left_key, right_key, "declared_dependency",
                    "Explicit source dependency reference; authority and provenance require review.",
                ))
            if left.stable_key in right.depends_on:
                proposals.append(RelationshipProposal(
                    right_key, left_key, "declared_dependency",
                    "Explicit source dependency reference; authority and provenance require review.",
                ))
            shared = sorted(set(normalize_concepts(left.concepts)) &
                            set(normalize_concepts(right.concepts)))
            if len(shared) >= 2:
                proposals.append(RelationshipProposal(
                    left_key, right_key, "shared_declared_topics",
                    "Shared source-declared concepts: " + ", ".join(shared[:5]),
                ))
    return sorted(proposals, key=lambda p: (p.subject, p.object, p.predicate))


def approved_public_projection(
    records: Iterable[MetadataObject], reviewed_links: Iterable[RelationshipProposal]
) -> dict:
    """Publish reviewed, rights-cleared *metadata only*, not proposed relations."""
    approved = {
        item.fingerprint: item
        for item in records if item.public_approved and item.rights_cleared
    }
    nodes = [{
        "id": item.fingerprint,
        "kind": item.kind,
        "title": item.title,
        "authority": item.authority,
        "source_version": item.source_version,
    } for _, item in sorted(approved.items())]
    edges = [{
        "source": edge.subject,
        "target": edge.object,
        "relation": edge.predicate,
        "basis": edge.basis,
    } for edge in reviewed_links
    if edge.review_state == "approved-public"
    and edge.subject in approved and edge.object in approved
    and not edge.evidence_assertion]
    return {
        "schema": "sc-research-metadata-graph/1.0",
        "nodes": nodes,
        "edges": sorted(edges, key=lambda e: (e["source"], e["target"], e["relation"])),
        "interpretation": "Structural and reviewed metadata links; not scientific validation.",
    }
