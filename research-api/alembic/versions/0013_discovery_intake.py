"""RH-08D-04: governed discovery intake (saved candidates, provenance, associations, Library proposals, audit).

Provenance rows and audit events are append-only in PostgreSQL. Nothing here is populated unless the discovery intake
gate is enabled.
"""
from alembic import op
import sqlalchemy as sa
revision = '0013_discovery_intake'
down_revision = '0012_source_rights_sync'
branch_labels = None
depends_on = None
IMMUTABLE = ('research_discovery_candidate_sources', 'research_discovery_library_proposals',
             'research_discovery_intake_events')


def upgrade():
    op.create_table('research_discovery_saved_candidates',
        sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('owner_scope',sa.String(160),nullable=False,index=True),
        sa.Column('dedup_key',sa.String(80),nullable=False),
        sa.Column('title_key',sa.String(64),nullable=False,index=True),
        sa.Column('candidate_id',sa.String(29),nullable=False),
        sa.Column('doi',sa.String(255)),
        sa.Column('title',sa.Text(),nullable=False),
        sa.Column('metadata',sa.JSON(),nullable=False),
        sa.Column('metadata_sha256',sa.String(64),nullable=False),
        sa.Column('status',sa.String(32),nullable=False),
        sa.Column('saved_by',sa.String(160),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),
        sa.UniqueConstraint('owner_scope','dedup_key',name='uq_research_discovery_saved_identity'))
    op.create_table('research_discovery_candidate_sources',
        sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('saved_id',sa.String(36),sa.ForeignKey('research_discovery_saved_candidates.id'),nullable=False,
                  index=True),
        sa.Column('provider',sa.String(32),nullable=False),
        sa.Column('provider_record_id',sa.String(255),nullable=False),
        sa.Column('retrieved_at',sa.String(40),nullable=False),
        sa.Column('query_sha256',sa.String(64),nullable=False),
        sa.Column('request_sha256',sa.String(64),nullable=False),
        sa.Column('response_sha256',sa.String(64),nullable=False),
        sa.Column('record_sha256',sa.String(64),nullable=False),
        sa.Column('adapter_version',sa.String(32),nullable=False),
        sa.Column('metadata_sha256',sa.String(64),nullable=False),
        sa.Column('metadata',sa.JSON(),nullable=False),
        sa.Column('saved_by',sa.String(160),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),
        sa.UniqueConstraint('saved_id','record_sha256',name='uq_research_discovery_source_record'))
    op.create_table('research_discovery_associations',
        sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('saved_id',sa.String(36),sa.ForeignKey('research_discovery_saved_candidates.id'),nullable=False,
                  index=True),
        sa.Column('entry_id',sa.String(36),sa.ForeignKey('research_entries.id'),nullable=False,index=True),
        sa.Column('owner_scope',sa.String(160),nullable=False),
        sa.Column('relation',sa.String(32),nullable=False),
        sa.Column('active',sa.Boolean(),nullable=False),
        sa.Column('changed_by',sa.String(160),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),
        sa.UniqueConstraint('saved_id','entry_id',name='uq_research_discovery_association'))
    op.create_table('research_discovery_library_proposals',
        sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('saved_id',sa.String(36),sa.ForeignKey('research_discovery_saved_candidates.id'),nullable=False,
                  unique=True),
        sa.Column('owner_scope',sa.String(160),nullable=False,index=True),
        sa.Column('proposal',sa.JSON(),nullable=False),
        sa.Column('proposal_sha256',sa.String(64),nullable=False),
        sa.Column('acknowledged_issues',sa.JSON(),nullable=False),
        sa.Column('status',sa.String(32),nullable=False),
        sa.Column('sent',sa.Boolean(),nullable=False),
        sa.Column('proposed_by',sa.String(160),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()))
    op.create_table('research_discovery_intake_events',
        sa.Column('seq',sa.Integer(),primary_key=True,autoincrement=False),
        sa.Column('action',sa.String(48),nullable=False),
        sa.Column('owner_scope',sa.String(160),nullable=False,index=True),
        sa.Column('saved_id',sa.String(36),index=True),
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
    for table in ('research_discovery_intake_events', 'research_discovery_library_proposals',
                  'research_discovery_associations', 'research_discovery_candidate_sources',
                  'research_discovery_saved_candidates'):
        op.drop_table(table)
