"""Read-only adapter contracts. No source enabled until its auth/schema are verified.
Live OpenAPI discovery is insufficient evidence of owner permission. These are
protocols for integration, deliberately not guessed HTTP routes or login flows.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

Authority = Literal['library', 'research-librarian', 'workspace', 'reporting-studio',
                    'workbench', 'lab', 'site-intelligence', 'publications']

@dataclass(frozen=True)
class AuthorizedSourceContext:
    authority: Authority
    owner_scope: str
    credential_ref: str  # secret-store reference, never a credential value
    permission: Literal['metadata:read']
    contract_sha256: str

@dataclass(frozen=True)
class SourceMetadata:
    authority: Authority
    source_id: str
    source_version: str
    title: str
    kind: str
    retrieved_at: datetime
    source_sha256: str | None
    rights_status: Literal['cleared', 'restricted', 'unknown']
    license_ref: str | None

class ReadOnlySourceAdapter(Protocol):
    def read_metadata(self, context: AuthorizedSourceContext, source_id: str) -> SourceMetadata: ...

class UnverifiedSourceAdapter:
    """Fail closed: no network, no anonymous fallback and no content copying."""
    def __init__(self, authority: Authority):
        self.authority = authority

    def read_metadata(self, context: AuthorizedSourceContext, source_id: str) -> SourceMetadata:
        if context.authority != self.authority or not context.owner_scope or context.permission != 'metadata:read':
            raise PermissionError('Source metadata access not authorized')
        raise PermissionError('Source authentication, schema and owner grant not certified')

ADAPTERS = {name: UnverifiedSourceAdapter(name) for name in (
    'library', 'research-librarian', 'workspace', 'reporting-studio',
    'workbench', 'lab', 'site-intelligence', 'publications')}
