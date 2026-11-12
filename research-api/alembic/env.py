import os
from pathlib import Path
from alembic import context
from sqlalchemy import create_engine, pool
from app.workspaces import Base
# Every module that declares tables on Base must be imported, or autogenerate proposes dropping them.
from app import convergence, editorial, figure_releases, object_registry, workspace_ingest  # noqa: F401
config = context.config
target_metadata = Base.metadata
# Maintained by the 0005 triggers, not by a model.
UNMODELED_TABLES = {"research_relation_history"}


def include_object(obj, name, type_, reflected, compare_to):
    return not (type_ == "table" and reflected and compare_to is None and name in UNMODELED_TABLES)


url = (Path(os.environ["RESEARCH_DATABASE_URL_FILE"]).read_text().strip()
       if os.getenv("RESEARCH_DATABASE_URL_FILE") else os.environ["RESEARCH_DATABASE_URL"])
if context.is_offline_mode():
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True, include_object=include_object)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, include_object=include_object)
        with context.begin_transaction():
            if connection.dialect.name == "postgresql":
                connection.exec_driver_sql("SELECT pg_advisory_xact_lock(330301)")
            context.run_migrations()
