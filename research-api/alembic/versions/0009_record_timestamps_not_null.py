"""Align 0006/0007 record timestamps with the models: created_at is NOT NULL.

The models always set created_at, so existing rows should have it. If any row lacks one the upgrade stops
rather than invent a time for an immutable record; the operator must investigate that row first.
"""
from alembic import op
import sqlalchemy as sa
revision = '0009_record_timestamps_not_null'
down_revision = '0008_editorial_publishing'
branch_labels = None
depends_on = None
TABLES = ('research_output_provenance', 'research_publication_approvals', 'research_workspace_exports',
          'research_workspace_withdrawals')


def upgrade():
    bind = op.get_bind()
    for table in TABLES:
        missing = bind.execute(sa.text(f'SELECT count(*) FROM {table} WHERE created_at IS NULL')).scalar()
        if missing:
            raise RuntimeError(f'{table}: {missing} rows have no created_at; refusing to invent timestamps')
    for table in TABLES:
        with op.batch_alter_table(table) as batch:
            batch.alter_column('created_at', existing_type=sa.DateTime(timezone=True), nullable=False)


def downgrade():
    for table in TABLES:
        with op.batch_alter_table(table) as batch:
            batch.alter_column('created_at', existing_type=sa.DateTime(timezone=True), nullable=True)
