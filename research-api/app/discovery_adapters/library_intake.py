"""RH-08D-02: the future Library intake proposal for a discovery candidate. Drafting only; submission is disabled.

Library owns source identity, rights and reviewed evidence. Research may only *propose* a candidate for Library
review, through a typed handoff that carries the provider metadata and provenance unchanged. The proposal lists
what a reviewer must resolve first (``blocking_issues``); it never resolves them itself. Submitting, persisting
or attaching a proposal to an article needs the RH-08D-04 gates (persistence, identity, owner isolation) and
separate owner authorisation, so ``submit`` always refuses.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .models import Candidate, CandidateAuthor, CandidateSource, FieldConflict

INTAKE_SCHEMA = "sc-research-library-intake-proposal/1.0"
LIBRARY_INTAKE_ENABLED = False
BlockingIssue = Literal["retracted", "retraction-status-unknown", "no-doi", "metadata-conflict", "possible-duplicate"]


class LibraryIntakeDisabled(RuntimeError):
    pass


class LibraryIntakeProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_: Literal["sc-research-library-intake-proposal/1.0"] = Field(default=INTAKE_SCHEMA, alias="schema")
    status: Literal["draft-not-submitted"] = "draft-not-submitted"
    library_review_required: Literal[True] = True
    evidence_status: Literal["not-evidence"] = "not-evidence"
    candidate_id: str = Field(pattern=r"^cand-[0-9a-f]{24}$")
    doi: str | None
    title: str
    authors: tuple[CandidateAuthor, ...]
    publication_year: int | None
    venue: str | None
    work_type: str | None
    retracted: bool | None
    sources: tuple[CandidateSource, ...] = Field(min_length=1)
    conflicts: tuple[FieldConflict, ...]
    possible_duplicate_of: tuple[str, ...]
    blocking_issues: tuple[BlockingIssue, ...]
    article_slug: str | None = Field(default=None, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def blocking_issues(candidate: Candidate) -> tuple[str, ...]:
    """What a Library reviewer must resolve before the source could be accepted. Unknown counts as unresolved."""
    issues = []
    if candidate.retracted is True:
        issues.append("retracted")
    elif candidate.retracted is None:
        issues.append("retraction-status-unknown")
    if not candidate.doi:
        issues.append("no-doi")
    if candidate.conflicts:
        issues.append("metadata-conflict")
    if candidate.possible_duplicate_of:
        issues.append("possible-duplicate")
    return tuple(issues)


def draft_proposal(candidate: Candidate, *, article_slug: str | None = None) -> LibraryIntakeProposal:
    """A proposal that copies the candidate's metadata and provenance without changing or completing them."""
    return LibraryIntakeProposal(
        candidate_id=candidate.candidate_id, doi=candidate.doi, title=candidate.title, authors=candidate.authors,
        publication_year=candidate.publication_year, venue=candidate.venue, work_type=candidate.work_type,
        retracted=candidate.retracted, sources=candidate.sources, conflicts=candidate.conflicts,
        possible_duplicate_of=candidate.possible_duplicate_of, blocking_issues=blocking_issues(candidate),
        article_slug=article_slug)


def submit(proposal: LibraryIntakeProposal):
    raise LibraryIntakeDisabled("Library intake is not enabled; it needs RH-08D-04 gates and owner authorisation")
