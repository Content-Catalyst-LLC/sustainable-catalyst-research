"""Sustainable Catalyst Research: read-only public catalog foundation."""
import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

app = FastAPI(title="Sustainable Catalyst Research", version="0.1.0", docs_url=None, redoc_url=None)
CATALOG_FILE = Path(__file__).with_name("public-catalog.json")
PREFIX = "/api/research/v1"
# Readiness requires exactly this Alembic head (tests assert it matches the migrations).
SCHEMA_HEAD = "0013_discovery_intake"
AREA_FIELDS = ("slug", "title", "summary")
ARTICLE_FIELDS = ("slug", "area_slug", "title", "summary", "version", "state")


def public_catalog():
    # With a Research-owned DB configured, entry workspaces are the source of
    # truth; the static public JSON exists only for initial no-DB previews.
    persistent = research_workspaces.persisted_public_catalog()
    if persistent is not None:
        return persistent
    data = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    if data.get("schema") != "sc-research-public-catalog/1.0":
        raise RuntimeError("Invalid public research catalog")
    areas = [
        {key: row[key] for key in AREA_FIELDS}
        for row in data.get("areas", [])
        if isinstance(row, dict) and row.get("visibility") == "public-approved"
        and all(isinstance(row.get(key), str) for key in AREA_FIELDS)
    ]
    allowed = {row["slug"] for row in areas}
    articles = [
        {key: row[key] for key in ARTICLE_FIELDS}
        for row in data.get("articles", [])
        if isinstance(row, dict) and row.get("visibility") == "public-approved"
        and row.get("rights_cleared") is True
        and row.get("area_slug") in allowed
        and row.get("state") in ("active-research", "published", "updated", "superseded")
        and all(isinstance(row.get(key), str) for key in ARTICLE_FIELDS)
    ]
    return areas, articles


@app.get(PREFIX + "/health")
def health():
    return {"ok": True, "service": "research-api"}


@app.exception_handler(SQLAlchemyError)
async def storage_error(request, exc):
    # SQLAlchemy error strings can contain source data and connection details.
    return JSONResponse(status_code=503, content={"detail": "Research storage unavailable"},
                        headers={"Cache-Control": "no-store", "Retry-After": "5"})


@app.middleware("http")
async def public_response_headers(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.get(PREFIX + "/ready")
def ready():
    try:
        if research_workspaces._engine is None:
            raise RuntimeError("unconfigured")
        with research_workspaces._engine.connect() as connection:
            version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
            if version != SCHEMA_HEAD:
                raise RuntimeError("migration required")
            connection.execute(text("SELECT id FROM research_metadata_objects LIMIT 0"))
    except (SQLAlchemyError, RuntimeError):
        raise HTTPException(status_code=503, detail="Research storage not ready") from None
    return {"ok": True, "storage": "ready"}


@app.get(PREFIX + "/public/status")
def public_status():
    """Confirm that a real persistent Research database backs the public catalog.

    Never identify an unconfigured JSON preview as a live backend. Do not
    reveal owner IDs, manuscript paths, source files, or server credentials.
    """
    ready()
    catalog = research_workspaces.persisted_public_catalog()
    if catalog is None:
        raise HTTPException(status_code=503, detail="Persistent Research catalog not configured")
    areas, articles = catalog
    return {
        "schema": "sc-research-public-backend-status/1.0",
        "source": "research-postgresql",
        "storage": "ready",
        "approved_areas": len(areas),
        "approved_articles": len(articles),
    }


@app.get(PREFIX + "/areas")
def areas():
    return {"items": public_catalog()[0]}


@app.get(PREFIX + "/articles")
def articles(area: str = "", q: str = ""):
    results = public_catalog()[1]
    if area:
        results = [row for row in results if row["area_slug"] == area]
    if q:
        needle = q.casefold()[:120]
        results = [row for row in results if needle in (row["title"] + " " + row["summary"]).casefold()]
    return {"items": results}


@app.get(PREFIX + "/articles/{slug}")
def article(slug: str):
    for row in public_catalog()[1]:
        if row["slug"] == slug:
            return row
    raise HTTPException(status_code=404, detail="Not found")


@app.get(PREFIX + "/relationships")
def relationships():
    areas, articles = public_catalog()
    return {
        "nodes": [{"id": "area:" + a["slug"], "label": a["title"]} for a in areas]
        + [{"id": "article:" + a["slug"], "label": a["title"]} for a in articles],
        "edges": [{"source": "area:" + a["area_slug"], "target": "article:" + a["slug"], "relation": "contains"} for a in articles],
        "interpretation": "Structural research relationships, not evidence of causality."
    }

# RH-02: Per-entry workspace persistence is a separate Research-owned database.
# No editor/write routes are public; source product authentication is not reused
# until the identity bridge is independently certified.
from . import workspaces as research_workspaces
# Import before dev-only create_all so Research object metadata tables register
# in the same independent PostgreSQL schema.
from . import object_registry as research_objects
# SC-MODEL-01: provenance/approval tables extend the same registry; writes stay
# disabled unless an isolated staging deployment enables its service credential.
from . import convergence as research_convergence
from . import workspace_ingest as research_workspace_ingest
# SC-ED-01: editorial publishing over the same registry; writes share the convergence gate.
from . import editorial as research_editorial

app.include_router(research_workspaces.router)
app.include_router(research_convergence.router)
app.include_router(research_workspace_ingest.router)
app.include_router(research_objects.router)
app.include_router(research_editorial.router)

_db_url = os.getenv("RESEARCH_DATABASE_URL")
if os.getenv("RESEARCH_DATABASE_URL_FILE"):
    _db_url = Path(os.environ["RESEARCH_DATABASE_URL_FILE"]).read_text().strip()
if _db_url:
    research_workspaces.configure_engine(_db_url)
    if os.getenv("RESEARCH_DEV_AUTO_CREATE_SCHEMA") == "1":
        research_workspaces.bootstrap_dev_schema()

# RH-08D-01: Public, disabled-by-default discovery contracts.
# Scholarly providers and private saved runs remain inactive until gated builds.
from . import discovery as research_discovery

app.include_router(research_discovery.router)

# SC-FIG-02B: read-only public figures. Off unless a release channel and store are configured;
# serves only figure revisions already approved at object, article and area level.
from . import public_figures as research_public_figures

app.include_router(research_public_figures.router)
