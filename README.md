# Sustainable Catalyst Research

Python-based research knowledge platform supporting independent research workspaces, versioned metadata, evidence provenance, scientific Discovery contracts, and reviewed knowledge relationships.

## Source

- [Research API](research-api/) — FastAPI, SQLAlchemy models and database migration contracts
- [Research metadata analysis](research-graphs/) — deterministic structural relationship proposals
- [Public frontend](research-web/) — accessible Research navigation and optional read-only API bridge
- [Automated tests](tests/) — Python, JavaScript and PostgreSQL integration checks
- [Local development](docs/REPOSITORY-BOOTSTRAP.md) — isolated containers and explicit local credentials
- [Development roadmap](BUILD_MAP.md), [status](DEVELOPMENT_STATUS.md), and [public-source boundary](PUBLIC_SOURCE_SCOPE.md)

## Privacy and publishing

This source-only candidate is intended for a **new public repository with fresh Git history**. The original Sustainable Catalyst Research repository, its historical commits, internal staging and production operations, and unpublished research remain private. This snapshot does not authorize publishing private data, enabling production services, or importing confidential workspaces.

The application defaults to private research objects. Public metadata requires affirmative review and rights clearance; the optional WordPress public API bridge and scholarly provider retrieval are disabled until verified and authorized. No private Workspace credentials belong in browser code or this repository.

Local development requires a freshly generated, Git-ignored `RESEARCH_DEV_PASSWORD`. No default password is supplied.

Operational architecture, deployment records, acceptance evidence and private research mappings stay in **access-controlled private** Documentation, Data and Handoffs repositories. They are not distributed as part of public application source.

## License

No permissive software license is granted by this source candidate. Public GitHub visibility alone does not grant third-party redistribution rights. Contributions and third-party materials require a separate written rights/license decision.
