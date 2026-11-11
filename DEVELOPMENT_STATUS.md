# Sustainable Catalyst Research — Development Status

The Research application includes a FastAPI backend, SQLAlchemy research models, PostgreSQL migrations, provenance-aware object metadata, graph analysis, a public frontend and test automation. See [the source roadmap](BUILD_MAP.md) for implementation locations and planned milestones.

**Scientific figures:** approved figures render on article pages through the shared Lab renderer. Only a synthetic staging channel exists; see [SC-FIG-02B](docs/SC-FIG-02B-PUBLIC-FIGURES.md).

**Deployment caution:** Source and test availability do not establish that the public website or a particular production environment is activated. The WordPress Research bridge and scholarly provider transports are disabled by default.

## Contributing

Work with the tests under `tests/` and application code under `research-api/`, `research-graphs/` and `research-web/`. Keep secrets out of source control, enforce permissions server-side, and use synthetic fixture data. Review source, license and history before any public release.

**Operational status, staging evidence and environment-specific acceptance are maintained separately in private repositories.**
