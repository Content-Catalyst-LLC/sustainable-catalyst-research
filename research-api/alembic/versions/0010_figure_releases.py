"""SC-FIG-03A: immutable figure release records (exact bytes Research released for an article revision)."""
from alembic import op
import sqlalchemy as sa
revision = '0010_figure_releases'
down_revision = '0009_record_timestamps_not_null'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('research_figure_releases',
        sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('object_id',sa.String(36),sa.ForeignKey('research_metadata_objects.id'),nullable=False,index=True),
        sa.Column('entry_id',sa.String(36),sa.ForeignKey('research_entries.id'),nullable=False,index=True),
        sa.Column('revision_number',sa.Integer(),nullable=False),
        sa.Column('bundle_sha256',sa.String(64),nullable=False,index=True),
        sa.Column('provenance_sha256',sa.String(64),nullable=False),
        sa.Column('record',sa.JSON(),nullable=False),
        sa.Column('record_sha256',sa.String(64),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),
        sa.UniqueConstraint('object_id','entry_id','revision_number',name='uq_research_figure_release'))
    if op.get_bind().dialect.name == 'postgresql':
        # research_deny_record_change() is created by 0006.
        op.execute('''CREATE TRIGGER research_figure_releases_immutable BEFORE UPDATE OR DELETE
          ON research_figure_releases FOR EACH ROW EXECUTE FUNCTION research_deny_record_change()''')


def downgrade():
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('DROP TRIGGER research_figure_releases_immutable ON research_figure_releases')
    op.drop_table('research_figure_releases')
