# RH-02 — Repository initialization and local acceptance

**Repository:** https://github.com/Content-Catalyst-LLC/sustainable-catalyst-research (canonical public Research application repository)  
**State:** Sanitized public-source candidate; independent production operations remain private.

Application code is developed in GitHub and public-safe source status is maintained in `BUILD_MAP.md`. Internal environment/release documents are held in separate private operational repositories.

## Isolated local/Pi build

```bash
git clone https://github.com/Content-Catalyst-LLC/sustainable-catalyst-research.git
cd sustainable-catalyst-research
cp infrastructure/dev.env.example infrastructure/.env
# Set RESEARCH_DEV_PASSWORD in infrastructure/.env to a freshly generated value.
# Never use an existing production, staging, or Workspace password.
docker compose -f infrastructure/compose.dev.yml up --build
```

Check `http://localhost:18881/api/research/v1/health` and `http://localhost:18880/` on the machine running Docker. Remote Pi usage requires an authorized SSH tunnel, not an assumed public port.

Tests can run without Docker:

```bash
python -m pip install -r research-api/requirements.txt -r tests/requirements.txt
python -m pytest tests -q
```

The local service intentionally serves no actual research articles yet. Public article import and live product data adapters require subsequent authorization, source reconciliation and publication gates.

**Local secrets:** `infrastructure/.env` is Git-ignored. The development stack now requires an explicit password instead of providing a shared default.

## Boundaries

- No production mounts, passwords, credentials or existing-service migrations.
- No protected Workspace identifiers or private datasets in the public catalog.
- Research article workspaces are logical project records, not separate Docker containers.
- Merging and Contabo cutover remain separate acceptance decisions.
