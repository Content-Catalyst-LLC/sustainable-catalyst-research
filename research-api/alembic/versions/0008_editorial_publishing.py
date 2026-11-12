"""SC-ED-01: editorial revisions, workflow pointers, area stewardship/publication and the audit chain."""
from alembic import op
import sqlalchemy as sa
revision = '0008_editorial_publishing'
down_revision = '0007_workspace_execution_ingest'
branch_labels = None
depends_on = None
IMMUTABLE = ('research_editorial_revisions', 'research_area_stewards', 'research_area_publications',
             'research_editorial_events')


def upgrade():
    op.create_table('research_editorial_articles',
        sa.Column('entry_id',sa.String(36),sa.ForeignKey('research_entries.id'),primary_key=True),
        sa.Column('head_revision',sa.Integer(),nullable=False,server_default='0'),
        sa.Column('submitted_revision',sa.Integer()),
        sa.Column('published_revision',sa.Integer()),
        sa.Column('review_status',sa.String(24),nullable=False,server_default='draft'),
        sa.Column('publication_status',sa.String(24),nullable=False,server_default='unpublished'),
        sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()))
    op.create_table('research_editorial_revisions',
        sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('entry_id',sa.String(36),sa.ForeignKey('research_entries.id'),nullable=False,index=True),
        sa.Column('revision_number',sa.Integer(),nullable=False),
        sa.Column('parent_revision',sa.Integer()),
        sa.Column('author_scope',sa.String(160),nullable=False),
        sa.Column('author_role',sa.String(16),nullable=False),
        sa.Column('change_summary',sa.Text(),nullable=False,server_default=''),
        sa.Column('document',sa.JSON(),nullable=False),
        sa.Column('document_sha256',sa.String(64),nullable=False),
        sa.Column('object_id',sa.String(36),sa.ForeignKey('research_metadata_objects.id'),nullable=False,unique=True),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),
        sa.UniqueConstraint('entry_id','revision_number',name='uq_research_editorial_revision'))
    op.create_table('research_area_stewards',
        sa.Column('area_id',sa.String(36),sa.ForeignKey('research_areas.id'),primary_key=True),
        sa.Column('steward_scope',sa.String(160),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()))
    op.create_table('research_area_publications',
        sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('area_id',sa.String(36),sa.ForeignKey('research_areas.id'),nullable=False,index=True),
        sa.Column('action',sa.String(16),nullable=False),
        sa.Column('steward_scope',sa.String(160),nullable=False),
        sa.Column('subject_ref',sa.String(240),nullable=False),
        sa.Column('workspace_approval_id',sa.String(120),unique=True),
        sa.Column('record',sa.JSON()),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()))
    op.create_table('research_editorial_events',
        sa.Column('seq',sa.Integer(),primary_key=True,autoincrement=False),
        sa.Column('action',sa.String(48),nullable=False),
        sa.Column('owner_scope',sa.String(160),nullable=False,index=True),
        sa.Column('entry_id',sa.String(36),index=True),
        sa.Column('area_id',sa.String(36)),
        sa.Column('body',sa.JSON(),nullable=False),
        sa.Column('prev_sha256',sa.String(64),nullable=False),
        sa.Column('event_sha256',sa.String(64),nullable=False,unique=True),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()))
    if op.get_bind().dialect.name != 'postgresql': return
    for table in IMMUTABLE:
        # research_deny_record_change() is created by 0006.
        op.execute(f'''CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE
          ON {table} FOR EACH ROW EXECUTE FUNCTION research_deny_record_change()''')


def downgrade():
    if op.get_bind().dialect.name == 'postgresql':
        for table in IMMUTABLE:
            op.execute(f'DROP TRIGGER {table}_immutable ON {table}')
    for table in ('research_editorial_events', 'research_area_publications', 'research_area_stewards',
                  'research_editorial_revisions', 'research_editorial_articles'):
        op.drop_table(table)
