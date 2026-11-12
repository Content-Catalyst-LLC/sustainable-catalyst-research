"""SC-MODEL-01: append-only Workspace export receipts and withdrawals."""
from alembic import op
import sqlalchemy as sa
revision = '0007_workspace_execution_ingest'
down_revision = '0006_convergence_provenance'
branch_labels = None
depends_on = None
TABLES = ('research_workspace_exports', 'research_workspace_withdrawals')


def upgrade():
    op.create_table('research_workspace_exports',
        sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('owner_scope',sa.String(160),nullable=False,index=True),
        sa.Column('grant_id',sa.String(96),nullable=False),
        sa.Column('project_id',sa.String(160),nullable=False),
        sa.Column('run_id',sa.String(96),nullable=False,index=True),
        sa.Column('export_sha256',sa.String(64),nullable=False,unique=True),
        sa.Column('verification_status',sa.String(48),nullable=False),
        sa.Column('computation_object_id',sa.String(36),sa.ForeignKey('research_metadata_objects.id'),nullable=False),
        sa.Column('record',sa.JSON(),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True)))
    op.create_table('research_workspace_withdrawals',
        sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('owner_scope',sa.String(160),nullable=False),
        sa.Column('project_id',sa.String(160),nullable=False),
        sa.Column('run_id',sa.String(96),nullable=False),
        sa.Column('notice_sha256',sa.String(64),nullable=False,unique=True),
        sa.Column('revoked_object_ids',sa.JSON(),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True)),
        sa.UniqueConstraint('owner_scope','run_id',name='uq_research_workspace_withdrawal'))
    if op.get_bind().dialect.name != 'postgresql': return
    for table in TABLES:
        # research_deny_record_change() is created by 0006.
        op.execute(f'''CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE
          ON {table} FOR EACH ROW EXECUTE FUNCTION research_deny_record_change()''')


def downgrade():
    if op.get_bind().dialect.name == 'postgresql':
        for table in TABLES:
            op.execute(f'DROP TRIGGER {table}_immutable ON {table}')
    op.drop_table('research_workspace_withdrawals')
    op.drop_table('research_workspace_exports')
