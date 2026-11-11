import os
from pathlib import Path
from alembic import context
from sqlalchemy import create_engine, pool
from app.workspaces import Base
from app import object_registry
config = context.config
target_metadata = Base.metadata
url = (Path(os.environ["RESEARCH_DATABASE_URL_FILE"]).read_text().strip()
       if os.getenv("RESEARCH_DATABASE_URL_FILE") else os.environ["RESEARCH_DATABASE_URL"])
if context.is_offline_mode():
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            if connection.dialect.name == "postgresql":
                connection.exec_driver_sql("SELECT pg_advisory_xact_lock(330301)")
            context.run_migrations()
