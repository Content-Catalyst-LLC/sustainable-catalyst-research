"""SC-RIGHTS-02: figure source index, rights confirmations and the append-only attestation log.

Backfills the sources of figures already released, so the freshness hold applies to them at once.
"""
import json
import uuid

from alembic import op
import sqlalchemy as sa
revision = '0012_source_rights_sync'
down_revision = '0011_source_rights_notices'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('research_figure_rights_sources',
        sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('object_id',sa.String(36),sa.ForeignKey('research_metadata_objects.id'),nullable=False,index=True),
        sa.Column('owner_scope',sa.String(160),nullable=False),
        sa.Column('source_sha256',sa.String(64),nullable=False,index=True),
        sa.UniqueConstraint('object_id','source_sha256',name='uq_research_figure_rights_source'))
    op.create_table('research_source_rights_confirmations',
        sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('owner_scope',sa.String(160),nullable=False),
        sa.Column('source_sha256',sa.String(64),nullable=False),
        sa.Column('status',sa.String(32),nullable=False),
        sa.Column('attested_sequence',sa.Integer(),nullable=False),
        sa.Column('research_sequence',sa.Integer(),nullable=False),
        sa.Column('attestation_id',sa.String(96),nullable=False),
        sa.Column('attested_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('attested_epoch',sa.Integer(),nullable=False),
        sa.Column('received_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),
        sa.UniqueConstraint('owner_scope','source_sha256',name='uq_research_source_rights_confirmation'))
    op.create_table('research_source_rights_attestations',
        sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('attestation_id',sa.String(96),nullable=False,unique=True),
        sa.Column('attestation_sha256',sa.String(64),nullable=False,unique=True),
        sa.Column('owner_scope',sa.String(160),nullable=False,index=True),
        sa.Column('key_id',sa.String(32),nullable=False),
        sa.Column('issued_epoch',sa.Integer(),nullable=False),
        sa.Column('results',sa.JSON(),nullable=False),
        sa.Column('received_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()))
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.execute('''CREATE TRIGGER research_source_rights_attestations_immutable BEFORE UPDATE OR DELETE
          ON research_source_rights_attestations FOR EACH ROW EXECUTE FUNCTION research_deny_record_change()''')
    rows = bind.execute(sa.text('''
        SELECT o.id, p.record, pr.owner_scope
        FROM research_figure_releases r
        JOIN research_metadata_objects o ON o.id = r.object_id
        JOIN research_output_provenance p ON p.object_id = o.id
        JOIN research_entry_workspaces w ON w.id = o.workspace_id
        JOIN research_entries e ON e.id = w.entry_id
        JOIN research_projects pr ON pr.id = e.project_id''')).fetchall()
    seen = set()
    for object_id, record, owner in rows:
        record = json.loads(record) if isinstance(record, str) else record
        for s in ((record or {}).get('figure') or {}).get('sourceLineage') or []:
            sha = s.get('sha256')
            if sha and (object_id, sha) not in seen:
                seen.add((object_id, sha))
                bind.execute(sa.text('INSERT INTO research_figure_rights_sources (id, object_id, owner_scope, source_sha256) '
                                     'VALUES (:i, :o, :w, :s)'), {'i': str(uuid.uuid4()), 'o': object_id, 'w': owner or '', 's': sha})


def downgrade():
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('DROP TRIGGER research_source_rights_attestations_immutable ON research_source_rights_attestations')
    op.drop_table('research_source_rights_attestations')
    op.drop_table('research_source_rights_confirmations')
    op.drop_table('research_figure_rights_sources')
