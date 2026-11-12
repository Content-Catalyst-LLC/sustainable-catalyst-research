"""SC-FIG-04.1: append-only record of Workspace-signed source rights notices Research applied."""
from alembic import op
import sqlalchemy as sa
revision = '0011_source_rights_notices'
down_revision = '0010_figure_releases'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('research_workspace_source_rights',
        sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('owner_scope',sa.String(160),nullable=False,index=True),
        sa.Column('project_id',sa.String(160),nullable=False),
        sa.Column('dataset_id',sa.String(160),nullable=False),
        sa.Column('source_sha256',sa.String(64),nullable=False,index=True),
        sa.Column('licence_status',sa.String(32),nullable=False),
        sa.Column('sequence',sa.Integer(),nullable=False),
        sa.Column('notice_id',sa.String(96),nullable=False,unique=True),
        sa.Column('notice_sha256',sa.String(64),nullable=False,unique=True),
        sa.Column('effective_at',sa.String(64),nullable=False),
        sa.Column('revoked_object_ids',sa.JSON(),nullable=False),
        sa.Column('figure_bytes_removed',sa.JSON(),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),
        sa.UniqueConstraint('owner_scope','source_sha256','sequence',name='uq_research_source_rights_sequence'))
    if op.get_bind().dialect.name == 'postgresql':
        # research_deny_record_change() is created by 0006.
        op.execute('''CREATE TRIGGER research_workspace_source_rights_immutable BEFORE UPDATE OR DELETE
          ON research_workspace_source_rights FOR EACH ROW EXECUTE FUNCTION research_deny_record_change()''')


def downgrade():
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('DROP TRIGGER research_workspace_source_rights_immutable ON research_workspace_source_rights')
    op.drop_table('research_workspace_source_rights')
